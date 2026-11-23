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
    # 生产环境由插件经 AppHooks.sync_document_modules 登记；测试直接登记同一映射。
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
    monkeypatch.setattr(engine.dirty, "_state", lambda: doc_state)
    monkeypatch.setattr(engine.dirty, "_save", lambda s: doc_state.update(s))
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
            (users / "张三.json").write_text('{"username": "张三"}', encoding="utf-8")
            (users / "张三.loadouts.json").write_text("{}", encoding="utf-8")
            documents = engine.iter_local_documents(users, session)
    finally:
        loguru_logger.remove(handler)
    assert [(d.label, d.kind) for d in documents] == []   # 纯资料文件不入文档通道
    warnings = "".join(messages)
    assert "张三.json" not in warnings                  # 资料文件不告警
    assert "张三.loadouts.json" in warnings             # 残留 kind 文件仍告警


def test_core_notes_suffix_wins_over_plugin_registry(env) -> None:
    """便利贴必须归类为 notes；曾被误当 user_doc（label「某用户.notes」）。"""
    users = env["dirs"]["users"]
    (users / "张三.notes.json").write_text('{"notes": []}\n', encoding="utf-8")
    (users / "张三.loadouts.json").write_text("{}\n", encoding="utf-8")
    env["doc_state"]["dirty"] = {"张三/notes": True, "张三/loadouts": True}
    _sync(env)
    kinds = {(p["label"], p["kind"]) for p in env["fake"].pushes}
    assert ("张三", "notes") in kinds
    assert ("张三", "loadouts") in kinds
    assert not any(label.endswith(".notes") for label, _ in kinds)


def test_stale_local_pull_makes_no_conflict_copy(env) -> None:
    """本地只是旧版时直接覆盖，不留副本——否则每次同步都在堆 .conflict 文件。"""
    users = env["dirs"]["users"]
    path = users / "张三.notes.json"
    path.write_text('{"notes": [], "old": true}\n', encoding="utf-8")
    remote = json.dumps({"notes": []}, ensure_ascii=False).encode("utf-8")
    env["fake"].set_remote_future("张三", "notes", remote, "2099-01-01T00:00:00Z")
    report = _sync(env)
    assert report.pulled == ["张三/notes"]
    assert report.conflicts == []
    assert not any(".conflict-" in p.name for p in users.iterdir())


def test_user_profile_and_avatar_not_in_document_channel(env) -> None:
    """资料入 account_users、头像入用户级头像表、定义走 definition——文档通道全都不带。"""
    users = env["dirs"]["users"]
    (users / "张三.json").write_text(json.dumps({
        "username": "张三", "avatar": "avatar_a.png",
        "attributes": {"level": 60}}, ensure_ascii=False), encoding="utf-8")
    (users / "张三.loadouts.json").write_text("{}\n", encoding="utf-8")
    (env["dirs"]["avatars"] / "avatar_a.png").write_bytes(b"\x89PNG")
    (env["dirs"]["session"] / "profile.yaml").write_text("quota: []\n", encoding="utf-8")
    env["doc_state"]["dirty"] = {"张三/loadouts": True}
    _sync(env)
    kinds = {(p["label"], p["kind"]) for p in env["fake"].pushes}
    assert ("张三", "loadouts") in kinds               # 用户内容照常
    assert ("avatar_a.png", "avatar") not in kinds       # 头像已退出文档通道
    assert ("__global__", "profile_yaml") not in kinds   # 定义已退出文档通道


def test_remote_only_user_doc_left_alone(env) -> None:
    """远端遗留的 user_doc 文档（资料已入表）不再下行，也不落本地。"""
    users = env["dirs"]["users"]
    env["fake"].docs[("张三", "user_doc")] = {
        "sha256": "x", "size": 3, "zipped": True, "payload": "e30=",
        "updated_at": "2099-01-01T00:00:00Z"}
    report = _sync(env)
    assert report.pulled == []
    assert not (users / "张三.json").exists()


def test_local_newer_pushes_with_remote_sha_as_lock(env) -> None:
    path = env["dirs"]["users"] / "张三.loadouts.json"
    path.write_text("{\"revision\": 2}\n", encoding="utf-8")
    env["fake"].docs[("张三", "loadouts")] = {
        "sha256": "old", "size": 3, "zipped": True, "payload": "",
        "updated_at": "2020-01-01T00:00:00Z"}
    env["doc_state"]["dirty"] = {"张三/loadouts": True}
    report = _sync(env)
    assert report.pushed == ["张三/loadouts"]
    push = env["fake"].pushes[0]
    assert push["expected"] == "old"


def test_push_conflict_falls_back_to_remote_win(env, monkeypatch) -> None:
    path = env["dirs"]["users"] / "张三.notes.json"
    path.write_text("{\"revision\": 9, \"notes\": []}\n", encoding="utf-8")
    env["fake"].fail_expected = True
    env["fake"].docs[("张三", "notes")] = {
        "sha256": "cloud-new", "size": 3, "zipped": True,
        "payload": base64.b64encode(gzip.compress(b"{\"notes\": []}\n")).decode(),
        "updated_at": "2020-01-01T00:00:00Z"}
    env["doc_state"]["dirty"] = {"张三/notes": True}
    report = _sync(env)
    # 推送失败回退为拉取：远端胜，本地另存冲突
    assert report.conflicts and "张三/notes" in report.conflicts[0]
    assert report.pulled == ["张三/notes"]


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
    """在 env 基础上登记一份账户级文档 demo_app/play_styles.json。"""
    from lvjiang.core.sync import registry

    monkeypatch.setattr(registry, "_SESSION_DOCUMENTS", {})
    registry.register_session_document("play_styles", "demo_app/play_styles.json")
    (env["dirs"]["session"] / "demo_app").mkdir(parents=True, exist_ok=True)
    return env


def test_session_document_pushes_under_global_label(session_env) -> None:
    """账户级文档与角色无关：label 必须是 __global__，不能带用户名。"""
    path = session_env["dirs"]["session"] / "demo_app" / "play_styles.json"
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
    path = session_env["dirs"]["session"] / "demo_app" / "play_styles.json"
    assert report.pulled == ["__global__/play_styles"]
    assert path.read_text(encoding="utf-8") == raw.decode()


def test_session_document_race_conflict_keeps_local_copy(session_env) -> None:
    """两边都改过（推送时被云端抢先）时云端赢，但本地这版必须另存不被静默丢弃。"""
    path = session_env["dirs"]["session"] / "demo_app" / "play_styles.json"
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
    path = session_env["dirs"]["session"] / "demo_app" / "play_styles.json"
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
        "张三", "play_styles", b"{}\n", "2099-01-01T00:00:00Z")
    report = _sync(session_env)
    assert report.pulled == []
    assert not (session_env["dirs"]["session"] / "demo_app" / "play_styles.json").exists()


# ─── core 固有会话文档（workflow / batch） ────────────────────


def test_core_workflow_and_batch_documents_pushed(env) -> None:
    """workflow.json 与 batch.json 是 core 固有账户级文档，label 固定 __global__。"""
    session = env["dirs"]["session"]
    (session / "workflow.json").write_text(
        '{"workflow_params": {"daily": {"count": 1}}}\n', encoding="utf-8")
    (session / "batch.json").write_text('{"groups": {}}\n', encoding="utf-8")
    env["doc_state"]["dirty"] = {"__global__/workflow": True, "__global__/batch": True}
    _sync(env)
    assert sorted((p["label"], p["kind"]) for p in env["fake"].pushes) == [
        ("__global__", "batch"), ("__global__", "workflow")]


def test_workflow_document_race_conflict_discards_local(env) -> None:
    """workflow 文档冲突不留副本：被云端抢先时本地版按最新云端覆盖后丢弃。"""
    path = env["dirs"]["session"] / "workflow.json"
    path.write_text('{"workflow_params": {"a": {"v": "local"}}}\n', encoding="utf-8")
    env["fake"].docs[("__global__", "workflow")] = {
        "sha256": "cloud-new", "size": 3, "zipped": True,
        "payload": base64.b64encode(
            gzip.compress(b'{"workflow_params": {"a": {"v": "cloud"}}}\n')).decode(),
        "updated_at": "2020-01-01T00:00:00Z"}
    env["fake"].fail_expected = True
    env["doc_state"]["dirty"] = {"__global__/workflow": True}
    report = _sync(env)
    assert report.pulled == ["__global__/workflow"]
    assert report.conflicts == []
    assert not any(".conflict-" in p.name for p in path.parent.iterdir())
    assert json.loads(path.read_text(encoding="utf-8"))[
        "workflow_params"]["a"]["v"] == "cloud"


def test_plugins_cannot_register_core_session_kinds() -> None:
    """workflow/batch 是 core 固有语义，插件不得登记或抢占。"""
    from lvjiang.core.sync import registry

    with pytest.raises(ValueError):
        registry.register_session_document("workflow", "plugin/x.json")
    with pytest.raises(ValueError):
        registry.register_session_document("batch", "plugin/x.json")


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
                        lambda: {"张三/notes"})
    monkeypatch.setattr(engine.dirty, "stamps", lambda: {"__baseline__/none": [0, 0]})
    monkeypatch.setattr(engine.dirty, "save_sync_state",
                        lambda rid, d, stamps=None: None)
    monkeypatch.setattr(engine, "_detect_local_changes", lambda _docs: set())
    users, session = tmp_path / "users", tmp_path / "session"
    users.mkdir()
    session.mkdir()
    (users / "张三.notes.json").write_text('{"notes": []}\n', encoding="utf-8")
    report = engine.sync_documents("t", users_dir=users, session_dir=session)
    assert report.errors == []
    assert report.pushed == ["张三/notes"]


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


def test_document_pull_skipped_when_version_row_head_matches(env, monkeypatch) -> None:
    """同步版本行的 document_head 等于本地水位 → 不发 documents/pull；不等就拉。"""
    routes: list[str] = []
    cloud = {"document_head": 5}

    def fake_post(route, data, token):
        routes.append(route)
        if route == "/api/sync/state":
            return dict(cloud)
        return {"documents": [], "next_id": int(data.get("since_revision") or 0) + 1}

    monkeypatch.setattr(engine, "post", fake_post)
    env["doc_state"]["remote_document_id"] = 5
    report = _sync(env)
    assert report.errors == []
    assert routes == ["/api/sync/state"]
    assert env["doc_state"]["remote_document_id"] == 5

    routes.clear()
    cloud["document_head"] = 6                    # 他机推了新文档
    _sync(env)
    assert "/api/sync/documents/pull" in routes

    routes.clear()
    cloud["document_head"] = 0                    # 云端清空：不等就拉，不会漏
    _sync(env)
    assert "/api/sync/documents/pull" in routes



def test_workflow_pull_does_not_overwrite_edit_while_fetching(env, monkeypatch) -> None:
    path = env["dirs"]["session"] / "workflow.json"
    old = b'{"workflow_params":{"wf":{"n":1}}}'
    edited = b'{"workflow_params":{"wf":{"n":7}}}'
    path.write_bytes(old)
    from lvjiang.core.sync import engine
    document = engine.LocalDocument("__global__", "workflow", path)

    def fetch(*_args, **_kwargs):
        path.write_bytes(edited)
        return {"payload_base64": base64.b64encode(b'{"workflow_params":{}}').decode()}

    monkeypatch.setattr(engine.client, "pull", fetch)
    report = engine.SyncReport()
    with pytest.raises(OSError, match="下行期间已被编辑"):
        engine._pull(document, "fixture", report)
    assert path.read_bytes() == edited
    assert not report.pulled


def test_corrupt_workflow_downlink_keeps_valid_local(env):
    from lvjiang.core.sync import engine
    path = env["dirs"]["session"] / "workflow.json"
    original = b'{"workflow_params":{"wf":{"n":0}}}'
    path.write_bytes(original)
    report = engine.SyncReport()
    with pytest.raises(ValueError):
        engine._pull(engine.LocalDocument("__global__", "workflow", path), "fixture", report, raw=b'{"workflow_params":null}')
    assert path.read_bytes() == original
    assert not report.pulled


def test_e2e_silent_migration_bypasses_fast_path_once_then_idles(env, monkeypatch):
    """本地与云端均为未改动的 legacy 明文时，持钥同步应绕过快路径静默加密上推一次，之后恢复空轮。"""
    import hashlib

    from lvjiang.core.account import e2e
    from lvjiang.core.sync import dirty, engine

    key = e2e.derive_key("pw-123456", "a" * 32)
    monkeypatch.setattr(e2e, "current_key", lambda: key)

    path = env["dirs"]["session"] / "workflow.json"
    plain = b'{"workflow_params":{"wf":{"n":1}}}'
    path.write_bytes(plain)
    plain_sha = hashlib.sha256(plain).hexdigest()

    env["fake"].docs[("__global__", "workflow")] = {
        "sha256": plain_sha, "size": len(plain), "zipped": True,
        "payload": base64.b64encode(gzip.compress(plain)).decode(),
        "updated_at": "2026-11-01T00:00:00Z", "revision": 5,
    }
    env["doc_state"]["remote_document_id"] = 5
    env["doc_state"]["stamps"] = {
        "__global__/workflow": [path.stat().st_size, path.stat().st_mtime_ns],
    }

    calls: list[str] = []
    orig_manifest = env["fake"].manifest
    monkeypatch.setattr(engine.client, "manifest",
                        lambda tok: (calls.append("manifest"), orig_manifest(tok))[1])
    monkeypatch.setattr(engine, "cloud_state",
                        lambda _tok, _post: {"document_head": 5})

    report1 = _sync(env)
    assert not report1.errors
    assert "manifest" in calls
    assert len(env["fake"].pushes) == 1
    pushed_raw = gzip.decompress(base64.b64decode(env["fake"].pushes[0]["payload_base64"]))
    assert e2e.is_encrypted_text(pushed_raw.decode("utf-8"))
    assert dirty.e2e_migrated_key_fp() == e2e.key_fingerprint(key)

    # 第二轮应恢复零成本空转（不拉 manifest、不推送）
    calls.clear()
    env["fake"].pushes.clear()
    env["doc_state"]["remote_document_id"] = 5
    report2 = _sync(env)
    assert not report2.errors
    assert calls == []
    assert env["fake"].pushes == []


def test_e2e_decrypt_failure_records_pending_and_retries_when_key_set(env, monkeypatch):
    """无钥时下拉密文不覆写本地文件并登记 pending_decrypt；恢复密钥后自动补拉解密。"""
    from lvjiang.core.account import e2e
    from lvjiang.core.sync import dirty, engine

    key = e2e.derive_key("pw-123456", "a" * 32)
    cur = {"key": None}
    monkeypatch.setattr(e2e, "current_key", lambda: cur["key"])

    path = env["dirs"]["session"] / "workflow.json"
    local_old = b'{"workflow_params":{"wf":{"n":1}}}'
    path.write_bytes(local_old)
    cloud_new = b'{"workflow_params":{"wf":{"n":9}}}'
    enc_bytes = e2e.encrypt_text(key, cloud_new.decode("utf-8")).encode("utf-8")

    env["fake"].set_remote_future("__global__", "workflow", enc_bytes, "2099-01-01T00:00:00Z")
    env["doc_state"]["stamps"] = {
        "__global__/workflow": [path.stat().st_size, path.stat().st_mtime_ns],
    }

    report1 = _sync(env)
    assert not report1.errors
    assert report1.pulled == []
    assert path.read_bytes() == local_old
    assert "__global__/workflow" in dirty.pending_decrypt()

    # 恢复密钥并同步：自动补拉解密并清空 pending_decrypt
    cur["key"] = key
    monkeypatch.setattr(engine, "cloud_state", lambda _tok, _post: {"document_head": 1})
    report2 = _sync(env)
    assert not report2.errors
    assert path.read_bytes() == cloud_new
    assert dirty.pending_decrypt() == set()


def test_pending_encrypt_does_not_overwrite_newer_remote_and_clears_stale(env, monkeypatch):
    """pending_encrypt 重试不得用本机旧内容覆盖远端新版，且失效待加密项应清理。"""
    from lvjiang.core.account import e2e
    from lvjiang.core.sync import dirty, engine

    key = e2e.derive_key("pw-123456", "a" * 32)
    monkeypatch.setattr(e2e, "current_key", lambda: key)

    path = env["dirs"]["session"] / "workflow.json"
    local_old = b'{"workflow_params":{"wf":{"n":1}}}'
    path.write_bytes(local_old)

    # 远端已是另一设备更新后的密文（sha 与本地明文不同）
    cloud_new = e2e.encrypt_text(key, '{"workflow_params":{"wf":{"n":99}}}').encode("utf-8")
    env["fake"].docs[("__global__", "workflow")] = {
        "sha256": engine.hashlib.sha256(cloud_new).hexdigest(),
        "size": len(cloud_new),
        "zipped": True,
        "payload": base64.b64encode(gzip.compress(cloud_new, mtime=0)).decode(),
        "updated_at": "2026-11-01T00:00:00Z",
    }
    env["doc_state"]["remote_document_id"] = 5
    env["doc_state"]["stamps"] = {
        "__global__/workflow": [path.stat().st_size, path.stat().st_mtime_ns],
    }
    dirty.set_pending_encrypt({"__global__/workflow", "ghost/notes"})
    monkeypatch.setattr(engine, "cloud_state", lambda _tok, _post: {"document_head": 5})

    report = _sync(env)
    assert not report.errors
    assert env["fake"].pushes == []
    assert dirty.pending_encrypt() == set()
    assert dirty.e2e_migrated_key_fp() == e2e.key_fingerprint(key)


def test_push_advances_watermark_when_revision_contiguous_only(env, monkeypatch):
    """连续 revision 才推进本地水位（含迁移补推），跳号时不跳过；相同字节不重写入磁盘。"""
    import hashlib
    import os

    from lvjiang.core.account import e2e
    from lvjiang.core.sync import engine

    key = e2e.derive_key("pw-123456", "a" * 32)
    monkeypatch.setattr(e2e, "current_key", lambda: key)

    path = env["dirs"]["session"] / "workflow.json"
    plain = b'{"workflow_params":{"wf":{"n":1}}}'
    path.write_bytes(plain)
    plain_sha = hashlib.sha256(plain).hexdigest()

    env["fake"].docs[("__global__", "workflow")] = {
        "sha256": plain_sha, "size": len(plain), "zipped": True,
        "payload": base64.b64encode(gzip.compress(plain, mtime=0)).decode(),
        "updated_at": "2026-11-01T00:00:00Z",
    }
    env["doc_state"]["remote_document_id"] = 5
    env["doc_state"]["stamps"] = {
        "__global__/workflow": [path.stat().st_size, path.stat().st_mtime_ns],
    }

    cloud_head = {"v": 5}
    pull_calls: list[dict] = []

    def fake_post(route, data, token):
        if route == "/api/sync/state":
            return {"document_head": cloud_head["v"]}
        pull_calls.append(data)
        return {"documents": [], "next_id": cloud_head["v"] + 1}

    next_rev = {"v": 6}
    orig_push = env["fake"].push

    def push_with_rev(token, label, kind, sha256, payload_base64, expected_sha256=None):
        orig_push(token, label, kind, sha256, payload_base64, expected_sha256)
        rev = next_rev["v"]
        cloud_head["v"] = rev
        return {"stored": True, "revision": rev}

    monkeypatch.setattr(engine, "post", fake_post)
    monkeypatch.setattr(engine.client, "push", push_with_rev)

    # 1) 空轮迁移推密文返回连续 revision=6：水位抬到 6，下一轮不发 documents/pull
    report1 = _sync(env)
    assert not report1.errors
    assert env["doc_state"]["remote_document_id"] == 6
    pull_calls.clear()
    report2 = _sync(env)
    assert not report2.errors
    assert pull_calls == []

    # 2) 本地标脏推送连续 revision=7：水位抬到 7
    note = env["dirs"]["users"] / "甲.notes.json"
    note.write_bytes(b'{"notes": []}\n')
    env["doc_state"]["dirty"] = {"甲/notes": True}
    next_rev["v"] = 7
    report3 = _sync(env)
    assert not report3.errors
    assert env["doc_state"]["remote_document_id"] == 7

    # 3) 跳号 revision=9（他机穿插写入 8）：水位保持 7，防止漏拉 8
    env["doc_state"]["dirty"] = {"甲/notes": True}
    next_rev["v"] = 9
    report4 = _sync(env)
    assert not report4.errors
    assert env["doc_state"]["remote_document_id"] == 7

    # 4) _pull 收到与本地相同字节时跳过写盘（保留原 mtime）且不计入 pulled
    fixed_ts = 1_700_000_000
    os.utime(note, (fixed_ts, fixed_ts))
    enc = e2e.encrypt_text(key, '{"notes": []}\n').encode("utf-8")
    pull_report = engine.SyncReport()
    engine._pull(engine.LocalDocument("甲", "notes", note), "t", pull_report, raw=enc)
    assert pull_report.pulled == []
    assert int(note.stat().st_mtime) == fixed_ts


def test_pending_decrypt_clears_unmanaged_and_not_found_zombies(env, monkeypatch):
    """无法定位本地路径或远端已不存在的 pending_decrypt 僵尸项应自动清理，不阻断迁移完成。"""
    from lvjiang.core.account import e2e
    from lvjiang.core.sync import dirty, engine
    from lvjiang.core.sync.client import SyncError

    key = e2e.derive_key("pw-123456", "a" * 32)
    monkeypatch.setattr(e2e, "current_key", lambda: key)
    monkeypatch.setattr(engine, "cloud_state", lambda _tok, _post: {"document_head": 5})
    env["doc_state"]["remote_document_id"] = 5

    dirty.set_pending_decrypt({"张三/unknown_plugin_kind", "李四/notes"})

    def fake_pull(_tok, label, kind):
        raise SyncError("服务接口不存在", code="not_found")

    monkeypatch.setattr(engine.client, "pull", fake_pull)

    report = _sync(env)
    assert report.errors == []
    assert dirty.pending_decrypt() == set()
    assert dirty.e2e_migrated_key_fp() == e2e.key_fingerprint(key)


def test_pull_migration_409_handles_plaintext_and_undecryptable_ciphertext(env, monkeypatch):
    """_pull 触发 409 重拉后：明文版本应落地并入 pending_encrypt；解不开的新密文保留本地并入 pending_decrypt。"""
    from lvjiang.core.account import e2e
    from lvjiang.core.sync import dirty, engine

    key = e2e.derive_key("pw-123456", "a" * 32)
    other_key = e2e.derive_key("pw-other", "a" * 32)
    monkeypatch.setattr(e2e, "current_key", lambda: key)

    path = env["dirs"]["users"] / "甲.notes.json"
    doc = engine.LocalDocument("甲", "notes", path)

    def conflict_push(*_args, **_kwargs):
        raise SyncConflict({"error": "conflict"})

    monkeypatch.setattr(engine.client, "push", conflict_push)

    # 1) 409 重拉到另一份明文：落盘并登记 pending_encrypt
    plain_v2 = b'{"notes": [{"text": "v2"}]}\n'
    monkeypatch.setattr(engine.client, "pull", lambda _tok, label, kind: {
        "label": label, "kind": kind, "sha256": "s2", "size": len(plain_v2),
        "zipped": True, "updated_at": "2026-11-02T00:00:00Z",
        "payload_base64": base64.b64encode(gzip.compress(plain_v2, mtime=0)).decode(),
    })
    report1 = engine.SyncReport()
    engine._pull(doc, "t", report1, raw=b'{"notes": []}\n')
    assert path.read_bytes() == plain_v2
    assert "甲/notes" in dirty.pending_encrypt()

    # 2) 409 重拉到本机解不开的新密文：保留当前本地明文并登记 pending_decrypt
    dirty.set_pending_encrypt(set())
    enc_other = e2e.encrypt_text(other_key, '{"notes": [{"text": "secret"}]}').encode("utf-8")
    monkeypatch.setattr(engine.client, "pull", lambda _tok, label, kind: {
        "label": label, "kind": kind, "sha256": "s3", "size": len(enc_other),
        "zipped": True, "updated_at": "2026-11-03T00:00:00Z",
        "payload_base64": base64.b64encode(gzip.compress(enc_other, mtime=0)).decode(),
    })
    report2 = engine.SyncReport()
    plain_v3 = b'{"notes": [{"text": "v3"}]}\n'
    engine._pull(doc, "t", report2, raw=plain_v3)
    assert path.read_bytes() == plain_v3
    assert "甲/notes" in dirty.pending_decrypt()
