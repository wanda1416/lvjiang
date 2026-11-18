"""profile 同步链路冒烟：临时库 + 假云端，一次性跑通所有同步入口。

用途：v12 字段改名后，检查客户端整条同步链路（快照清单/上传、push、pull、
回放、周期重置、regen 物化）是否还有「列名/键名对不上」这类残留。
一次性诊断脚本，不进 tests/。
"""
from __future__ import annotations

import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from loguru import logger  # noqa: E402

logger.remove()

from lvjiang.core.profile import (  # noqa: E402
    engine,
    regen_gate,
    repository,
    service,
    snapshot_sync,
)
from lvjiang.core.profile.models import QuotaKeyDef  # noqa: E402


class FakeCloud:
    """按 worker 的真实响应形状模拟云端。"""

    def __init__(self) -> None:
        self.head = 0
        self.rows: list[dict] = []
        self.snapshots: list[dict] = []
        self.acked: dict[str, int] = {}
        self.calls: list[str] = []

    def post(self, route: str, data: dict, token: str) -> dict:
        self.calls.append(route)
        if route == "/api/sync/profile/snapshot" and not data.get("id"):
            return {"snapshots": list(reversed(self.snapshots)), "head_remote_id": self.head}
        if route == "/api/sync/profile/snapshot":
            snap = self.snapshots[data["id"] - 1]
            return dict(snap)
        if route == "/api/sync/profile/snapshot/put":
            snapshot_id = len(self.snapshots) + 1
            entries = data.get("entries") or []
            snap = {
                "id": snapshot_id,
                "snapshot_remote_id": self.head,
                "origin_instance_id": data.get("instance_id", ""),
                "origin_name": data.get("origin_name", ""),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "entry_count": entries and len(entries[0].get("items", [])) or 0,
                "entries": entries[0].get("items", []) if entries else [],
            }
            self.snapshots.append(snap)
            return {"id": snapshot_id, "snapshot_remote_id": self.head,
                    "entries": snap["entry_count"], "unchanged": False}
        if route == "/api/sync/profile/push":
            results = []
            for op in data.get("ops", []):
                op_id = op["op_id"]
                if op_id in self.acked:
                    results.append({"op_id": op_id, "remote_id": self.acked[op_id],
                                    "status": "duplicate"})
                    continue
                self.head += 1
                self.acked[op_id] = self.head
                self.rows.append({**op, "remote_id": self.head})
                results.append({"op_id": op_id, "remote_id": self.head, "status": "accepted"})
            accepted = sum(1 for r in results if r["status"] == "accepted")
            return {"accepted": accepted, "skipped": len(results) - accepted,
                    "remote_id": self.head, "results": results}
        if route == "/api/sync/profile/pull":
            since = int(data.get("since_remote_id") or 0)
            ops = [r for r in self.rows if r["remote_id"] > since]
            ops.sort(key=lambda r: r["remote_id"])
            return {"ops": ops, "last_remote_id": ops[-1]["remote_id"] if ops else since}
        if route == "/api/sync/state":
            return {"instance_id": "test-inst", "account": {"id": "acc"},
                    "protocol_version": 2, "not_inited": [], "profile_sync": {},
                    "snapshot_count": len(self.snapshots), "snapshot_head": self.head}
        raise AssertionError(f"未模拟的接口: {route}")


def step(name, fn):
    try:
        out = fn()
        print(f"[OK]   {name}: {out}")
        return True
    except Exception as error:  # noqa: BLE001
        print(f"[FAIL] {name}: {type(error).__name__}: {error}")
        traceback.print_exc(limit=3)
        return False


def main() -> int:
    tmp = Path(tempfile.mkdtemp()) / "profile.db"
    repository._DB_PATH = tmp
    repository.reset_profile_db()
    regen_gate.reset()
    regen_gate.mark_pull_completed()

    cloud = FakeCloud()
    snapshot_sync.post = cloud.post            # 假化网络层
    state: dict = {}
    snapshot_sync.profile_sync_state = lambda token=None: state
    snapshot_sync.save_profile_sync_state = lambda patch, token=None: state.update(patch)

    # 本机写入几类数据（配额 / 库存 / regen / note / 锁定）
    repository.db_upsert("tester", "quota", "daily", 5, op_type="set", source="日常")
    repository.db_upsert("tester", "stock", "jinbi", 120, op_type="set", source="补给")
    repository.db_upsert("tester", "regen", "xinli", 80, op_type="set", source="打坐")
    repository.db_upsert("tester", "note", "memo", 0, op_type="set",
                         value_text="记得交任务", source="手记")
    repository.db_set_locked("tester", "stock", "jinbi")

    ok = True
    ok &= step("快照清单（云空）", lambda: snapshot_sync.remote_snapshots("t"))
    ok &= step("首次上传基线快照", lambda: snapshot_sync.refresh_snapshot("t", allow_empty=True).__dict__)
    ok &= step("push_local", lambda: snapshot_sync.push_local("t").__dict__)
    ok &= step("再推一次（应全重复）", lambda: snapshot_sync.push_local("t").__dict__)

    # 造一条跨端 op，走 pull + 回放
    cloud.head += 1
    cloud.rows.append({
        "remote_id": cloud.head, "instance_id": "other-device", "local_id": 7,
        "username": "tester", "type": "stock", "key": "jinbi",
        "op": "set", "value": 999, "delta": None, "value_text": "",
        "source": "别处", "happened_at": datetime.now().isoformat(timespec="seconds"),
        "old_value": 120, "op_id": "cross-op-1",
    })
    ok &= step("pull_and_replay", lambda: snapshot_sync.pull_and_replay("t", allow_overwrite=True).__dict__)
    ok &= step("回放后读值", lambda: repository.db_read_entry("tester", "stock", "jinbi"))

    # 跨周期：tick 不写库，只有真实写入才物化 reset + set
    old = (datetime.now() - timedelta(days=3)).isoformat(timespec="seconds")
    repository.db_upsert("tester", "quota", "daily", 42, event_at=old, op_type="set")
    kd = QuotaKeyDef(key="daily", label="日", period="day", reset_time="05:00")
    cfg = SimpleNamespace(get_keys_by_model=lambda m: [kd] if m == "quota" else [])
    ok &= step("tick 不写 reset",
               lambda: engine.ProfileProcessor._tick_user(
                   SimpleNamespace(_quota_display={}, _notify_updated=lambda u: None),
                   "tester", cfg))
    ok &= step("跨周期读值（派生 0）",
               lambda: service.profile_read("tester", "daily"))

    ok &= step("历史查询", lambda: len(repository.db_get_history("tester")))
    ok &= step("历史聚合", lambda: len(repository.db_history_page(
        __import__("lvjiang.core.profile.history_query", fromlist=["HistoryQuery"]).HistoryQuery()
    )))
    ok &= step("同步总入口 sync_all", lambda: snapshot_sync.sync_all("t").__dict__)
    ok &= step("本机待上传行", lambda: len(repository.db_list_unacked(
        repository._author_stamp())))
    ok &= step("全量读值", lambda: len(repository.db_read_all("tester")))

    # ── 场景：同步状态丢失（退出登录 / 删 account.json）后能否「恢复」而非「重建」
    before = dict(repository.db_read_all("tester"))
    local_max = repository.db_max_remote_id()
    state.clear()                                   # 模拟 account.json 被删
    ok &= step("状态清空后 pull_and_replay", lambda: snapshot_sync.pull_and_replay("t").__dict__)
    print("     恢复后 state:", {k: state.get(k) for k in
                                 ("remote_history_id", "profile_snapshot_id", "snapshot_remote_id")})
    print("     本地已应用 remote_id:", local_max,
          "| 恢复后水位:", state.get("remote_history_id"))
    after = dict(repository.db_read_all("tester"))
    changed = [k for k in before if before[k] != after.get(k)]
    print("     条目是否被覆盖:", "无变化" if not changed else f"变了 {changed}")
    ok &= step("恢复后 push 仍可继续", lambda: snapshot_sync.push_local("t").__dict__)

    print("\n调用过的接口:", sorted(set(cloud.calls)))
    print("云端 head:", cloud.head, "| 快照数:", len(cloud.snapshots))
    print("结论:", "全部通过" if ok else "存在失败项，见上方 [FAIL]")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
