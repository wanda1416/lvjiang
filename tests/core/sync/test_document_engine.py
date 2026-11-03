"""文档通道引擎：胜负判定、ui_state 保持、冲突另存、gzip 与头像二进制。"""

from __future__ import annotations

import base64
import gzip
import json
import tempfile
from pathlib import Path

import pytest

from lvjiang.core.sync import engine
from lvjiang.core.sync.client import SyncConflict


class FakeClient:
    """内存版远端：payload 存 base64（与协议一致），按引擎的调用记账。"""

    def __init__(self, documents: dict[tuple[str, str], dict] | None = None):
        self.docs = documents or {}
        self.pushes: list[dict] = []
        self.pulls: list[tuple[str, str]] = []
        self.fail_expected: bool = False

    def manifest(self, token):
        return [{"label": k[0], "kind": k[1], "sha256": d["sha256"], "size": d["size"],
                 "updated_at": d["updated_at"]} for k, d in self.docs.items()]

    def pull(self, token, label, kind):
        self.pulls.append((label, kind))
        entry = self.docs[(label, kind)]
        return {"label": label, "kind": kind, "sha256": entry["sha256"], "size": entry["size"],
                "zipped": entry.get("zipped", True), "updated_at": entry["updated_at"],
                "payload_base64": entry["payload"]}

    def push(self, token, label, kind, sha256, payload_base64, expected_sha256=None):
        key = (label, kind)
        if self.fail_expected and expected_sha256 is not None:
            # 模拟竞态：manifest 读取后服务端又被写入，CAS 必冲突。
            current = self.docs.get(key)
            raise SyncConflict({"sha256": current["sha256"], "payload_base64": current["payload"],
                                "size": current["size"], "updated_at": current["updated_at"],
                                "error": "conflict"})
        self.pushes.append({"label": label, "kind": kind, "sha256": sha256,
                            "payload_base64": payload_base64, "expected": expected_sha256})
        self.docs[key] = {"sha256": sha256, "size": len(payload_base64), "zipped": kind != "avatar",
                          "payload": payload_base64,
                          "updated_at": "2020-01-01T00:00:00Z"}

    # 便捷：远端时间只用于胜负判定，push 统一写成过去时间让本地 mtime 必 newer。
    def set_remote_future(self, label, kind, raw: bytes, updated_at: str):
        payload = base64.b64encode(gzip.compress(raw)).decode("ascii")
        self.docs[(label, kind)] = {"sha256": engine.hashlib.sha256(raw).hexdigest(),
                                    "size": len(raw), "zipped": True,
                                    "payload": payload, "updated_at": updated_at}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # yysls 生产环境经 AppHooks.sync_document_modules 登记；测试直接登记同一映射。
    from lvjiang.core.sync import registry
    registry.register_user_document_kind("loadouts", ".loadouts.json")
    fake = FakeClient()
    monkeypatch.setattr(engine.client, "manifest", fake.manifest)
    monkeypatch.setattr(engine.client, "pull", fake.pull)
    monkeypatch.setattr(engine.client, "push", fake.push)
    dirs = {"users": tmp_path / "users", "avatars": tmp_path / "avatars",
            "session": tmp_path / "session"}
    for directory in dirs.values():
        directory.mkdir(parents=True)
    return {"fake": fake, "dirs": dirs}


def _sync(env):
    return engine.sync_documents("t", users_dir=env["dirs"]["users"],
                                 avatars_dir=env["dirs"]["avatars"],
                                 session_dir=env["dirs"]["session"])


def test_unregistered_plugin_document_not_uploaded_as_user_doc(monkeypatch) -> None:
    """插件未登记时（如同步早于插件注册），带点文件名不得当成 user_doc 上云。"""
    from lvjiang.core.sync import registry

    monkeypatch.setattr(registry, "_USER_DOCUMENT_KINDS", {})
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        users, avatars, session = root / "users", root / "avatars", root / "session"
        for directory in (users, avatars, session):
            directory.mkdir()
        (users / "蔡元君.json").write_text('{"username": "蔡元君"}', encoding="utf-8")
        (users / "蔡元君.loadouts.json").write_text("{}", encoding="utf-8")
        documents = engine.iter_local_documents(users, avatars, session)
    assert [(d.label, d.kind) for d in documents] == []   # 纯资料文件不入文档通道


def test_core_notes_suffix_wins_over_plugin_registry(env) -> None:
    """便利贴必须归类为 notes；曾被误当 user_doc（label「某用户.notes」）。"""
    users = env["dirs"]["users"]
    (users / "蔡元君.notes.json").write_text('{"notes": []}\n', encoding="utf-8")
    (users / "蔡元君.loadouts.json").write_text("{}\n", encoding="utf-8")
    _sync(env)
    kinds = {(p["label"], p["kind"]) for p in env["fake"].pushes}
    assert ("蔡元君", "notes") in kinds
    assert ("蔡元君", "loadouts") in kinds
    assert not any(label.endswith(".notes") for label, _ in kinds)


def test_stale_local_pull_makes_no_conflict_copy(env) -> None:
    """本地只是旧版时直接覆盖，不留副本——否则每次同步都在堆 .conflict 文件。"""
    users = env["dirs"]["users"]
    path = users / "蔡元君.notes.json"
    path.write_text('{"notes": [], "old": true}\n', encoding="utf-8")
    remote = json.dumps({"notes": []}, ensure_ascii=False).encode("utf-8")
    env["fake"].set_remote_future("蔡元君", "notes", remote, "2099-01-01T00:00:00Z")
    report = _sync(env)
    assert report.pulled == ["蔡元君/notes"]
    assert report.conflicts == []
    assert not any(".conflict-" in p.name for p in users.iterdir())


def test_user_profile_not_pushed_as_document_but_avatar_is(env) -> None:
    """用户资料入 account_users 表（users_sync），文档通道只带它引用的头像。"""
    users = env["dirs"]["users"]
    (users / "蔡元君.json").write_text(json.dumps({
        "username": "蔡元君", "avatar": "avatar_a.png",
        "attributes": {"level": 60}, "ui_state": {"equip_filter": ["x"]}}, ensure_ascii=False),
        encoding="utf-8")
    (users / "蔡元君.loadouts.json").write_text("{}\n", encoding="utf-8")
    (env["dirs"]["avatars"] / "avatar_a.png").write_bytes(b"\x89PNG")
    (env["dirs"]["session"] / "profile.yaml").write_text("quota: []\n", encoding="utf-8")
    _sync(env)
    kinds = {(p["label"], p["kind"]) for p in env["fake"].pushes}
    assert ("蔡元君", "user_doc") not in kinds          # 资料不走文档
    assert ("蔡元君", "loadouts") in kinds
    assert ("avatar_a.png", "avatar") in kinds           # 引用的头像照常
    assert ("__global__", "profile_yaml") in kinds


def test_remote_only_user_doc_left_alone(env) -> None:
    """远端遗留的 user_doc 文档（资料已入表）不再下行，也不落本地。"""
    users = env["dirs"]["users"]
    env["fake"].docs[("蔡元君", "user_doc")] = {
        "sha256": "x", "size": 3, "zipped": True, "payload": "e30=",
        "updated_at": "2099-01-01T00:00:00Z"}
    report = _sync(env)
    assert report.pulled == []
    assert not (users / "蔡元君.json").exists()


def test_local_newer_pushes_with_remote_sha_as_lock(env) -> None:
    path = env["dirs"]["users"] / "蔡元君.loadouts.json"
    path.write_text("{\"revision\": 2}\n", encoding="utf-8")
    env["fake"].docs[("蔡元君", "loadouts")] = {
        "sha256": "old", "size": 3, "zipped": True, "payload": "",
        "updated_at": "2020-01-01T00:00:00Z"}
    report = _sync(env)
    assert report.pushed == ["蔡元君/loadouts"]
    push = env["fake"].pushes[0]
    assert push["expected"] == "old"


def test_push_conflict_falls_back_to_remote_win(env, monkeypatch) -> None:
    path = env["dirs"]["users"] / "蔡元君.notes.json"
    path.write_text("{\"revision\": 9, \"notes\": []}\n", encoding="utf-8")
    env["fake"].fail_expected = True
    env["fake"].docs[("蔡元君", "notes")] = {
        "sha256": "cloud-new", "size": 3, "zipped": True,
        "payload": base64.b64encode(gzip.compress(b"{\"notes\": []}\n")).decode(),
        "updated_at": "2020-01-01T00:00:00Z"}
    report = _sync(env)
    # 推送失败回退为拉取：远端胜，本地另存冲突
    assert report.conflicts and "蔡元君/notes" in report.conflicts[0]
    assert report.pulled == ["蔡元君/notes"]


def test_avatar_pushed_raw_binary_and_remote_only_pulled(env) -> None:
    avatar = bytes([0, 255, 137, 80, 78, 71])
    (env["dirs"]["avatars"] / "avatar_a.png").write_bytes(avatar)
    (env["dirs"]["users"] / "蔡元君.json").write_text(json.dumps({
        "username": "蔡元君", "avatar": "avatar_a.png"}, ensure_ascii=False),
        encoding="utf-8")
    report = _sync(env)
    push = next(p for p in env["fake"].pushes if p["kind"] == "avatar")
    assert base64.b64decode(push["payload_base64"]) == avatar  # 不 gzip
    assert not any(p["kind"] == "user_doc" for p in env["fake"].pushes)
    # 远端独有头像（另一台设备上传）→ 下行到本地
    remote_avatar = bytes([1, 2, 3])
    env["fake"].docs[("avatar_b.png", "avatar")] = {
        "sha256": "abc", "size": 3, "zipped": False,
        "payload": base64.b64encode(remote_avatar).decode(),
        "updated_at": "2099-01-01T00:00:00Z"}
    report = _sync(env)
    assert (env["dirs"]["avatars"] / "avatar_b.png").read_bytes() == remote_avatar
    assert "avatar_b.png/avatar" in report.pulled
