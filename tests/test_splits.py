import pytest

from app.modules.intake.catalog import load_sample
from app.modules.runs.pipeline import calculate
from app.modules.splits.planner import (A_ORDER, B_ORDER, compare_plans, grade_run,
                                        plan_strategy)


@pytest.fixture(scope="module")
def snapshot():
    return calculate(load_sample("tonight"))


@pytest.fixture(scope="module")
def grades(snapshot):
    return grade_run(snapshot)


def test_wafer_grades_match_delivery_story(grades):
    by_wafer = {row["wafer"]: row["grade"] for row in grades}
    assert by_wafer == {
        "W-21": "PASS", "W-22": "PASS",
        "W-23": "REWORK", "W-24": "REWORK",
        "W-25": "CONFLICT", "W-26": "PENDING",
    }


def test_conflict_and_pending_never_enter_any_order(grades):
    for strategy in ("A_FIRST", "B_FIRST"):
        plan = plan_strategy(grades, strategy)
        eligible = set(plan["orders"][A_ORDER]["wafers"]) | set(plan["orders"][B_ORDER]["wafers"])
        assert not ({"W-25", "W-26"} & eligible)
        assert set(plan["blocked"]) == {"W-25", "W-26"}


def test_same_wafer_never_assigned_to_both_orders(grades):
    for strategy in ("A_FIRST", "B_FIRST"):
        plan = plan_strategy(grades, strategy)
        assert not (set(plan["orders"][A_ORDER]["wafers"]) & set(plan["orders"][B_ORDER]["wafers"]))
        destinations = [line["destination"] for line in plan["lines"]]
        assert destinations.count("A") + destinations.count("B") <= len(grades)


def test_a_first_holds_one_rework_and_makes_b_late(grades):
    plan = plan_strategy(grades, "A_FIRST")
    assert plan["orders"][A_ORDER]["wafers"] == ["W-21", "W-22"]
    assert plan["orders"][A_ORDER]["short"] == 1  # 留厂片返修好前 A 仍缺
    assert plan["held"] == ["W-23"]
    assert plan["orders"][B_ORDER]["wafers"] == ["W-24"]
    assert plan["orders"][B_ORDER]["short"] == 1  # B 晚交一片


def test_b_first_fills_b_and_leaves_a_short_on_site(grades):
    plan = plan_strategy(grades, "B_FIRST")
    assert plan["orders"][B_ORDER]["wafers"] == ["W-23", "W-24"]
    assert plan["orders"][B_ORDER]["short"] == 0
    assert plan["orders"][A_ORDER]["wafers"] == ["W-21", "W-22"]
    assert plan["orders"][A_ORDER]["short"] == 1
    assert plan["held"] == []


def test_neither_strategy_is_fully_feasible_tonight(grades):
    comparison = compare_plans(calculate(load_sample("tonight")))
    assert all(not plan["feasible"] for plan in comparison["plans"].values())


def test_unknown_strategy_rejected(grades):
    with pytest.raises(ValueError):
        plan_strategy(grades, "THIRD_WAY")
