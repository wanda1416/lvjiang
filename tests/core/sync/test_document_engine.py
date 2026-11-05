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

    def pull_all(self, token):
        return [{"label": k[0], "kind": k[1], "sha256": d["sha256"], "size": d["size"],
                 "zipped": d.get("zipped", True), "updated_at": d["updated_at"],
                 "payload_base64": d["payload"]} for k, d in self.docs.items()]

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
    monkeypatch.setattr(engine.client, "pull_all", fake.pull_all)
    monkeypatch.setattr(engine.client, "push", fake.push)
    # 增量判定所需的 document_sync 状态与增量 pull 接口
    doc_state: dict = {"remote_document_id": 0, "dirty": {},
                       "stamps": {"__baseline__/none": [0, 0]}}
    monkeypatch.setattr(engine.dirty, "remote_document_id",
                        lambda: doc_state.get("remote_document_id") or 0)
    monkeypatch.setattr(engine.dirty, "dirty_keys",
                        lambda: set(doc_state.get("dirty") or {}))
    monkeypatch.setattr(engine.dirty, "stamps",
                        lambda: dict(doc_state.get("stamps") or {}))

    def fake_save_state(rid, d, stamps=None):
        doc_state["remote_document_id"] = rid
        doc_state["dirty"] = {k: True for k in d}
        if stamps is not None:
            doc_state["stamps"] = stamps

    monkeypatch.setattr(engine.dirty, "save_sync_state", fake_save_state)
    # 本文件只验证「标脏 → 状态机」；磁盘戳感知由 test_document_detect.py 覆盖。
    monkeypatch.setattr(engine, "_detect_local_changes", lambda _docs: set())

    def fake_delta(route, data, token):
        docs = [{"label": k[0], "kind": k[1], "sha256": d["sha256"], "size": d["size"],
                 "zipped": d.get("zipped", True), "updated_at": d["updated_at"],
                 "payload_base64": d["payload"], "revision": i + 1}
                for i, (k, d) in enumerate(sorted(fake.docs.items()))]
        return {"documents": docs, "next_id": len(docs) + 1}

    monkeypatch.setattr(engine, "post", fake_delta)
    dirs = {"users": tmp_path / "users", "avatars": tmp_path / "avatars",
            "session": tmp_path / "session"}
    for directory in dirs.values():
        directory.mkdir(parents=True)
    return {"fake": fake, "dirs": dirs, "doc_state": doc_state}


def _sync(env):
    return engine.sync_documents("t", users_dir=env["dirs"]["users"],
                                 session_dir=env["dirs"]["session"])


def test_unregistered_plugin_document_not_uploaded_as_user_doc(monkeypatch) -> None:
    """插件未登记时（如同步早于插件注册），带点文件名不得当成 user_doc 上云。

    纯资料文件（{名}.json）静默跳过——每个用户一行告警全是噪音；只有带
    kind 后缀却查不到登记的（.loadouts.json）才值得告警。
    """
    from loguru import logger as loguru_logger

    from lvjiang.core.sync import registry

    monkeypatch.setattr(registry, "_USER_DOCUMENT_KINDS", {})
    messages: list[str] = []
    handler = loguru_logger.add(messages.append, level="WARNING")
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            users, avatars, session = root / "users", root / "avatars", root / "session"
            for directory in (users, avatars, session):
                directory.mkdir()
            (users / "蔡元君.json").write_text('{"username": "蔡元君"}', encoding="utf-8")
            (users / "蔡元君.loadouts.json").write_text("{}", encoding="utf-8")
            documents = engine.iter_local_documents(users)
    finally:
        loguru_logger.remove(handler)
    assert [(d.label, d.kind) for d in documents] == []   # 纯资料文件不入文档通道
    warnings = "".join(messages)
    assert "蔡元君.json" not in warnings                  # 资料文件不告警
    assert "蔡元君.loadouts.json" in warnings             # 残留 kind 文件仍告警


def test_core_notes_suffix_wins_over_plugin_registry(env) -> None:
    """便利贴必须归类为 notes；曾被误当 user_doc（label「某用户.notes」）。"""
    users = env["dirs"]["users"]
    (users / "蔡元君.notes.json").write_text('{"notes": []}\n', encoding="utf-8")
    (users / "蔡元君.loadouts.json").write_text("{}\n", encoding="utf-8")
    env["doc_state"]["dirty"] = {"蔡元君/notes": True, "蔡元君/loadouts": True}
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


def test_user_profile_and_avatar_not_in_document_channel(env) -> None:
    """资料入 account_users、头像入用户级头像表、定义走 definition——文档通道全都不带。"""
    users = env["dirs"]["users"]
    (users / "蔡元君.json").write_text(json.dumps({
        "username": "蔡元君", "avatar": "avatar_a.png",
        "attributes": {"level": 60}}, ensure_ascii=False), encoding="utf-8")
    (users / "蔡元君.loadouts.json").write_text("{}\n", encoding="utf-8")
    (env["dirs"]["avatars"] / "avatar_a.png").write_bytes(b"\x89PNG")
    (env["dirs"]["session"] / "profile.yaml").write_text("quota: []\n", encoding="utf-8")
    env["doc_state"]["dirty"] = {"蔡元君/loadouts": True}
    _sync(env)
    kinds = {(p["label"], p["kind"]) for p in env["fake"].pushes}
    assert ("蔡元君", "loadouts") in kinds               # 用户内容照常
    assert ("avatar_a.png", "avatar") not in kinds       # 头像已退出文档通道
    assert ("__global__", "profile_yaml") not in kinds   # 定义已退出文档通道


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
    env["doc_state"]["dirty"] = {"蔡元君/loadouts": True}
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
    env["doc_state"]["dirty"] = {"蔡元君/notes": True}
    report = _sync(env)
    # 推送失败回退为拉取：远端胜，本地另存冲突
    assert report.conflicts and "蔡元君/notes" in report.conflicts[0]
    assert report.pulled == ["蔡元君/notes"]


def test_document_channel_has_no_avatar_kind(env) -> None:
    """头像不再作为文档 kind 存在：远端遗留的 avatar 文档不下行也不落本地。"""
    env["fake"].docs[("avatar_b.png", "avatar")] = {
        "sha256": "abc", "size": 3, "zipped": False,
        "payload": base64.b64encode(bytes([1, 2, 3])).decode(),
        "updated_at": "2099-01-01T00:00:00Z"}
    report = _sync(env)
    assert report.pulled == []
    assert not (env["dirs"]["avatars"] / "avatar_b.png").exists()


# ─── 账户级（会话）文档 ────────────────────────────────────────

@pytest.fixture()
def session_env(env, monkeypatch):
    """在 env 基础上登记一份账户级文档 yysls/play_styles.json。"""
    from lvjiang.core.sync import registry

    monkeypatch.setattr(registry, "_SESSION_DOCUMENTS", {})
    registry.register_session_document("play_styles", "yysls/play_styles.json")
    (env["dirs"]["session"] / "yysls").mkdir(parents=True, exist_ok=True)
    return env


def test_session_document_pushes_under_global_label(session_env) -> None:
    """账户级文档与角色无关：label 必须是 __global__，不能带用户名。"""
    path = session_env["dirs"]["session"] / "yysls" / "play_styles.json"
    path.write_text('{"牵丝·霖": {"火拳": {"min_outer": 1}}}\n', encoding="utf-8")
    session_env["doc_state"]["dirty"] = {"__global__/play_styles": True}
    _sync(session_env)
    assert [(p["label"], p["kind"]) for p in session_env["fake"].pushes] == [
        ("__global__", "play_styles")]


def test_session_document_pulls_to_registered_path(session_env) -> None:
    """云端独有时落到登记的相对路径（目录不存在自动创建）。"""
    raw = '{"裂石·钧": {"双切": {"max_outer": 2}}}\n'.encode()
    session_env["fake"].set_remote_future(
        "__global__", "play_styles", raw, "2099-01-01T00:00:00Z")
    report = _sync(session_env)
    path = session_env["dirs"]["session"] / "yysls" / "play_styles.json"
    assert report.pulled == ["__global__/play_styles"]
    assert path.read_text(encoding="utf-8") == raw.decode()


def test_session_document_race_conflict_keeps_local_copy(session_env) -> None:
    """两边都改过（推送时被云端抢先）时云端赢，但本地这版必须另存不被静默丢弃。"""
    path = session_env["dirs"]["session"] / "yysls" / "play_styles.json"
    path.write_text('{"local": 1}\n', encoding="utf-8")
    session_env["fake"].docs[("__global__", "play_styles")] = {
        "sha256": "cloud-new", "size": 3, "zipped": True,
        "payload": base64.b64encode(gzip.compress(b'{"cloud": 2}\n')).decode(),
        "updated_at": "2020-01-01T00:00:00Z"}
    session_env["fake"].fail_expected = True
    session_env["doc_state"]["dirty"] = {"__global__/play_styles": True}
    report = _sync(session_env)
    assert report.pulled == ["__global__/play_styles"]
    assert report.conflicts and "play_styles" in report.conflicts[0]
    assert json.loads(path.read_text(encoding="utf-8")) == {"cloud": 2}


def test_session_document_stale_local_is_overwritten_without_copy(session_env) -> None:
    """本地只是旧版时直接覆盖，不留副本——否则每次同步都在堆 .conflict 文件。"""
    path = session_env["dirs"]["session"] / "yysls" / "play_styles.json"
    path.write_text('{"local": 1}\n', encoding="utf-8")
    session_env["fake"].set_remote_future(
        "__global__", "play_styles", b'{"cloud": 2}\n', "2099-01-01T00:00:00Z")
    report = _sync(session_env)
    assert report.pulled == ["__global__/play_styles"]
    assert report.conflicts == []
    assert not any(".conflict-" in p.name for p in path.parent.iterdir())


def test_session_document_with_user_label_is_not_laid_down(session_env) -> None:
    """label 不是 __global__ 的同 kind 远端文档不认——账户级文档只有一份。"""
    session_env["fake"].set_remote_future(
        "蔡元君", "play_styles", b"{}\n", "2099-01-01T00:00:00Z")
    report = _sync(session_env)
    assert report.pulled == []
    assert not (session_env["dirs"]["session"] / "yysls" / "play_styles.json").exists()


def test_core_syncs_with_zero_plugins_registered(monkeypatch, tmp_path) -> None:
    """没有任何插件注册时文档通道照常工作——依赖方向只能是插件指向 core。

    这条守的是「单独启动主引擎」：core 必须能在只认得自己那几类文档的情况下
    跑完一轮同步，不得因为缺插件而抛异常或漏掉 core 自己的文档。
    """
    from lvjiang.core.sync import registry

    monkeypatch.setattr(registry, "_USER_DOCUMENT_KINDS", {})
    monkeypatch.setattr(registry, "_SESSION_DOCUMENTS", {})
    fake = FakeClient()
    monkeypatch.setattr(engine.client, "manifest", fake.manifest)
    monkeypatch.setattr(engine.client, "pull", fake.pull)
    monkeypatch.setattr(engine.client, "pull_all", fake.pull_all)
    monkeypatch.setattr(engine.client, "push", fake.push)
    monkeypatch.setattr(engine, "post", lambda route, data, token: {
        "documents": [], "next_id": 1})
    monkeypatch.setattr(engine.dirty, "remote_document_id", lambda: 0)
    monkeypatch.setattr(engine.dirty, "dirty_keys",
                        lambda: {"蔡元君/notes"})
    monkeypatch.setattr(engine.dirty, "stamps", lambda: {"__baseline__/none": [0, 0]})
    monkeypatch.setattr(engine.dirty, "save_sync_state",
                        lambda rid, d, stamps=None: None)
    monkeypatch.setattr(engine, "_detect_local_changes", lambda _docs: set())
    users, session = tmp_path / "users", tmp_path / "session"
    users.mkdir()
    session.mkdir()
    (users / "蔡元君.notes.json").write_text('{"notes": []}\n', encoding="utf-8")
    report = engine.sync_documents("t", users_dir=users, session_dir=session)
    assert report.errors == []
    assert report.pushed == ["蔡元君/notes"]


def _remote_note_payload(raw: bytes) -> str:
    return base64.b64encode(gzip.compress(raw)).decode("ascii")


def _pull_note(monkeypatch, tmp_path, payload: str, pushes: list):
    monkeypatch.setattr(engine.client, "pull", lambda t, label, kind: {
        "label": label, "kind": kind, "sha256": "x", "size": 1, "zipped": True,
        "updated_at": "2020-01-01T00:00:00Z", "payload_base64": payload})
    monkeypatch.setattr(engine.client, "push",
                        lambda *args, **kwargs: pushes.append(args))
    doc = engine.LocalDocument("甲", "notes", tmp_path / "甲.notes.json")
    report = engine.SyncReport()
    engine._pull(doc, "t", report)
    return report


def test_pull_encrypted_note_does_not_reupload(monkeypatch, tmp_path) -> None:
    """远端已是密文的便利贴只落地，不每轮重新上推（0.15.10 修复）。"""
    from lvjiang.core.account import e2e

    key = b"k" * 32
    monkeypatch.setattr(e2e, "_KEY", key)
    ciphertext = e2e.encrypt_text(key, "明文便利贴").encode("utf-8")
    pushes: list = []
    report = _pull_note(monkeypatch, tmp_path,
                        _remote_note_payload(ciphertext), pushes)
    assert report.pulled == ["甲/notes"]
    assert pushes == []


def test_pull_legacy_plaintext_note_reuploads_ciphertext(monkeypatch, tmp_path) -> None:
    """远端是 legacy 明文的便利贴，拉下后立即重推密文（E2E 迁移钩子）。"""
    from lvjiang.core.account import e2e

    monkeypatch.setattr(e2e, "_KEY", b"k" * 32)
    pushes: list = []
    report = _pull_note(monkeypatch, tmp_path,
                        _remote_note_payload("明文便利贴".encode("utf-8")), pushes)
    assert report.pulled == ["甲/notes"]
    assert len(pushes) == 1
    # 上推的是密文（gzip 后 base64），而不是原始明文
    _, _, kind, _, pushed_payload = pushes[0]
    assert kind == "notes"
    pushed_raw = gzip.decompress(base64.b64decode(pushed_payload))
    from lvjiang.core.account.e2e import is_encrypted_text
    assert is_encrypted_text(pushed_raw.decode("utf-8"))
