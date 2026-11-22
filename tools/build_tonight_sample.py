"""生成 tonight 拆批样例的探针 CSV 与契约 JSON。

运行：python3 tools/build_tonight_sample.py
晶圆半径 4、边缘带 1，ACTIVE 格为满足 x^2+y^2 <= 9 的 29 个位置，全部测满，
避免未探针格被复判为 PENDING。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sample_data"

RADIUS, EDGE = 4, 1
ACTIVE = [(x, y) for y in range(RADIUS, -RADIUS - 1, -1) for x in range(-RADIUS, RADIUS + 1)
          if x * x + y * y <= (RADIUS - EDGE) ** 2]
assert len(ACTIVE) == 29
# 缺陷簇取相邻三格，保证四邻域归并为单一簇；全部选 x^2+y^2 <= 8 的安全点
FAIL_CLUSTER = [(1, -1), (2, -1), (2, 0)]
CONFLICT_POINT = (-1, 1)
PENDING_POINT = (-2, 0)
assert all(x * x + y * y <= 8 for x, y in (*FAIL_CLUSTER, CONFLICT_POINT, PENDING_POINT))


def pass_rows(wafer_id, blocked=()):
    blocked = set(blocked)
    return [(wafer_id, x, y, 1, "PASS", 1, "") for x, y in ACTIVE
            if (x, y) not in blocked]


def fail_rows(wafer_id, note):
    return [(wafer_id, x, y, 1, "FAIL", 2, note) for x, y in FAIL_CLUSTER]


wafers = {
    # 全通过片：29 格全部 PASS
    "W-21": pass_rows("W-21"),
    "W-22": pass_rows("W-22"),
    # 可返工片：单簇三枚失败，其余 PASS
    "W-23": pass_rows("W-23", blocked=FAIL_CLUSTER) + fail_rows("W-23", "缺陷集中"),
    "W-24": pass_rows("W-24", blocked=FAIL_CLUSTER) + fail_rows("W-24", "缺陷集中"),
    # 冲突片：同测次同点 PASS/FAIL 冲突，其余测满
    "W-25": pass_rows("W-25", blocked=[*FAIL_CLUSTER, CONFLICT_POINT]) + fail_rows("W-25", "") + [
        ("W-25", *CONFLICT_POINT, 2, "PASS", 1, "机台甲 T2"),
        ("W-25", *CONFLICT_POINT, 2, "FAIL", 2, "机台乙 T2 同测次冲突"),
    ],
    # 待测片：一格缺末次结果，其余测满
    "W-26": pass_rows("W-26", blocked=[*FAIL_CLUSTER, PENDING_POINT]) + fail_rows("W-26", "") + [
        ("W-26", *PENDING_POINT, 1, "-", "", "待测，缺末次结果"),
    ],
}

lines = ["wafer,x,y,attempt,result,bin,note"]
record_count = 0
for wafer_id, rows in wafers.items():
    for row in rows:
        lines.append(",".join(str(part) for part in row))
        record_count += 1
(OUT / "tonight-probe.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

# 台账按复判后的芯粒（位置）计数；冲突点两行探针只算一枚冲突芯粒
ledger = []
for wafer_id, rows in wafers.items():
    cell_results = {}
    for _, x, y, _, result, *_ in rows:
        if result in ("PASS", "FAIL"):
            cell_results.setdefault((x, y), set()).add(result)
    measured = set(cell_results)
    passed = sum(1 for v in cell_results.values() if v == {"PASS"})
    failed = sum(1 for v in cell_results.values() if v == {"FAIL"})
    conflict = sum(1 for v in cell_results.values() if v == {"PASS", "FAIL"})
    pending = len(ACTIVE) - len(measured)
    ledger.append({"wafer": wafer_id, "valid": len(ACTIVE), "passed": passed, "failed": failed,
                   "pending": pending, "conflict": conflict})

spec = {
    "sample": "tonight",
    "label": "今晚拆批图批",
    "lot": "FT26-1122",
    "sources": [{"name": "tonight-probe.csv", "records": record_count, "notch": "S"}],
    "wafers": [{"wafer": wafer_id, "radius": RADIUS, "edge": EDGE, "notch_depth": 0,
                "notch_half_width": 0} for wafer_id in wafers],
    "ledger": ledger,
}
(OUT / "tonight.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {record_count} probe rows across {len(wafers)} wafers; ACTIVE cells per wafer: {len(ACTIVE)}")
