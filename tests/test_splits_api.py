import sqlite3

import pytest

from app.main import create_app
from app.modules.splits.store import SplitStore


@pytest.fixture
def split_client(tmp_path):
    db = tmp_path / "split.sqlite3"
    from fastapi.testclient import TestClient
    with TestClient(create_app(db, seed=False)) as client:
        response = client.post("/api/runs", json={"sample": "tonight"})
        assert response.status_code == 201
        run_id = response.json()["manifest"]["run_id"]
        yield client, f"/api/runs/{run_id}/splits", db


def test_comparison_endpoint_lists_both_strades_and_blocked(split_client):
    client, base, _ = split_client
    data = client.get(base + "/comparison").json()
    assert set(data["plans"]) == {"A_FIRST", "B_FIRST"}
    assert data["inventory"]["CONFLICT"] == ["W-25"]
    assert data["inventory"]["PENDING"] == ["W-26"]


def test_publish_creates_versioned_order_and_tracks_shortage(split_client):
    client, base, _ = split_client
    response = client.post(base + "/publish", json={"strategy": "A_FIRST"})
    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["order"]["version"] == 1
    current = body["current"]
    assert current["plan"]["orders"]["B"]["short"] == 1
    assert current["plan"]["held"] == ["W-23"]

    history = client.get(base + "/history").json()
    assert history["current_version"] == 1
    assert history["versions"][0]["strategy"] == "A_FIRST"


def test_republish_same_plan_is_idempotent_no_second_order(split_client):
    client, base, _ = split_client
    first = client.post(base + "/publish", json={"strategy": "A_FIRST"}).json()
    second = client.post(base + "/publish", json={"strategy": "A_FIRST"}).json()
    assert first["created"] is True and second["created"] is False
    assert first["order"]["version"] == second["order"]["version"] == 1
    assert client.get(base + "/history").json()["versions"] == [
        client.get(base + "/history").json()["versions"][0]]


def test_switching_strategy_opens_new_version_but_keeps_history(split_client):
    client, base, _ = split_client
    client.post(base + "/publish", json={"strategy": "A_FIRST"})
    second = client.post(base + "/publish", json={"strategy": "B_FIRST"}).json()
    assert second["created"] is True and second["order"]["version"] == 2
    current = client.get(base + "/current").json()
    assert current["version"] == 2 and current["strategy"] == "B_FIRST"
    history = client.get(base + "/history").json()
    assert [v["version"] for v in history["versions"]] == [1, 2]
    # 旧版仍可按版本号查回
    old = client.get(base + "/versions/1").json()
    assert old["version"] == 1 and old["strategy"] == "A_FIRST"


def test_interrupted_publish_rolls_back_and_restores_previous(split_client):
    client, base, db = split_client
    client.post(base + "/publish", json={"strategy": "A_FIRST"})
    before = client.get(base + "/current").json()

    response = client.post(base + "/publish", json={"strategy": "B_FIRST", "simulate_failure": True})
    assert response.status_code == 503
    assert response.json()["code"] == "PUBLISH_INTERRUPTED"

    after = client.get(base + "/current").json()
    assert after["version"] == before["version"] == 1
    assert after["strategy"] == "A_FIRST"
    # 中断未留下半截版本
    assert [v["version"] for v in client.get(base + "/history").json()["versions"]] == [1]


def test_interrupted_first_publish_leaves_no_current(split_client):
    client, base, _ = split_client
    response = client.post(base + "/publish", json={"strategy": "A_FIRST", "simulate_failure": True})
    assert response.status_code == 503
    missing = client.get(base + "/current")
    assert missing.status_code == 404
    assert missing.json()["code"] == "SPLIT_NOT_PUBLISHED"


def test_original_run_archive_still_queryable_after_publish(split_client):
    client, base, _ = split_client
    client.post(base + "/publish", json={"strategy": "B_FIRST"})
    run_url = base.replace("/splits", "")
    manifest = client.get(run_url).json()
    assert manifest["lot"] == "FT26-1122"
    assert client.get(run_url + "/export").status_code == 200
    assert client.get(run_url + "/retest/W-25?result=CONFLICT").status_code == 200


def test_store_tables_are_separate_from_run_archive(split_client):
    client, base, db = split_client
    client.post(base + "/publish", json={"strategy": "A_FIRST"})
    with sqlite3.connect(db) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"wafer_runs", "split_runs", "split_current"} <= tables


def test_split_store_direct_rollback_on_error(tmp_path):
    store = SplitStore(tmp_path / "direct.sqlite3")
    store.initialize()
    plan = {"strategy": "A_FIRST", "orders": {"A": {"wafers": ["W-21"], "assigned": 1, "demand": 3, "short": 2},
                                               "B": {"wafers": [], "assigned": 0, "demand": 2, "short": 2}},
            "held": ["W-23"], "blocked": ["W-25", "W-26"], "lines": [], "feasible": False, "demand": {"A": 3, "B": 2}}
    with pytest.raises(sqlite3.OperationalError):
        store.publish("run-x", plan, "2026-11-22T20:00:00+00:00", simulate_failure=True)
    assert store.current("run-x") is None
    assert store.versions("run-x") == []
