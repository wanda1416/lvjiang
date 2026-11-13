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
        self.push_bodies: list[dict] = []
        self.fail = False

    def post(self, route: str, data: dict, token: str) -> dict:
        if self.fail:
            raise snapshot_sync.SyncError("账号服务暂时不可用")
        if route == "/api/sync/state":
            return {"protocol_version": 2}
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
            return {"ops": rows,
                    "last_remote_id": rows[-1]["remote_id"] if rows else since}
        if route == "/api/sync/profile/push":
            self.push_bodies.append(data)
            results = []
            for op in data["ops"]:
                remote_id = len(self.rows) + 1
                self.rows.append({"remote_id": remote_id,
                                  "instance_id": "mine", "local_id": op["local_id"],
                                  "username": op["username"], "type": op["type"],
                                  "key": op["key"], "op": op["op"],
                                  "value": op.get("value"), "delta": op.get("delta"),
                                  "value_text": op.get("value_text", ""),
                                  "source": op.get("source", ""),
                                  "happened_at": op["happened_at"],
                                  "op_id": op.get("op_id")})
                self.pushed.append(op)
                results.append({"op_id": op.get("op_id"), "remote_id": remote_id,
                                "status": "accepted"})
            return {"accepted": len(results), "skipped": 0,
                    "remote_id": len(self.rows), "results": results}
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
    mine = "mine"

    def db_upsert(username, type_, key, value, event_at=None, op_type=None,
                  delta_value=None, source="", value_text="", sync_from=None,
                  *, instance_id=None, local_id=None, history_ts=None,
                  op_id=None, remote_id=None, touch_entry=True,
                  history_old_value_text=None):
        writes.append((username, type_, key, value, value_text, op_type))
        bucket = entries.setdefault(username, {}).setdefault(type_, {})
        previous = bucket.get(key, {})
        if touch_entry:
            bucket[key] = {
                "value": value, "value_text": value_text,
                "event_at": event_at or "2026-11-03T10:00:00",
                "op_id": op_id or previous.get("op_id") or ""}
        if op_type is not None:
            history.append({
                "id": max((r["id"] for r in history), default=0) + 1,
                "username": username, "type": type_, "key": key,
                "old_value": previous.get("value"), "new_value": value,
                "value_text": value_text, "op_type": op_type,
                "delta_value": delta_value,
                "event_at": history_ts or "2026-11-03T10:00:00",
                "source": source,
                "sync_from": sync_from or "",
                "old_value_text": history_old_value_text or "",
                "instance_id": instance_id or mine,
                "local_id": local_id,
                "op_id": op_id or "",
                "remote_id": remote_id,
            })

    monkeypatch.setattr(snapshot_sync, "post", server.post)
    monkeypatch.setattr(snapshot_sync, "profile_sync_state", lambda: dict(state))
    monkeypatch.setattr(snapshot_sync, "save_profile_sync_state",
                        lambda value: state.update(value))
    monkeypatch.setattr(snapshot_sync.repository, "db_upsert", db_upsert)
    def db_upsert_many(username, batch):
        for entry in batch:
            type_, key, value, event_at = entry[0], entry[1], entry[2], entry[3]
            value_text = entry[7] if len(entry) > 7 else ""
            db_upsert(username, type_, key, value, event_at=event_at,
                      value_text=value_text)
    monkeypatch.setattr(snapshot_sync.repository, "db_upsert_many", db_upsert_many)
    def _read_all(username):
        from lvjiang.core.profile.rows import Entry
        rows = []
        for type_, group in entries.get(username, {}).items():
            for key, raw in group.items():
                rows.append(Entry(username=username, type=type_, key=key,
                                  value=float(raw.get("value") or 0),
                                  value_text=raw.get("value_text") or "",
                                  event_at=raw.get("event_at") or "",
                                  op_id=raw.get("op_id") or ""))
        return rows

    monkeypatch.setattr(snapshot_sync.repository, "db_read_all", _read_all)
    def _read_entry(u, t, k):
        from lvjiang.core.profile.rows import Entry
        raw = entries.get(u, {}).get(t, {}).get(k)
        if not raw:
            return None
        return Entry(username=u, type=t, key=k,
                     value=float(raw.get("value") or 0),
                     value_text=raw.get("value_text") or "",
                     event_at=raw.get("event_at") or "",
                     op_id=raw.get("op_id") or "",
                     locked=bool(raw.get("locked")))

    monkeypatch.setattr(snapshot_sync.repository, "db_read_entry", _read_entry)
    monkeypatch.setattr(snapshot_sync.repository, "db_count_history",
                        lambda username: len([r for r in history if r["username"] == username]))

    def _users_with_history_since(since, author=""):
        return {r["username"] for r in history
                if r["event_at"] > since and (not author or r.get("instance_id") == author)}

    monkeypatch.setattr(snapshot_sync.repository, "db_users_with_history_since",
                        _users_with_history_since)
    # 回放幂等判据：账本行身份 (来源设备, 该设备上的第几号操作)
    locked: set[tuple[str, str, str]] = set()
    monkeypatch.setattr(snapshot_sync.repository, "db_is_locked",
                        lambda u, t, k: (u, t, k) in locked)
    def _rows_of(since, limit, author=""):
        from lvjiang.core.profile.repository import _author_stamp

        who = author or _author_stamp()
        return [r for r in history
                if r["id"] > since and r.get("op_type") != "reset"
                and r.get("instance_id", who) == who][:limit]

    monkeypatch.setattr(snapshot_sync.repository, "db_get_history_since", _rows_of)
    monkeypatch.setattr(snapshot_sync.repository, "db_op_id_seen",
                        lambda op_id: any(r.get("op_id") == op_id for r in history))
    def _unacked(author, limit):
        from lvjiang.core.profile.rows import PendingOp
        rows = [r for r in history
                if r.get("instance_id") == author and r.get("remote_id") is None
                and r.get("op_type") in ("set", "delta")
                and not str(r.get("op_id") or "").startswith("unscoped:")][:limit]
        return [PendingOp(
            local_id=int(r["id"]), op_id=str(r.get("op_id") or ""),
            username=r["username"], type=r["type"], key=r["key"],
            value=float(r.get("new_value") or 0),
            value_text=r.get("value_text") or "",
            op_type=r.get("op_type") or "set",
            event_at=r.get("event_at") or "",
            source=r.get("source") or "",
        ) for r in rows]
    monkeypatch.setattr(snapshot_sync.repository, "db_list_unacked", _unacked)
    def _mark(pairs):
        by_id = {r["id"]: r for r in history}
        for history_id, remote in pairs:
            row = by_id.get(history_id)
            if row is not None and row["remote_id"] is None:
                row["remote_id"] = remote
    monkeypatch.setattr(snapshot_sync.repository, "db_mark_remote_ids", _mark)

    def _reopen(author):
        for row in history:
            if (row["instance_id"] == author and row["remote_id"] == -1
                    and row["op_type"] in ("set", "delta")
                    and not str(row["op_id"] or "").startswith("unscoped:")):
                row["remote_id"] = None

    monkeypatch.setattr(snapshot_sync.repository, "db_reopen_abandoned", _reopen)

    def _merge(rows):
        applied = 0
        for row in rows:
            username, type_, key = row["username"], row["type"], row["key"]
            if (username, type_, key) in locked:
                continue
            current = entries.get(username, {}).get(type_, {}).get(key, {})
            event_at = row.get("event_at") or ""
            op_id = row.get("op_id") or ""
            if current and not snapshot_sync.is_strictly_newer(
                    event_at, op_id, current.get("event_at") or "",
                    current.get("op_id") or ""):
                continue
            db_upsert(username, type_, key, row.get("value") or 0,
                      event_at=event_at or None,
                      value_text=row.get("value_text") or "",
                      op_id=op_id or None)
            applied += 1
        return applied

    monkeypatch.setattr(snapshot_sync.repository, "db_merge_snapshot_candidates", _merge)
    monkeypatch.setattr(snapshot_sync.repository, "_author_stamp", lambda: mine)
    # 不 mock 的话 push/bootstrap 会走真库（config/session/profile.db）——
    # 既违反测试隔离门禁，泄漏的连接还会在后续用例里以
    # PytestUnraisableExceptionWarning 的形式炸出来。
    monkeypatch.setattr(snapshot_sync.repository, "db_max_history_id",
                        lambda: max((r["id"] for r in history), default=0))
    # 同步状态丢失时用它恢复水位：只认真正落过账的远端行
    monkeypatch.setattr(
        snapshot_sync.repository, "db_max_remote_id",
        lambda: max((r["remote_id"] for r in history
                     if isinstance(r.get("remote_id"), int) and r["remote_id"] > 0),
                    default=0))
    monkeypatch.setattr(snapshot_sync, "current_instance_id", lambda: "mine")
    monkeypatch.setattr(snapshot_sync, "backup_local", lambda: str(tmp_path / "backup"))
    return {"server": server, "entries": entries, "history": history,
            "state": state, "writes": writes, "locked": locked}


def test_rebase_when_cloud_snapshot_is_newer(env) -> None:
    env["state"]["profile_snapshot_id"] = 1
    env["state"]["remote_history_id"] = 2
    env["entries"]["张三"] = {"stock": {"energy": {
        "value": 1, "event_at": "2026-11-01T00:00:00", "op_id": "old"}}}
    env["server"].snapshots = [{
        "id": 2, "snapshot_remote_id": 9, "origin_instance_id": "other",
        "origin_name": "书房PC", "created_at": "2026-11-12T00:00:00", "entry_count": 1,
        "entries": json.dumps([{
            "username": "张三", "type": "stock", "key": "energy", "value": 40,
            "event_at": "2026-11-12T08:00:00+08:00", "op_id": "snap"}])}]
    report = snapshot_sync.pull_and_replay("t")
    assert report.snapshots == 1
    entry = env["entries"]["张三"]["stock"]["energy"]
    assert entry["value"] == 40
    assert entry["event_at"] == "2026-11-12T08:00:00"
    assert env["state"]["profile_snapshot_id"] == 2


def test_state_loss_recovers_watermark_instead_of_rebuilding(env) -> None:
    """删 account.json / 退出登录后，用云端快照信息 + 本地已应用水位恢复同步。

    状态丢了不代表要按快照重建：本地账本里留着已应用的 remote_id，
    云端给了最新快照 id 与 head，据此恢复水位即可——既不重放历史，
    也不让快照覆盖本地值。
    """
    # 本机已同步过：账本里有已落账的远端行（remote_id 5），条目是本地更新的值
    env["history"].append({
        "id": 1, "username": "张三", "type": "stock", "key": "energy",
        "old_value": 0, "new_value": 40, "value_text": "", "op_type": "set",
        "delta_value": None, "event_at": "2026-11-12T09:00:00", "source": "",
        "sync_from": "", "old_value_text": "", "instance_id": "mine",
        "local_id": 1, "op_id": "mine-1", "remote_id": 5,
    })
    env["entries"]["张三"] = {"stock": {"energy": {
        "value": 40, "event_at": "2026-11-12T09:00:00", "op_id": "mine-1"}}}
    # 云端有一份更早的快照（水位 3），值更旧
    env["server"].snapshots = [{
        "id": 7, "snapshot_remote_id": 3, "origin_instance_id": "other",
        "origin_name": "书房PC", "created_at": "2026-11-10T00:00:00", "entry_count": 1,
        "entries": json.dumps([{
            "username": "张三", "type": "stock", "key": "energy", "value": 1,
            "event_at": "2026-11-10T00:00:00", "op_id": "snap-old"}])}]
    assert env["state"] == {}            # 状态全丢

    report = snapshot_sync.pull_and_replay("t")

    assert report.snapshots == 0       # 没有按快照重建
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 40   # 本地值没被覆盖
    assert env["state"]["remote_history_id"] == 5                     # 水位按本地已应用恢复
    assert env["state"]["profile_snapshot_id"] == 7                   # 记下云端最新快照
    assert env["state"]["snapshot_remote_id"] == 3


def test_old_protocol_blocks_sync(env, monkeypatch) -> None:
    def post(route, data, token):
        if route == "/api/sync/state":
            return {"protocol_version": 1}
        raise AssertionError(route)

    monkeypatch.setattr(snapshot_sync, "post", post)
    report = snapshot_sync.sync_all("t")
    assert report.pushed == 0
    assert any("尚未升级" in item for item in report.errors)
    followed = snapshot_sync.pull_and_replay("t")
    assert followed.pulled == 0
    assert any("尚未升级" in item for item in followed.errors)


def test_snapshot_without_event_time_uses_snapshot_clock(env) -> None:
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 0, "origin_instance_id": "other",
        "origin_name": "旧PC", "created_at": "2026-11-06T18:00:00.000Z", "entry_count": 1,
        "entries": json.dumps([{
            "username": "张三", "type": "stock", "key": "energy", "value": 4,
        }]),
    }]
    report = snapshot_sync.pull_and_replay("t")
    assert report.snapshots == 1
    entry = env["entries"]["张三"]["stock"]["energy"]
    assert entry["value"] == 4
    assert entry["event_at"] == "2026-11-06T18:00:00"


def test_bootstrap_applies_snapshot_and_sets_watermarks(env) -> None:
    env["server"].rows = [{"remote_id": 1}, {"remote_id": 2}]
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 2, "origin_instance_id": "other",
        "origin_name": "书房PC", "created_at": "2026-11-03T10:00:00", "entry_count": 2,
        "entries": json.dumps([
            {"username": "张三", "type": "stock", "key": "energy", "value": 20,
             "value_text": "", "event_at": "2026-11-03T10:00:00"},
            {"username": "李四", "type": "stock", "key": "energy", "value": 8,
             "value_text": "", "event_at": "2026-11-03T10:00:00"}])}]
    report = snapshot_sync.pull_and_replay("t", allow_overwrite=True)
    assert report.snapshots == 1
    # 一份快照含全部用户，落地时按条目里的 username 归属
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 20
    assert env["entries"]["李四"]["stock"]["energy"]["value"] == 8
    assert env["state"]["remote_history_id"] == 2
    assert env["state"]["profile_snapshot_id"] == 1
    assert env["state"]["local_history_id"] == 0
    assert all(write[5] is None for write in env["writes"])


def test_push_marks_rows_older_than_snapshot_and_uploads_newer(env) -> None:
    """不新于快照候选的行标 -1；更晚的本机行照常上传，不因条数上限拒绝。"""
    env["state"]["profile_snapshot_id"] = 1
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 5, "origin_instance_id": "other",
        "created_at": "2026-11-04T08:00:00", "entry_count": 1,
        "entries": json.dumps([{
            "username": "张三", "type": "stock", "key": "energy",
            "value": 10, "event_at": "2026-11-04T08:00:00", "op_id": "snap"}])}]
    env["history"].extend([
        {"id": 1, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 1, "value_text": "", "op_type": "set",
         "event_at": "2026-11-04T07:00:00", "instance_id": "mine", "op_id": "old",
         "remote_id": None, "local_id": 1},
        {"id": 2, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 12, "value_text": "", "op_type": "set",
         "event_at": "2026-11-04T09:00:00", "instance_id": "mine", "op_id": "new-op-id",
         "remote_id": None, "local_id": 2},
    ])
    report = snapshot_sync.push_local("t")
    assert report.pushed == 1
    assert env["history"][0]["remote_id"] == -1
    assert env["history"][1]["remote_id"] == 1


def test_replay_stale_set_loses_to_newer_local_value(env) -> None:
    """回放按操作时间合并：远端旧 set 不覆盖本地新值（delta 恒应用）。"""
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1
    env["entries"].setdefault("张三", {})["stock"] = {
        "energy": {"value": 120, "event_at": "2026-11-04T07:00:00"}}
    env["server"].rows = [
        {"remote_id": 1},
        {"remote_id": 2, "instance_id": "phone", "local_id": 2,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 100, "happened_at": "2026-11-03T10:00:00"},   # 旧值
        {"remote_id": 3, "instance_id": "phone", "local_id": 3,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "delta", "delta": 5, "happened_at": "2026-11-03T10:00:01"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 0
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 120


def test_replay_stamps_entry_with_source_time_not_local_clock(env) -> None:
    """回放后条目的 event_at 必须是源 op 的 happened_at，不是本机墙钟。

    2026-11-04 手机端 3179 事故根因：回放把 event_at 盖成本地墙钟，此后
    所有远端 set 都被「跳过旧 set」护栏判成过去时，设备永久锁死在旧值。
    条目时间戳必须是源写入时间，护栏比较的才是同一种时间。
    """
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1
    env["server"].rows = [
        {"remote_id": 1},
        {"remote_id": 2, "instance_id": "phone", "local_id": 2,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 7, "happened_at": "2026-11-01T09:00:00"},
        {"remote_id": 3, "instance_id": "phone", "local_id": 3,
         "username": "张三", "type": "stock", "key": "moon",
         "op": "set", "value": 8, "happened_at": "2026-11-02T09:00:00"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 2
    entries = env["entries"]["张三"]["stock"]
    assert entries["energy"]["event_at"] == "2026-11-01T09:00:00"
    assert entries["moon"]["event_at"] == "2026-11-02T09:00:00"


def test_replayed_set_wins_over_later_stale_replay_of_same_key(env) -> None:
    """先回放新 set 再回放旧 set：旧的被护栏拦下，且不污染时间戳。

    与上一条互补：护栏判断的"本地现值时间"现在也是源时间，两端比较的是
    同一种时钟——时间上更晚的 set 恒胜，无论到达顺序。
    """
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1
    env["server"].rows = [
        {"remote_id": 1},
        # 到达序：先旧后新（离线设备补传 + 在线设备新写的典型混排）
        {"remote_id": 2, "instance_id": "phone", "local_id": 2,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 100, "happened_at": "2026-11-01T09:00:00"},
        {"remote_id": 3, "instance_id": "phone", "local_id": 3,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 200, "happened_at": "2026-11-03T09:00:00"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 2
    entry = env["entries"]["张三"]["stock"]["energy"]
    assert entry["value"] == 200
    assert entry["event_at"] == "2026-11-03T09:00:00"


def test_incremental_pull_applies_set_and_delta(env) -> None:
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1   # 已落地过，不得重新 bootstrap
    env["entries"].setdefault("张三", {})["stock"] = {"energy": {"value": 10}}
    env["server"].rows = [
        {"remote_id": 1},
        {"remote_id": 2, "instance_id": "phone", "local_id": 2,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "delta", "delta": 5, "value": None},
        {"remote_id": 3, "instance_id": "phone", "local_id": 3,
         "username": "张三", "type": "stock", "key": "moon",
         "op": "set", "value": 7},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 1
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 10
    assert env["entries"]["张三"]["stock"]["moon"]["value"] == 7
    assert env["state"]["remote_history_id"] == 3


def test_push_uploads_only_ledger_rows_above_watermark(env) -> None:
    env["state"]["local_history_id"] = 100
    env["history"].extend([
        {"id": 100, "username": "张三", "type": "stock", "key": "old",
         "new_value": 1, "value_text": "", "op_type": "set",
         "delta_value": None, "event_at": "2026-11-03T09:00:00",
         "instance_id": "mine", "op_id": "already", "remote_id": 8,
         "local_id": 100},
        {"id": 101, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 5, "value_text": "", "op_type": "set",
         "delta_value": None, "event_at": "2026-11-03T10:00:00",
         "instance_id": "mine", "op_id": "set-row", "remote_id": None,
         "local_id": 101},
        {"id": 102, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 3, "value_text": "", "op_type": "delta",
         "delta_value": -2, "event_at": "2026-11-03T10:05:00",
         "instance_id": "mine", "op_id": "delta-row", "remote_id": None,
         "local_id": 102},
    ])
    report = snapshot_sync.push_local("t")
    assert report.pushed == 2
    assert [op["op_id"] for op in env["server"].pushed] == ["set-row", "delta-row"]
    assert env["server"].pushed[1]["op"] == "set"
    assert env["server"].pushed[1]["value"] == 3


def test_push_rejected_row_does_not_block_the_rest(env, monkeypatch) -> None:
    """一条非法时间只放弃自己，同批其余行继续拿到 remote_id。"""
    env["history"].extend([
        {"id": 1, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 1, "value_text": "", "op_type": "set",
         "event_at": "2000-01-01T00:00:00", "instance_id": "mine", "op_id": "old-row",
         "remote_id": None, "local_id": 1},
        {"id": 2, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 9, "value_text": "", "op_type": "set",
         "event_at": "2026-11-12T09:00:00", "instance_id": "mine", "op_id": "new-row",
         "remote_id": None, "local_id": 2},
    ])

    def post(route, data, token):
        if route == "/api/sync/profile/push":
            return {"results": [
                {"op_id": "old-row", "remote_id": None, "status": "rejected",
                 "error": "invalid_event_at"},
                {"op_id": "new-row", "remote_id": 4, "status": "accepted"},
            ]}
        return env["server"].post(route, data, token)

    monkeypatch.setattr(snapshot_sync, "post", post)
    report = snapshot_sync.push_local("t")
    assert report.pushed == 1
    assert report.rejected == 1
    assert any("校准" in item for item in report.errors)
    assert env["history"][0]["remote_id"] == -1
    assert env["history"][1]["remote_id"] == 4


def test_abandoned_rows_are_rejudged_against_the_snapshot(env) -> None:
    """上次因时间被标 -1 的行，下一轮按快照候选重判，不永久放弃。"""
    env["state"]["profile_snapshot_id"] = 1
    env["server"].snapshots = [{
        "id": 1, "remote_id": 0, "snapshot_remote_id": 0,
        "entries": json.dumps([{
            "username": "张三", "type": "stock", "key": "energy",
            "value": 1, "event_at": "2026-11-04T12:00:00", "op_id": "snap",
        }]),
    }]
    env["history"].extend([
        {"id": 50, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 8, "value_text": "", "op_type": "set",
         "event_at": "2026-11-04T16:00:00", "instance_id": "mine",
         "op_id": "legacy:mine:50", "remote_id": -1, "local_id": 50},
        {"id": 51, "username": "张三", "type": "stock", "key": "energy",
         "new_value": 2, "value_text": "", "op_type": "set",
         "event_at": "2026-11-04T08:00:00", "instance_id": "mine",
         "op_id": "legacy:mine:51", "remote_id": -1, "local_id": 51},
    ])
    report = snapshot_sync.push_local("t")
    assert report.pushed == 1
    assert report.rejected == 0
    assert [op["op_id"] for op in env["server"].pushed] == ["legacy:mine:50"]
    assert env["server"].pushed[0]["happened_at"] == "2026-11-04T16:00:00"
    assert env["history"][0]["remote_id"] == 1
    assert env["history"][1]["remote_id"] == -1


def test_overwrite_warning_lists_users_from_latest_snapshot(env) -> None:
    # 快照是账户级多份：只看最新一份（列表首位），旧的忽略
    env["server"].snapshots = [
        {"id": 3, "snapshot_remote_id": 7, "origin_instance_id": "other",
         "origin_name": "手机", "created_at": "T3", "entry_count": 2,
         "entries": json.dumps([
             {"username": "张三", "type": "stock", "key": "energy", "value": 1},
             {"username": "王五", "type": "stock", "key": "energy", "value": 2}])},
        {"id": 2, "snapshot_remote_id": 5, "origin_instance_id": "mine",
         "origin_name": "本机", "created_at": "T2", "entry_count": 1,
         "entries": json.dumps([{"username": "李四", "type": "stock", "key": "e", "value": 1}])},
    ]
    env["entries"]["张三"] = {"stock": {"energy": {"value": 1}, "moon": {"value": 2}}}
    env["entries"]["李四"] = {"stock": {"energy": {"value": 1}}}
    # 王五：本地无数据 → 不需要警告；李四 只出现在旧快照里 → 也不算
    warnings = snapshot_sync.overwrite_warnings("t")
    assert [row["username"] for row in warnings] == ["张三"]
    assert warnings[0]["entries"] == 2
    assert warnings[0]["origin_name"] == "手机"

    # 最新快照来自本机且本地没有更新的行 → 无需确认（快照已含全部本地状态）
    env["server"].snapshots[0]["origin_instance_id"] = "mine"
    assert snapshot_sync.overwrite_warnings("t") == []

    # 本机快照但本地有快照创建之后、未推送的新行 → 必须确认
    # （2026-11-04 事故：本机旧快照静默盖掉未推送的 323 行更新）
    env["history"].append({"id": 900, "username": "张三", "type": "stock",
                           "key": "energy", "new_value": 8, "value_text": "",
                           "op_type": "set", "delta_value": None,
                           "event_at": "T9", "instance_id": "mine"})
    warnings = snapshot_sync.overwrite_warnings("t")
    assert [row["username"] for row in warnings] == ["张三"]


def test_pull_stops_with_pending_when_overwrite_not_confirmed(env) -> None:
    env["server"].snapshots = [{
        "id": 2, "snapshot_remote_id": 9, "origin_instance_id": "other",
        "origin_name": "书房PC", "created_at": "T2", "entry_count": 1,
        "entries": json.dumps([{"username": "张三", "type": "stock", "key": "energy",
                                "value": 99}])}]
    env["entries"]["张三"] = {"stock": {"energy": {"value": 5}}}
    report = snapshot_sync.pull_and_replay("t")
    assert report.pending == []
    # 两边都没有事件时间时，快照不覆盖已有本地值；水位仍然前进。
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 5
    assert env["state"]["remote_history_id"] == 9


def test_replay_writes_ledger_row_with_origin_identity(env) -> None:
    """回放必须落一行本地账本：来源设备、业务时间、来源描述全都要留住。

    2026-11-06 事故：另一台实例把自己那份陈旧状态当新事实推上来，把用户刚做的
    重置覆盖回去。当时回放刻意不写 history，结果值被改回去却查不到是谁、什么时候、
    从哪台设备改的——变更记录里只剩用户自己那条 reset。
    """
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"regen": {"wumu": {"value": 0.0,
                                                  "event_at": "2026-11-06T07:23:18"}}}
    env["server"].rows = [
        {"remote_id": 1},
        {"remote_id": 2, "instance_id": "other-device", "local_id": 45,
         "username": "张三", "type": "regen", "key": "wumu",
         "op": "set", "value": 20, "value_text": "",
         "old_value_text": "旧备注", "sync_from": "体能",
         "source": "太平武墓", "happened_at": "2026-11-06T07:45:19"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.pulled == 1
    assert env["entries"]["张三"]["regen"]["wumu"]["value"] == 20.0
    row = env["history"][-1]
    assert row["instance_id"] == "other-device"       # 谁写的
    assert row["op_id"] == "legacy:other-device:45"  # 行身份：来源设备的操作
    assert row["event_at"] == "2026-11-06T07:45:19"  # 业务时间，不是本机墙钟
    assert row["source"] == "太平武墓"               # 为什么变
    assert row["sync_from"] == "体能"            # 同步链上游（云端下发）
    assert row["old_value_text"] == "旧备注"      # note 撤销用的旧文本
    assert (row["old_value"], row["new_value"]) == (0.0, 20.0)


def test_replay_is_idempotent_on_repeated_pull(env) -> None:
    """同一条 op 回放两次只生效一次：水位没落盘就崩溃、页级重试都靠这条。

    判据是行身份 ``(来源设备, 该设备上的 local_id)``——库重建后本地自增 id 会
    重新编号，拿它判重必然在重建窗口期失效（旧 delta 会被算第二遍）。
    """
    env["state"]["remote_history_id"] = 1
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"stock": {"energy": {"value": 100}}}
    op = {"remote_id": 2, "instance_id": "other-device", "local_id": 7,
          "username": "张三", "type": "stock", "key": "energy",
          "op": "set", "value": 140, "value_text": "",
          "happened_at": "2026-11-03T11:00:00"}
    env["server"].rows = [{"remote_id": 1}, op]
    assert snapshot_sync.pull_and_replay("t").pulled == 1
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 140

    # 水位被回退（模拟没存盘就崩溃），同一条 op 重新拉一遍
    env["state"]["remote_history_id"] = 1
    assert snapshot_sync.pull_and_replay("t").pulled == 0
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 140
    assert len(env["history"]) == 1


def test_own_echo_ops_are_skipped_by_ledger_identity(env) -> None:
    """自己的 op 回声回来不重放：本机写入的行身份就是 (本机, 本地 id)。"""
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1          # 已落地，不触发 bootstrap
    # 本机写过 id=51 的 delta（效果已在 entries 里：energy=100）
    env["history"].append({"id": 51, "username": "张三", "type": "stock",
                           "key": "energy", "new_value": 100, "value_text": "",
                           "op_type": "delta", "delta_value": 40,
                           "event_at": "2026-11-03T10:00:00",
                           "instance_id": "mine", "local_id": 51,
                           "op_id": "legacy:mine:51"})
    env["entries"]["张三"] = {"stock": {"energy": {"value": 100}}}
    # 云端回放流：自己的 echo（local_id=51）+ 别的设备的新 delta
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "mine", "local_id": 51,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 100, "value_text": "",
         "happened_at": "2026-11-03T10:00:00"},
        {"remote_id": 2, "instance_id": "other", "local_id": 9,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 105, "value_text": "",
         "happened_at": "2026-11-03T11:00:00"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 105
    assert report.pulled == 1
    assert env["writes"] == [(
        "张三", "stock", "energy", 105.0, "", "set")]


def test_replay_of_unchanged_value_touches_nothing(env) -> None:
    """回放的值与本地现值相同就不写：不抬 event_at，也不刷空账本行。

    抬时间戳不是无害的：条目 ``event_at`` 被远端噪声推高之后，此后更早的
    真实 op 会被「跳过旧 set」护栏永久误挡（2026-11-04 手机端 3179 事故）
    """
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"stock": {"energy": {
        "value": 5, "event_at": "2026-11-03T09:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 3,
         "username": "张三", "type": "stock", "key": "energy",
         "op": "set", "value": 5, "value_text": "",
         "happened_at": "2026-11-04T09:00:00"},
    ]
    assert snapshot_sync.pull_and_replay("t").pulled == 1
    assert env["entries"]["张三"]["stock"]["energy"]["event_at"] == "2026-11-04T09:00:00"


def test_no_rebootstrap_when_watermark_is_zero(env) -> None:
    """事故回归：新库快照水位为 0 时，已落地的设备不得每次同步都被快照回滚。

    2026-11-03 数据损坏根源：以 remote_history_id==0 判定「需要 bootstrap」，
    而新库首份快照水位恰为 0 → 每次同步把 entries 回滚到旧快照，tick 累计
    与未推送的改动被反复抹除。
    """
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1   # 已落地过（哪怕水位是 0）
    env["entries"]["张三"] = {"stock": {"energy": {"value": 42}}}   # 本地新值
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 0, "origin_instance_id": "mine",
        "origin_name": "本机", "created_at": "T0", "entry_count": 1,
        "entries": json.dumps([{"username": "张三", "type": "stock",
                                "key": "energy", "value": 7}])}]       # 旧快照值
    report = snapshot_sync.pull_and_replay("t")
    # 不重新落地：本地 42 保留，快照里的 7 不得覆盖
    assert env["entries"]["张三"]["stock"]["energy"]["value"] == 42
    assert report.snapshots == 0
    assert env["writes"] == []


def test_network_failure_reports_error_without_crashing(env) -> None:
    env["server"].fail = True
    report = snapshot_sync.pull_and_replay("t")
    assert report.errors
    assert env["entries"] == {}


class _DayQuota:
    period = "day"
    reset_time = "05:00"
    reset_day = 0


def test_push_skips_reset_and_sends_following_delta(env) -> None:
    """周期重置留在本地，不上传。后面的增量照常上行。"""
    env["state"]["local_history_id"] = 0
    env["history"].extend([
        {"id": 10, "username": "张三", "type": "quota", "key": "weekly",
         "new_value": 0, "value_text": "", "op_type": "reset",
         "delta_value": -5, "event_at": "2026-11-04T05:00:00",
         "instance_id": "mine", "op_id": "reset-row", "remote_id": None},
        {"id": 11, "username": "张三", "type": "quota", "key": "weekly",
         "new_value": 3, "value_text": "", "op_type": "delta",
         "delta_value": 3, "event_at": "2026-11-04T09:00:00",
         "instance_id": "mine", "op_id": "delta-row", "remote_id": None,
         "local_id": 11},
    ])
    snapshot_sync.push_local("t")
    assert [op["op_id"] for op in env["server"].pushed] == ["delta-row"]
    assert env["server"].pushed[0]["op"] == "set"
    assert env["server"].pushed[0]["value"] == 3


def test_cross_period_replay_zeros_before_delta(env, monkeypatch) -> None:
    """回放不依赖对端上传的 reset。本地还停在上个周期时，增量从 0 加。"""
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"quota": {"weekly": {"value": 5,
                                                     "event_at": "2026-11-03T09:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 3, "value": None, "value_text": "",
         "happened_at": "2026-11-04T09:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    assert env["entries"]["张三"]["quota"]["weekly"]["value"] == 5
    assert all(write[5] != "reset" for write in env["writes"])


def test_cross_period_replay_set_replaces_stale_value(env, monkeypatch) -> None:
    """跟上的设备已是 15。落后的设备上个周期还是 5，回放 set(15) 得到 15。"""
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"quota": {"weekly": {"value": 5,
                                                     "event_at": "2026-11-03T09:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "set", "value": 15, "delta": None, "value_text": "",
         "happened_at": "2026-11-04T09:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    assert env["entries"]["张三"]["quota"]["weekly"]["value"] == 15.0


def test_same_period_delta_adds_to_current_value(env, monkeypatch) -> None:
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"quota": {"weekly": {"value": 10,
                                                     "event_at": "2026-11-04T08:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 5, "value": None, "value_text": "",
         "happened_at": "2026-11-04T09:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    assert env["entries"]["张三"]["quota"]["weekly"]["value"] == 10


def test_previous_period_delta_is_discarded(env, monkeypatch) -> None:
    """本地已在新周期时，上一周期的增量不能加进来。"""
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"quota": {"weekly": {"value": 4,
                                                     "event_at": "2026-11-04T06:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 3, "value": None, "value_text": "",
         "happened_at": "2026-11-03T20:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    assert env["entries"]["张三"]["quota"]["weekly"]["value"] == 4
    assert env["writes"] == []


def test_offset_timestamp_keeps_clock_face(env, monkeypatch) -> None:
    """带偏移的发生时间按钟面参加周期判断，不因时区比较抛错而丢掉。"""
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"quota": {"weekly": {"value": 5,
                                                     "event_at": "2026-11-03T09:00:00"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 3, "value": None, "value_text": "",
         "happened_at": "2026-11-04T09:00:00+08:00"},
    ]
    report = snapshot_sync.pull_and_replay("t")
    assert report.errors == []
    assert env["entries"]["张三"]["quota"]["weekly"]["value"] == 5


def test_unparseable_local_time_does_not_zero_base(env, monkeypatch) -> None:
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["entries"]["张三"] = {"quota": {"weekly": {"value": 10,
                                                     "event_at": "not-a-time"}}}
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 1, "value": None, "value_text": "",
         "happened_at": "2026-11-04T09:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    assert env["entries"]["张三"]["quota"]["weekly"]["value"] == 10


def test_old_period_delta_does_not_seed_empty_entry(env, monkeypatch) -> None:
    """没有本地条目时，已经过去的周期增量也不写入当前值。"""
    monkeypatch.setattr(snapshot_sync, "_quota_key", lambda key: _DayQuota())
    env["state"]["remote_history_id"] = 0
    env["state"]["profile_snapshot_id"] = 1
    env["server"].rows = [
        {"remote_id": 1, "instance_id": "other", "local_id": 1,
         "username": "张三", "type": "quota", "key": "weekly",
         "op": "delta", "delta": 3, "value": None, "value_text": "",
         "happened_at": "2000-01-01T20:00:00"},
    ]
    snapshot_sync.pull_and_replay("t")
    assert "张三" not in env["entries"]


def test_bootstrap_skips_locked_entries_silently(env) -> None:
    """锁定条目拒绝快照覆盖是锁的语义，静默跳过——不得刷误导性 ERROR。"""
    env["server"].rows = [{"remote_id": 1}]
    env["server"].snapshots = [{
        "id": 1, "snapshot_remote_id": 1, "origin_instance_id": "other",
        "origin_name": "手机", "created_at": "2026-11-04T13:20:00", "entry_count": 2,
        "entries": json.dumps([
            {"username": "王五", "type": "quota", "key": "locked_key", "value": 9,
             "event_at": "2026-11-04T13:00:00"},
            {"username": "张三", "type": "quota", "key": "free_key", "value": 7,
             "event_at": "2026-11-04T13:00:00"}])}]
    env["locked"].add(("王五", "quota", "locked_key"))
    report = snapshot_sync.pull_and_replay("t", allow_overwrite=True)
    assert report.snapshots == 1
    assert "王五" not in env["entries"]        # 锁定的没被覆盖
    assert env["entries"]["张三"]["quota"]["free_key"]["value"] == 7
    assert not report.errors
