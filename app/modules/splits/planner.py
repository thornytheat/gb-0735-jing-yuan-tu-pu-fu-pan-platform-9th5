"""图批拆批归属引擎。

以复判后的整片（晶圆）为分配单位：
- PASS：全通过整片，可进 A 单；
- REWORK：缺陷集中、可一轮返工的整片，可进 B 单，也可留厂返修后补 A；
- CONFLICT / PENDING：冲突或待测，不能计入任何一张单。

两种取舍均为确定性分配，且同一片不会同时落到两张单。
"""

A_ORDER = "A"
B_ORDER = "B"
ORDER_DEMAND = {A_ORDER: 3, B_ORDER: 2}
STRATEGIES = ("A_FIRST", "B_FIRST")
ELIGIBLE = {"PASS", "REWORK"}
BLOCKED = {"CONFLICT", "PENDING"}
GRADE_LABEL = {"PASS": "全通过", "REWORK": "可返工", "CONFLICT": "冲突", "PENDING": "待测"}


def grade_wafer(wafer, cells, clusters):
    """按复判结果把整片归入四个交付等级。"""
    results = {cell["result"] for cell in cells.values()}
    if "CONFLICT" in results:
        return "CONFLICT"
    if "PENDING" in results:
        return "PENDING"
    if "FAIL" not in results:
        return "PASS"
    # 失败芯粒四邻域归并为单一缺陷簇，即缺陷集中、可一轮返工
    return "REWORK" if len(clusters) == 1 else "CONFLICT"


def grade_run(snapshot):
    """汇总图批内每片晶圆的等级与证据计数。"""
    grades = []
    for wafer in snapshot["audit"]["wafers"]:
        wafer_id = wafer["wafer"]
        cells = snapshot["decisions"][wafer_id]
        grade = grade_wafer(wafer_id, cells, snapshot["clusters"][wafer_id])
        counts = {"PASS": wafer["passed"], "FAIL": wafer["failed"],
                  "PENDING": wafer["pending"], "CONFLICT": wafer["conflict"]}
        grades.append({"wafer": wafer_id, "grade": grade, "grade_label": GRADE_LABEL[grade],
                       "cluster_count": len(snapshot["clusters"][wafer_id]), "counts": counts})
    return grades


def _short(orders, order):
    return max(0, ORDER_DEMAND[order] - len(orders[order]))


def _build(grades, strategy):
    orders = {A_ORDER: [], B_ORDER: []}
    held = []  # 留厂返修，暂不进任何单
    notes = {g["wafer"]: [] for g in grades}
    if strategy == "A_FIRST":
        # A 单先吃全通过片；仅留一片可返工片返修补齐缺口，其余可返工片供 B，
        # B 因被借走一片而晚交一片
        for item in grades:
            if item["grade"] == "PASS" and len(orders[A_ORDER]) < ORDER_DEMAND[A_ORDER]:
                orders[A_ORDER].append(item["wafer"])
                notes[item["wafer"]].append("全通过片直配 A 单")
        retain = _short(orders, A_ORDER)
        for item in grades:
            if item["grade"] != "REWORK" or item["wafer"] in orders[A_ORDER]:
                continue
            if retain > 0:
                held.append(item["wafer"])
                notes[item["wafer"]].append("留厂返修，补齐 A 单缺口")
                retain -= 1
            elif len(orders[B_ORDER]) < ORDER_DEMAND[B_ORDER]:
                orders[B_ORDER].append(item["wafer"])
                notes[item["wafer"]].append("缺陷集中，一轮返工后交 B 单")
    else:
        # B 单先锁全部可返工片，A 单只收全通过片，当场缺片
        for item in grades:
            if item["grade"] == "REWORK" and len(orders[B_ORDER]) < ORDER_DEMAND[B_ORDER]:
                orders[B_ORDER].append(item["wafer"])
                notes[item["wafer"]].append("优先保 B，可返工片整批入 B 单")
        for item in grades:
            if item["grade"] == "PASS" and len(orders[A_ORDER]) < ORDER_DEMAND[A_ORDER]:
                orders[A_ORDER].append(item["wafer"])
                notes[item["wafer"]].append("全通过片直配 A 单")

    blocked = [g["wafer"] for g in grades if g["grade"] in BLOCKED]
    for wafer_id in blocked:
        reason = "冲突未裁决" if next(g for g in grades if g["wafer"] == wafer_id)["grade"] == "CONFLICT" else "尚有待测芯粒"
        notes[wafer_id].append(f"{reason}，不计入任何交付单")
    assigned = set(orders[A_ORDER]) | set(orders[B_ORDER]) | set(held)
    for item in grades:
        if item["grade"] == "REWORK" and item["wafer"] not in assigned:
            notes[item["wafer"]].append("可返工产能已满，暂留厂")
    return orders, held, blocked, notes


def _validate(grades, orders, held):
    """互斥与资格校验：同一片不能同时进两张单，冲突/待测不得计入。"""
    eligible = {g["wafer"] for g in grades if g["grade"] in ELIGIBLE}
    a_set, b_set = set(orders[A_ORDER]), set(orders[B_ORDER])
    overlap = sorted(a_set & b_set)
    if overlap:
        raise ValueError(f"同一片不能同时进两张单：{'、'.join(overlap)}")
    if held and (set(held) & (a_set | b_set)):
        raise ValueError("留厂片不能同时归属交付单")
    invalid = sorted((a_set | b_set) - eligible)
    if invalid:
        raise ValueError(f"冲突或待测片不能计入交付单：{'、'.join(invalid)}")
    if any(len(orders[order]) > ORDER_DEMAND[order] for order in orders):
        raise ValueError("归属数量超过交付单需求")


def plan_strategy(grades, strategy):
    if strategy not in STRATEGIES:
        raise ValueError(f"未知取舍方案：{strategy}")
    orders, held, blocked, notes = _build(grades, strategy)
    _validate(grades, orders, held)
    lines = []
    for item in grades:
        wafer_id = item["wafer"]
        if wafer_id in orders[A_ORDER]:
            destination = "A"
        elif wafer_id in orders[B_ORDER]:
            destination = "B"
        elif wafer_id in held:
            destination = "HELD"
        else:
            destination = "NONE"
        lines.append({**item, "destination": destination, "notes": notes[wafer_id]})
    summary = {
        "strategy": strategy,
        "demand": dict(ORDER_DEMAND),
        "orders": {order: {"wafers": wafers, "assigned": len(wafers),
                           "demand": ORDER_DEMAND[order], "short": _short(orders, order)}
                   for order, wafers in orders.items()},
        "held": held,
        "blocked": blocked,
        "lines": lines,
    }
    summary["feasible"] = all(summary["orders"][o]["short"] == 0 for o in orders)
    return summary


def compare_plans(snapshot):
    grades = grade_run(snapshot)
    plans = {strategy: plan_strategy(grades, strategy) for strategy in STRATEGIES}
    inventory = {}
    for grade in (*GRADE_LABEL,):
        inventory[grade] = [g["wafer"] for g in grades if g["grade"] == grade]
    return {"grades": grades, "inventory": inventory, "plans": plans,
            "demand": dict(ORDER_DEMAND)}
