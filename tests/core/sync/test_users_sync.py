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
    users = tmp_path / "users"
    users.mkdir()
    return {"server": server, "tmp": tmp_path, "users": users, "account": account_json}


def _write_roster(tmp, names: list[str]) -> None:
    (tmp / "session.json").write_text(
        json.dumps({"users": names, "other": 1}, ensure_ascii=False) + "\n", encoding="utf-8")


def _roster(tmp) -> list[str]:
    return json.loads((tmp / "session.json").read_text(encoding="utf-8"))["users"]


def test_first_push_sets_order_and_signature(env) -> None:
    tmp, users = env["tmp"], env["users"]
    _write_roster(tmp, ["楚湘菱", "蔡元君"])
    (users / "蔡元君.json").write_text(json.dumps({
        "attributes": {"level": 60}, "ui_state": {"filter": ["x"]}}, ensure_ascii=False),
        encoding="utf-8")
    report = users_sync.sync_users("t")
    assert report.pushed == 2
    push = env["server"].pushes[0]
    assert push["order_changed"] is True                    # 首次（无签名）
    payload = {u["username"]: u for u in push["users"]}
    assert payload["蔡元君"]["data"]["attributes"]["level"] == 60
    assert "ui_state" not in payload["蔡元君"]["data"]       # 界面偏好不上云
    assert users_sync._sync_state()["roster"] == ["楚湘菱", "蔡元君"]


def test_unchanged_roster_pushes_without_reordering_cloud(env) -> None:
    """常规同步（本地顺序没变）不得改写云端顺序——否则旧顺序设备会冲掉新排序。"""
    tmp, server = env["tmp"], env["server"]
    _write_roster(tmp, ["甲", "乙"])
    users_sync.sync_users("t")                              # 建立签名 甲,乙
    # 云端被另一台设备排成 乙,甲
    server.rows["乙"]["position"], server.rows["甲"]["position"] = 0, 1
    report = users_sync.sync_users("t")                     # 本地仍 甲,乙（签名一致）
    assert env["server"].pushes[-1]["order_changed"] is False
    # 下行：本地按云端重排为 乙,甲
    assert _roster(tmp) == ["乙", "甲"]
    assert report.reordered


def test_local_reorder_propagates(env) -> None:
    """本地主动排序 → order_changed=true → 云端采用本地顺序。"""
    tmp, server = env["tmp"], env["server"]
    _write_roster(tmp, ["甲", "乙", "丙"])
    users_sync.sync_users("t")
    _write_roster(tmp, ["丙", "甲", "乙"])                   # 用户手动重排
    users_sync.sync_users("t")
    assert env["server"].pushes[-1]["order_changed"] is True
    assert [_ for _ in sorted(server.rows, key=lambda n: server.rows[n]["position"])] \
        == ["丙", "甲", "乙"]
    assert _roster(tmp) == ["丙", "甲", "乙"]                # 本地保持自己的新顺序


def test_pull_merges_profile_preserving_local_ui_state(env) -> None:
    tmp, users, server = env["tmp"], env["users"], env["server"]
    _write_roster(tmp, ["蔡元君"])
    (users / "蔡元君.json").write_text(json.dumps({
        "attributes": {"level": 1}, "ui_state": {"filter": ["本地"]}},
        ensure_ascii=False), encoding="utf-8")
    server.rows["蔡元君"] = {"position": 0,
                            "data": {"attributes": {"level": 99}},
                            "updated_at": "2999-01-01T00:00:00"}   # 云端更新
    report = users_sync.sync_users("t")
    merged = json.loads((users / "蔡元君.json").read_text(encoding="utf-8"))
    assert merged["attributes"]["level"] == 99               # 资料取云端（LWW）
    assert merged["ui_state"]["filter"] == ["本地"]           # ui_state 永远本机
    assert report.pulled == 1


def test_unknown_remote_user_profile_not_written(env) -> None:
    tmp, server = env["tmp"], env["server"]
    _write_roster(tmp, ["甲"])
    server.rows["陌生人"] = {"position": 0, "data": {"attributes": {}},
                            "updated_at": "T"}
    report = users_sync.sync_users("t")
    assert not (env["users"] / "陌生人.json").exists()
    assert report.pulled == 0
