from datetime import datetime, timezone

from app.errors import ReviewError
from app.modules.splits.planner import compare_plans, plan_strategy
from app.modules.splits.store import SplitStore


class SplitService:
    def __init__(self, run_service, store: SplitStore):
        self.runs = run_service
        self.store = store

    def _snapshot(self, run_id):
        _, snapshot, _ = self.runs.store.get(run_id)
        return snapshot

    def comparison(self, run_id):
        return compare_plans(self._snapshot(run_id))

    def publish(self, run_id, strategy, simulate_failure=False):
        snapshot = self._snapshot(run_id)
        try:
            plan = plan_strategy(compare_plans(snapshot)["grades"], strategy)
        except ValueError as exc:
            raise ReviewError("BAD_STRATEGY", str(exc)) from exc
        published_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            payload, created, version = self.store.publish(
                run_id, plan, published_at, simulate_failure=simulate_failure)
        except Exception as exc:
            # 事务已回滚：旧版归属单（若有）仍是 current，原图批归档保持可查
            if simulate_failure:
                raise ReviewError("PUBLISH_INTERRUPTED", "模拟发布中断，已恢复旧版归属单", 503) from exc
            raise
        return {"order": payload, "created": created, "current": self.store.current(run_id)}

    def current(self, run_id):
        payload = self.store.current(run_id)
        if payload is None:
            raise ReviewError("SPLIT_NOT_PUBLISHED", "该图批尚未发布拆批归属单", 404)
        return payload

    def history(self, run_id):
        versions = self.store.versions(run_id)
        current = self.store.current(run_id)
        return {"run_id": run_id, "current_version": current["version"] if current else None,
                "versions": versions}

    def version(self, run_id, version):
        return self.store.get_version(run_id, version)
