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
            head = max((r["remote_id"] for r in self.rows), default=0)
            return {"snapshots": [{k: v for k, v in row.items() if k != "entries"}
                                  for row in self.snapshots],
                    "head_remote_id": head}
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
    monkeypatch.setattr(snapshot_sync.repository, "db_users_with_history_since",
                        lambda since: {r["username"] for r in history if r["ts"] > since})
    def _fingerprint(u, t, k, ts, *, delta, new_value):
        for r in history:
            if (r["username"], r["type"], r["key"], r["ts"]) != (u, t, k, ts):
                continue
            if delta is not None and r.get("change_type") == "delta" and r["delta_value"] == delta:
                return True
            if delta is None and r.get("change_type") != "delta" and r["new_value"] == new_value:
                return True
        return False
    monkeypatch.setattr(snapshot_sync.repository, "db_history_fingerprint_exists", _fingerprint)
    locked: set[tuple[str, str, str]] = set()
    monkeypatch.setattr(snapshot_sync.repository, "db_is_locked",
                        lambda u, t, k: (u, t, k) in locked)
    def _rows_of(since, limit, author=""):
        from lvjiang.core.profile.repository import _author_stamp

        mine = author or _author_stamp()
        return [r for r in history
                if r["id"] > since and r.get("device_id", mine) == mine][:limit]

    monkeypatch.setattr(snapshot_sync.repository, "db_get_history_since", _rows_of)
    monkeypatch.setattr(snapshot_sync.repository, "db_history_row_exists",
                        lambda history_id: any(r["id"] == history_id for r in history))
    monkeypatch.setattr(snapshot_sync, "current_instance_id", lambda: "mine")
    monkeypatch.setattr(snapshot_sync, "backup_local", lambda: str(tmp_path / "backup"))
    return {"server": server, "entries": entries, "history": history,
            "state": state, "writes": writes, "locked": locked}


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
    # 落地前的本地历史不上行：快照已代表当前状态，自己的陈年账本推上去
    # 只会把旧值回放给别的设备（Android 首次同步曾倾倒两万余条）。
    assert env["state"]["local_history_id"] == max(
        (row["id"] for row in env["history"]), default=0)
    # 回放不写本地 history（因此无从回声上行）
    assert all(write[5] is None for write in env["writes"])


def test_push_refuses_backlog_over_limit(env) -> None:
    """积压超过上限（1000）拒绝上传，提示刷新快照——防止水位异常时全量倾倒。
    周一周期重置风暴（~500 条 reset）属常规量，必须放行。"""
    env["state"]["local_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["state"]["remote_history_id"] = 5
    env["history"].extend([
        {"id": i, "username": "蔡元君", "type": "stock", "key": f"k{i}",
         "new_value": 1, "new_value_text": "", "change_type": "set",
         "delta_value": None, "ts": "2026-11-04T07:00:00"}
        for i in range(1, snapshot_sync.MAX_PUSH_BACKLOG + 2)])
    report = snapshot_sync.push_local("t")
    assert report.pushed == 0
    assert any("刷新快照" in e for e in report.errors)


def test_replay_stale_set_loses_to_newer_local_value(env) -> None:
    """回放按操作时间合并：远端旧 set 不覆盖本地新值（delta 恒应用）。"""
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1
    env["entries"].setdefault("蔡元君", {})["stock"] = {
        "energy": {"value": 120, "updated_at": "2026-11-04T07:00:00"}}
    env["server"].rows = [
        {"remote_id": 1},
        {"remote_id": 2, "username": "蔡元君", "type": "stock", "key": "energy",
         "op": "set", "value": 100, "happened_at": "2026-11-03T10:00:00"},   # 旧值
        {"remote_id": 3, "username": "蔡元君", "type": "stock", "key": "energy",
         "op": "delta", "delta": 5, "happened_at": "2026-11-03T10:00:01"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 1                          # 只有 delta 生效
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 125


def test_incremental_pull_applies_set_and_delta(env) -> None:
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1   # 已落地过，不得重新 bootstrap
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
    env["state"]["local_history_id"] = 100
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
    assert env["state"]["local_history_id"] == 102


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

    # 最新快照来自本机且本地没有更新的行 → 无需确认（快照已含全部本地状态）
    env["server"].snapshots[0]["origin_instance_id"] = "mine"
    assert snapshot_sync.overwrite_warnings("t") == []

    # 本机快照但本地有快照创建之后、未推送的新行 → 必须确认
    # （2026-11-04 事故：本机旧快照静默盖掉未推送的 323 行更新）
    env["history"].append({"id": 900, "username": "蔡元君", "type": "stock",
                           "key": "energy", "new_value": 8, "new_value_text": "",
                           "change_type": "set", "delta_value": None,
                           "ts": "T9"})
    warnings = snapshot_sync.overwrite_warnings("t")
    assert [row["username"] for row in warnings] == ["蔡元君"]


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


def test_own_echo_ops_with_local_rows_are_skipped(env) -> None:
    """回声护栏：自己实例的 op 且本地已有该 history 行 → 不得重放（delta 双算）。"""
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1          # 已落地，不触发 bootstrap
    # 本地已有 id=51 的行（效果已在 entries 里：energy=100）
    env["history"].append({"id": 51, "username": "蔡元君", "type": "stock",
                           "key": "energy", "new_value": 100, "new_value_text": "",
                           "change_type": "delta", "delta_value": 40,
                           "ts": "2026-11-03T10:00:00"})
    env["entries"]["蔡元君"] = {"stock": {"energy": {"value": 100}}}
    # 云端回放流：自己的 echo（local_id=51，本地已有）+ 别的设备的新 delta
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "mine", "local_id": 51,
         "username": "蔡元君", "type": "stock", "key": "energy",
         "op": "delta", "delta": 40, "value": None, "value_text": "",
         "happened_at": "2026-11-03T10:00:00"},
        {"remote_id": 2, "instance_id": "other", "local_id": 9,
         "username": "蔡元君", "type": "stock", "key": "energy",
         "op": "delta", "delta": 5, "value": None, "value_text": "",
         "happened_at": "2026-11-03T11:00:00"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    # echo 被跳过（不双算 +40），他机 delta 正常应用（100+5）
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 105
    assert report.pulled == 1
    assert env["writes"] == [(  # 只有他机那一条写入了 entries
        "蔡元君", "stock", "energy", 105.0, "", None)]


def test_own_echo_ops_matched_by_fingerprint_after_rebuild(env) -> None:
    """回声指纹兜底：库重建后本地 id 重新编号，云端旧 local_id 对不上时，
    仍要靠内容指纹识别 echo，防止旧 delta 重复应用（2026-11-04 二次污染）。"""
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    # 本地重建后的行：id 变成 77（云端记得的是 local_id=51）
    env["history"].append({"id": 77, "username": "蔡元君", "type": "stock",
                           "key": "energy", "new_value": 100, "new_value_text": "",
                           "change_type": "delta", "delta_value": 40,
                           "ts": "2026-11-03T10:00:00"})
    env["entries"]["蔡元君"] = {"stock": {"energy": {"value": 100}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "mine", "local_id": 51,   # 对不上 77
         "username": "蔡元君", "type": "stock", "key": "energy",
         "op": "delta", "delta": 40, "value": None, "value_text": "",
         "happened_at": "2026-11-03T10:00:00"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    # 指纹命中 → 跳过，不再把 +40 算第二遍
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 100
    assert report.pulled == 0
    assert env["writes"] == []


def test_no_rebootstrap_when_watermark_is_zero(env) -> None:
    """事故回归：新库快照水位为 0 时，已落地的设备不得每次同步都被快照回滚。

    2026-11-03 数据损坏根源：以 remote_history_id==0 判定「需要 bootstrap」，
    而新库首份快照水位恰为 0 → 每次同步把 entries 回滚到旧快照，tick 累计
    与未推送的改动被反复抹除。
    """
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1   # 已落地过（哪怕水位是 0）
    env["entries"]["蔡元君"] = {"stock": {"energy": {"value": 42}}}   # 本地新值
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 0, "origin_instance_id": "mine",
        "origin_name": "本机", "created_at": "T0", "entry_count": 1,
        "entries": json.dumps([{"username": "蔡元君", "type": "stock",
                                "key": "energy", "value": 7}])}]       # 旧快照值
    report = snapshot_sync.pull_and_replay("t")
    # 不重新落地：本地 42 保留，快照里的 7 不得覆盖
    assert env["entries"]["蔡元君"]["stock"]["energy"]["value"] == 42
    assert report.snapshots == 0
    assert env["writes"] == []


def test_network_failure_reports_error_without_crashing(env) -> None:
    env["server"].fail = True
    report = snapshot_sync.pull_and_replay("t")
    assert report.errors
    assert env["entries"] == {}


def test_push_maps_reset_to_zero_set(env) -> None:
    """reset 行上行映射为 set(0)：跨边界设备先按时间戳归零再回放 delta，
    不会把 delta 加在周期前的旧基线上（2026-11-04 复盘补的窟窿）。"""
    env["state"]["local_history_id"] = 0
    env["history"].extend([
        {"id": 10, "username": "蔡元君", "type": "quota", "key": "weekly",
         "new_value": 0, "new_value_text": "", "change_type": "reset",
         "delta_value": -5, "ts": "2026-11-04T05:00:00"},
        {"id": 11, "username": "蔡元君", "type": "quota", "key": "weekly",
         "new_value": 3, "new_value_text": "", "change_type": "delta",
         "delta_value": 3, "ts": "2026-11-04T09:00:00"},
    ])
    snapshot_sync.push_local("t")
    assert [op["op"] for op in env["server"].pushed] == ["set", "delta"]
    assert env["server"].pushed[0]["value"] == 0     # reset → set(0)
    assert env["server"].pushed[0]["delta"] is None


def test_cross_boundary_replay_converges_on_stale_base(env) -> None:
    """离线跨周期的设备：reset-set 先归零，delta 才不会加在旧基线上。"""
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["蔡元君"] = {"quota": {"weekly": {"value": 5,
                                                     "updated_at": "2026-11-03T09:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "蔡元君", "type": "quota", "key": "weekly",
         "op": "set", "value": 0, "delta": None, "value_text": "",
         "happened_at": "2026-11-04T05:00:00"},      # 对端周期归零（reset 映射）
        {"remote_id": 2, "instance_id": "other", "local_id": 2,
         "username": "蔡元君", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 3, "value": None, "value_text": "",
         "happened_at": "2026-11-04T09:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    # 5(旧) --set0--> 0 --delta+3--> 3；没有 reset-set 时会错算成 8
    assert env["entries"]["蔡元君"]["quota"]["weekly"]["value"] == 3.0


def test_bootstrap_skips_locked_entries_silently(env) -> None:
    """锁定条目拒绝快照覆盖是锁的语义，静默跳过——不得刷误导性 ERROR。"""
    env["server"].rows = [{"remote_id": 1}]
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 1, "origin_instance_id": "other",
        "origin_name": "手机", "created_at": "2026-11-04T13:20:00", "entry_count": 2,
        "entries": json.dumps([
            {"username": "宛元芷", "type": "quota", "key": "locked_key", "value": 9,
             "updated_at": "2026-11-04T13:00:00"},
            {"username": "蔡元君", "type": "quota", "key": "free_key", "value": 7,
             "updated_at": "2026-11-04T13:00:00"}])}]
    env["locked"].add(("宛元芷", "quota", "locked_key"))
    report = snapshot_sync.pull_and_replay("t", allow_overwrite=True)
    assert report.snapshots == 1
    assert "宛元芷" not in env["entries"]        # 锁定的没被覆盖
    assert env["entries"]["蔡元君"]["quota"]["free_key"]["value"] == 7
    assert not report.errors
