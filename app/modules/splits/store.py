import json
import sqlite3
from contextlib import closing, contextmanager

from app.errors import ReviewError
from app.manifest import canonical, digest
from app.modules.splits.planner import STRATEGIES


class SplitStore:
    """拆批归属单的版本化归档。

    一次发布在单事务内写入新版本与全部归属行，最后才切换 current 指针：
    任一步骤失败（含模拟中断）整体回滚，旧版保持可读，原图批归档不受影响。
    """

    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            yield connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS split_runs (
                    run_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    strategy TEXT NOT NULL,
                    plan_hash TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, version)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS split_current (
                    run_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                )
            """)

    def current(self, run_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT version FROM split_current WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            return self._load(connection, run_id, row[0])

    def versions(self, run_id):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT version, strategy, plan_hash, published_at FROM split_runs "
                "WHERE run_id = ? ORDER BY version", (run_id,)).fetchall()
        return [{"version": v, "strategy": s, "plan_hash": h, "published_at": t} for v, s, h, t in rows]

    def get_version(self, run_id, version):
        with self.connect() as connection:
            return self._load(connection, run_id, version)

    def _load(self, connection, run_id, version):
        row = connection.execute(
            "SELECT payload FROM split_runs WHERE run_id = ? AND version = ?",
            (run_id, version)).fetchone()
        if row is None:
            raise ReviewError("SPLIT_VERSION_NOT_FOUND", "归属单版本未找到", 404)
        return json.loads(row[0])

    def publish(self, run_id, plan, published_at, simulate_failure=False):
        """发布归属单；返回 (payload, created, version)。

        同一图批同方案同内容重跑不新增第二张归属单（幂等）；
        内容变化才开新版本。simulate_failure 在切换指针前抛错以验证回滚。
        """
        if plan["strategy"] not in STRATEGIES:
            raise ReviewError("BAD_STRATEGY", "取舍方案无效")
        plan_hash = digest(canonical(plan).encode())
        with self.connect() as connection:
            previous = connection.execute(
                "SELECT version FROM split_current WHERE run_id = ?", (run_id,)).fetchone()
            existing = connection.execute(
                "SELECT version, payload FROM split_runs WHERE run_id = ? AND plan_hash = ?",
                (run_id, plan_hash)).fetchone()
            if existing is not None:
                version = existing[0]
                if previous is None or previous[0] != version:
                    connection.execute(
                        "INSERT INTO split_current VALUES (?, ?) "
                        "ON CONFLICT(run_id) DO UPDATE SET version = excluded.version",
                        (run_id, version))
                return json.loads(existing[1]), False, version

            next_version = 1 + int(connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM split_runs WHERE run_id = ?",
                (run_id,)).fetchone()[0])
            payload = {"run_id": run_id, "version": next_version, "strategy": plan["strategy"],
                       "plan_hash": plan_hash, "published_at": published_at, "plan": plan,
                       "replaces_version": previous[0] if previous else None}
            connection.execute(
                "INSERT INTO split_runs VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, next_version, plan["strategy"], plan_hash,
                 canonical(payload), published_at))
            if simulate_failure:
                raise sqlite3.OperationalError("simulated publish interruption")
            connection.execute(
                "INSERT INTO split_current VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET version = excluded.version",
                (run_id, next_version))
            old_version = previous[0] if previous else None
        return payload, True, next_version
