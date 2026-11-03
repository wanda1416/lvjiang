"""profile 账本同步：引导、增量回放、上行水位、非初始设备覆盖确认。"""

from __future__ import annotations

import json

import pytest

from lvjiang.core.profile import snapshot_sync


class FakeServer:
    """最小远端：快照列表 + 按 remote_id 的自增行 + 幂等上行。"""

    def __init__(self):
        self.snapshots: list[dict] = []
        self.rows: list[dict] = []
        self.pushed: list[dict] = []
        self.fail = False

    def post(self, route: str, data: dict, token: str) -> dict:
        if self.fail:
            raise snapshot_sync.SyncError("账号服务暂时不可用")
        if route == "/api/sync/profile/snapshot":
            if isinstance(data.get("id"), int):
                match = next((row for row in self.snapshots if row["id"] == data["id"]), None)
                return {"snapshot": match}
            return {"snapshots": [{k: v for k, v in row.items() if k != "entries"}
                                  for row in self.snapshots]}
        if route == "/api/sync/profile/pull":
            since = data.get("since_remote_id", 0)
            rows = [r for r in self.rows if r["remote_id"] > since]
            return {"ops": rows, "last_remote_id": rows[-1]["remote_id"] if rows else since}
        if route == "/api/sync/profile/push":
            before = len(self.rows)
            for op in data["ops"]:
                self.rows.append({"remote_id": len(self.rows) + 1,
                                  "username": op["username"], "type": op["type"],
                                  "key": op["key"], "op": op["op"],
                                  "value": op.get("value"), "delta": op.get("delta"),
                                  "value_text": op.get("value_text", ""),
                                  "happened_at": op["happened_at"]})
                self.pushed.append(op)
            return {"accepted": len(self.rows) - before,
                    "skipped": 0, "remote_id": len(self.rows)}
        if route == "/api/sync/profile/snapshot/put":
            snapshot = {"id": len(self.snapshots) + 1,
                        "snapshot_remote_id": len(self.rows),
                        "origin_instance_id": "mine",
                        "origin_name": data.get("origin_name", ""),
                        "created_at": "2026-11-03T10:00:00",
                        "entry_count": len(data["entries"]),
                        "entries": json.dumps(data["entries"])}
            self.snapshots.insert(0, snapshot)   # 最新在前
            return {"id": snapshot["id"], "snapshot_remote_id": snapshot["snapshot_remote_id"],
                    "entries": snapshot["entry_count"]}
        raise AssertionError(route)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    server = FakeServer()
    entries: dict[str, dict[str, dict]] = {}
    history: list[dict] = []
    state: dict = {}
    writes: list[tuple] = []

    def db_upsert(username, type_, key, value, updated_at=None, change_type=None,
                  delta_value=None, source="", value_text="", sync_from=None):
        writes.append((username, type_, key, value, value_text, change_type))
        entries.setdefault(username, {}).setdefault(type_, {})[key] = {
            "value": value, "value_text": value_text,
            "updated_at": updated_at or "2026-11-03T10:00:00"}

    monkeypatch.setattr(snapshot_sync, "post", server.post)
    monkeypatch.setattr(snapshot_sync, "profile_sync_state", lambda: dict(state))
    monkeypatch.setattr(snapshot_sync, "save_profile_sync_state",
                        lambda value: state.update(value))
    monkeypatch.setattr(snapshot_sync.repository, "db_upsert", db_upsert)
    monkeypatch.setattr(snapshot_sync.repository, "db_read_all",
                        lambda username: entries.get(username, {}))
    monkeypatch.setattr(snapshot_sync.repository, "db_read_entry",
                        lambda u, t, k: entries.get(u, {}).get(t, {}).get(k, {}))
    monkeypatch.setattr(snapshot_sync.repository, "db_count_history",
                        lambda username: len([r for r in history if r["username"] == username]))
    monkeypatch.setattr(snapshot_sync.repository, "db_get_history_since",
                        lambda since, limit: [r for r in history if r["id"] > since][:limit])
    monkeypatch.setattr(snapshot_sync, "current_instance_id", lambda: "mine")
    monkeypatch.setattr(snapshot_sync, "backup_local", lambda: str(tmp_path / "backup"))
    return {"server": server, "entries": entries, "history": history,
            "state": state, "writes": writes}


def test_bootstrap_applies_snapshot_and_sets_watermarks(env) -> None:
    env["server"].rows = [{"remote_id": 1}, {"remote_id": 2}]
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 2, "origin_instance_id": "other",
        "origin_name": "书房PC", "created_at": "2026-11-03T10:00:00", "entry_count": 2,
        "entries": json.dumps([
            {"username": "蔡元君", "type": "stock", "key": "energy", "value": 20,
             "value_text": "", "updated_at": "2026-11-03T10:00:00"},
            {"username": "楚湘菱", "type": "stock", "key": "energy", "value": 8,
             "value_text": "", "updated_at": "2026-11-03T10:00:00"}])}]
    report = snapshot_sync.pull_and_replay("t", allow_overwrite=True)
    assert report.snapshots == 1
    # 一份快照含全部用户，落地时按条目里的 username 归属
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 20
    assert env["entries"]["楚湘菱"]["stock"]["energy"]["value"] == 8
    assert env["state"]["remote_history_id"] == 2
    assert env["state"]["profile_snapshot_id"] == 1
    # 回放不写本地 history（因此无从回声上行）
    assert all(write[5] is None for write in env["writes"])


def test_incremental_pull_applies_set_and_delta(env) -> None:
    env["state"]["remote_history_id"] = 1
    env["entries"].setdefault("蔡元君", {})["stock"] = {"energy": {"value": 10}}
    env["server"].rows = [
        {"remote_id": 1},
        {"remote_id": 2, "username": "蔡元君", "type": "stock", "key": "energy",
         "op": "delta", "delta": 5, "value": None},
        {"remote_id": 3, "username": "蔡元君", "type": "stock", "key": "moon",
         "op": "set", "value": 7},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 2
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 15   # 10 + 5
    assert env["entries"]["蔡元君"]["stock"]["moon"]["value"] == 7
    assert env["state"]["remote_history_id"] == 3


def test_push_uploads_only_ledger_rows_above_watermark(env) -> None:
    env["state"]["push_local_id"] = 100
    env["history"].extend([
        {"id": 100, "username": "蔡元君", "type": "stock", "key": "old",
         "new_value": 1, "new_value_text": "", "change_type": "set",
         "delta_value": None, "ts": "2026-11-03T09:00:00"},
        {"id": 101, "username": "蔡元君", "type": "stock", "key": "energy",
         "new_value": 5, "new_value_text": "", "change_type": "set",
         "delta_value": None, "ts": "2026-11-03T10:00:00"},
        {"id": 102, "username": "蔡元君", "type": "stock", "key": "energy",
         "new_value": 3, "new_value_text": "", "change_type": "delta",
         "delta_value": -2, "ts": "2026-11-03T10:05:00"},
    ])
    report = snapshot_sync.push_local("t")
    assert report.pushed == 2
    assert [op["local_id"] for op in env["server"].pushed] == [101, 102]
    assert env["server"].pushed[1]["op"] == "delta"
    assert env["server"].pushed[1]["delta"] == -2
    assert env["state"]["push_local_id"] == 102


def test_overwrite_warning_lists_users_from_latest_snapshot(env) -> None:
    # 快照是账户级多份：只看最新一份（列表首位），旧的忽略
    env["server"].snapshots = [
        {"id": 3, "snapshot_remote_id": 7, "origin_instance_id": "other",
         "origin_name": "手机", "created_at": "T3", "entry_count": 2,
         "entries": json.dumps([
             {"username": "蔡元君", "type": "stock", "key": "energy", "value": 1},
             {"username": "宛元芷", "type": "stock", "key": "energy", "value": 2}])},
        {"id": 2, "snapshot_remote_id": 5, "origin_instance_id": "mine",
         "origin_name": "本机", "created_at": "T2", "entry_count": 1,
         "entries": json.dumps([{"username": "楚湘菱", "type": "stock", "key": "e", "value": 1}])},
    ]
    env["entries"]["蔡元君"] = {"stock": {"energy": {"value": 1}, "moon": {"value": 2}}}
    env["entries"]["楚湘菱"] = {"stock": {"energy": {"value": 1}}}
    # 宛元芷：本地无数据 → 不需要警告；楚湘菱 只出现在旧快照里 → 也不算
    warnings = snapshot_sync.overwrite_warnings("t")
    assert [row["username"] for row in warnings] == ["蔡元君"]
    assert warnings[0]["entries"] == 2
    assert warnings[0]["origin_name"] == "手机"

    # 最新快照来自本机 → 无需任何确认
    env["server"].snapshots[0]["origin_instance_id"] = "mine"
    assert snapshot_sync.overwrite_warnings("t") == []


def test_pull_stops_with_pending_when_overwrite_not_confirmed(env) -> None:
    env["server"].snapshots = [{
        "id": 2, "snapshot_remote_id": 9, "origin_instance_id": "other",
        "origin_name": "书房PC", "created_at": "T2", "entry_count": 1,
        "entries": json.dumps([{"username": "蔡元君", "type": "stock", "key": "energy",
                                "value": 99}])}]
    env["entries"]["蔡元君"] = {"stock": {"energy": {"value": 5}}}
    report = snapshot_sync.pull_and_replay("t")
    assert [row["username"] for row in report.pending] == ["蔡元君"]
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 5   # 未被覆盖
    assert env["state"].get("remote_history_id", 0) == 0
    # sync_all 同样停在 pending，且不做上行
    combined = snapshot_sync.sync_all("t")
    assert combined.pending and combined.pushed == 0


def test_network_failure_reports_error_without_crashing(env) -> None:
    env["server"].fail = True
    report = snapshot_sync.pull_and_replay("t")
    assert report.errors
    assert env["entries"] == {}
