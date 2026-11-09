"""用户表同步：名册顺序上行/重排、资料合并保留 ui_state、顺序变更传播。"""

from __future__ import annotations

import json

import pytest

from lvjiang.core.sync import users_sync


class FakeServer:
    """按服务端真实语义模拟：资料 LWW；顺序只在 order_changed 时重写。"""

    def __init__(self):
        self.rows: dict[str, dict] = {}          # username → {position, data, updated_at}
        self.pushes: list[dict] = []

    def post(self, route: str, data: dict, token: str) -> dict:
        if route == "/api/sync/users/push":
            self.pushes.append(data)
            order_changed = data.get("order_changed") is True
            for user in data["users"]:
                prev = self.rows.get(user["username"])
                newer = prev is None or user["updated_at"] >= prev["updated_at"]
                if prev is None:
                    self.rows[user["username"]] = {
                        "position": user["position"],
                        "data": user.get("data", {}),
                        "updated_at": user["updated_at"]}
                else:
                    if order_changed:
                        prev["position"] = user["position"]
                    if newer:
                        prev["data"] = user.get("data", {})
                        prev["updated_at"] = user["updated_at"]
            return {"users": len(data["users"]), "total": len(self.rows)}
        if route == "/api/sync/users/pull":
            rows = [{"username": name, **row}
                    for name, row in sorted(self.rows.items(),
                                            key=lambda kv: (kv[1]["position"], kv[0]))]
            return {"users": rows}
        raise AssertionError(route)


@pytest.fixture()
def env(monkeypatch, tmp_path):
    server = FakeServer()
    account_json = tmp_path / "account.json"
    state: dict = {}

    def load_state() -> dict:
        return dict(state)

    def save_state(value: dict) -> None:
        state.clear()
        state.update(value)

    monkeypatch.setattr(users_sync, "post", server.post)
    monkeypatch.setattr(users_sync, "_session_path", lambda: tmp_path / "session.json")
    monkeypatch.setattr(users_sync, "_users_dir", lambda: tmp_path / "users")
    monkeypatch.setattr(users_sync, "_sync_state", load_state)
    monkeypatch.setattr(users_sync, "_save_sync_state", save_state)
    # 进程内 E2E 密钥可能被其它用例留下：本组用例走明文推送路径，必须复位。
    from lvjiang.core.account import e2e
    monkeypatch.setattr(e2e, "_KEY", None)
    users = tmp_path / "users"
    users.mkdir()
    return {"server": server, "tmp": tmp_path, "users": users, "account": account_json}


def _write_roster(tmp, names: list[str]) -> None:
    (tmp / "session.json").write_text(
        json.dumps({"users": names, "other": 1}, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_user(users, name: str, attributes: dict) -> None:
    (users / f"{name}.json").write_text(
        json.dumps({"attributes": attributes}, ensure_ascii=False) + "\n",
        encoding="utf-8")


def _roster(tmp) -> list[str]:
    return json.loads((tmp / "session.json").read_text(encoding="utf-8"))["users"]


def test_first_push_sets_order_and_signature(env) -> None:
    tmp, users = env["tmp"], env["users"]
    _write_roster(tmp, ["李四", "张三"])
    _write_user(users, "李四", {"level": 50})
    (users / "张三.json").write_text(json.dumps({
        "attributes": {"level": 60}, "ui_state": {"filter": ["x"]}}, ensure_ascii=False),
        encoding="utf-8")
    report = users_sync.sync_users("t")
    assert report.pushed == 2
    push = env["server"].pushes[0]
    assert push["order_changed"] is True                    # 首次（无签名）
    payload = {u["username"]: u for u in push["users"]}
    assert payload["张三"]["data"]["attributes"]["level"] == 60
    assert "ui_state" not in payload["张三"]["data"]       # 界面偏好不上云
    assert users_sync._sync_state()["roster"] == ["李四", "张三"]


def test_unchanged_roster_pushes_without_reordering_cloud(env) -> None:
    """常规同步（本地顺序没变）不得改写云端顺序——否则旧顺序设备会冲掉新排序。

    内容签名一致时推送整个跳过（连请求都不发），比「发了但 order_changed=false」
    更进一步：无变化时不产生任何云端写入。"""
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, ["甲", "乙"])
    _write_user(users, "甲", {"level": 1})
    _write_user(users, "乙", {"level": 2})
    users_sync.sync_users("t")                              # 建立签名 甲,乙
    # 云端被另一台设备排成 乙,甲
    server.rows["乙"]["position"], server.rows["甲"]["position"] = 0, 1
    push_count = len(server.pushes)
    report = users_sync.sync_users("t")                     # 本地仍 甲,乙（签名一致）
    assert len(server.pushes) == push_count                 # 无变化：推送跳过
    assert report.pushed == 0
    # 下行：本地按云端重排为 乙,甲
    assert _roster(tmp) == ["乙", "甲"]
    assert report.reordered


def test_local_reorder_propagates(env) -> None:
    """本地主动排序 → order_changed=true → 云端采用本地顺序。"""
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, ["甲", "乙", "丙"])
    _write_user(users, "甲", {"level": 1})
    _write_user(users, "乙", {"level": 2})
    _write_user(users, "丙", {"level": 3})
    users_sync.sync_users("t")
    _write_roster(tmp, ["丙", "甲", "乙"])                   # 用户手动重排
    users_sync.sync_users("t")
    assert env["server"].pushes[-1]["order_changed"] is True
    assert [_ for _ in sorted(server.rows, key=lambda n: server.rows[n]["position"])] \
        == ["丙", "甲", "乙"]
    assert _roster(tmp) == ["丙", "甲", "乙"]                # 本地保持自己的新顺序


def test_pull_merges_profile_preserving_local_ui_state(env) -> None:
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, ["张三"])
    (users / "张三.json").write_text(json.dumps({
        "attributes": {"level": 1}, "ui_state": {"filter": ["本地"]}},
        ensure_ascii=False), encoding="utf-8")
    server.rows["张三"] = {"position": 0,
                            "data": {"attributes": {"level": 99}},
                            "updated_at": "2999-01-01T00:00:00"}   # 云端更新
    report = users_sync.sync_users("t")
    merged = json.loads((users / "张三.json").read_text(encoding="utf-8"))
    assert merged["attributes"]["level"] == 99               # 资料取云端（LWW）
    assert merged["ui_state"]["filter"] == ["本地"]           # ui_state 永远本机
    assert report.pulled == 1


def test_remote_only_user_is_created_locally(env) -> None:
    """云端有、本地没有的用户，下行时直接创建本地资料文件并纳入名册。"""
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, ["甲"])
    _write_user(users, "甲", {"level": 1})
    server.rows["陌生人"] = {"position": 1,
                            "data": {"attributes": {"level": 9}}, "updated_at": "T"}
    report = users_sync.sync_users("t")
    assert (users / "陌生人.json").exists()
    assert report.pulled == 1
    assert _roster(tmp) == ["甲", "陌生人"]


def test_fresh_device_adopts_cloud_roster_and_creates_files(env) -> None:
    """全新实例（空名册）同步后必须把云端用户完整拉回本地（0.15.10 修复）。"""
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, [])
    server.rows["甲"] = {"position": 0,
                        "data": {"attributes": {"level": 60}}, "updated_at": "T"}
    server.rows["乙"] = {"position": 1,
                        "data": {"attributes": {"level": 70}}, "updated_at": "T"}
    report = users_sync.sync_users("t")
    assert report.pulled == 2
    assert _roster(tmp) == ["甲", "乙"]
    assert json.loads((users / "甲.json").read_text(encoding="utf-8")) \
        ["attributes"]["level"] == 60


def test_missing_user_file_not_pushed_as_empty(env) -> None:
    """名册有名字但本地没有资料文件：不上行空资料，只从云端补回（防 LWW 清空）。"""
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, ["甲"])
    server.rows["甲"] = {"position": 0,
                        "data": {"attributes": {"level": 99}}, "updated_at": "T"}
    report = users_sync.sync_users("t")
    assert server.pushes == []
    assert report.pulled == 1
    local = json.loads((users / "甲.json").read_text(encoding="utf-8"))
    assert local["attributes"]["level"] == 99
