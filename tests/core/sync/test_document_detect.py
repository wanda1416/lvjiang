"""磁盘戳感知：只读实例/外部工具改的文档，主实例必须自己发现并上行。"""
from __future__ import annotations

import os

from lvjiang.core.sync import engine


class _FakeClient:
    def __init__(self):
        self.docs: dict[tuple[str, str], dict] = {}
        self.pushes: list[dict] = []

    def manifest(self, token):
        return [{"label": k[0], "kind": k[1], "sha256": d["sha256"],
                 "size": d["size"], "updated_at": d["updated_at"]}
                for k, d in self.docs.items()]

    def pull_all(self, token):
        return [{"label": k[0], "kind": k[1], "sha256": d["sha256"],
                 "size": d["size"], "zipped": True, "updated_at": d["updated_at"],
                 "payload_base64": d["payload"]} for k, d in self.docs.items()]

    def push(self, token, label, kind, sha256, payload_base64, expected_sha256=None):
        self.pushes.append({"label": label, "kind": kind, "sha256": sha256})
        return {"stored": True}


def _env(tmp_path, monkeypatch):
    from lvjiang.core.sync import registry
    registry.register_user_document_kind("loadouts", ".loadouts.json")
    fake = _FakeClient()
    monkeypatch.setattr(engine.client, "manifest", fake.manifest)
    monkeypatch.setattr(engine.client, "pull_all", fake.pull_all)
    monkeypatch.setattr(engine.client, "push", fake.push)
    state: dict = {"remote_document_id": 5, "dirty": {}, "stamps": {}}
    monkeypatch.setattr(engine.dirty, "remote_document_id",
                        lambda: state["remote_document_id"])
    monkeypatch.setattr(engine.dirty, "dirty_keys", lambda: set(state["dirty"]))
    monkeypatch.setattr(engine.dirty, "stamps", lambda: dict(state["stamps"]))
    monkeypatch.setattr(engine.dirty, "save_sync_state",
                        lambda rid, d, stamps=None: state.update(
                            {"remote_document_id": rid,
                             "dirty": set(d),
                             "stamps": stamps if stamps is not None
                             else state["stamps"]}))
    # 云端水位不变（next_id == remote_id + 1），即远端没有更新
    monkeypatch.setattr(engine, "post",
                        lambda route, data, token: {"documents": [],
                                                    "next_id": state["remote_document_id"] + 1})
    users, session = tmp_path / "users", tmp_path / "session"
    for directory in (users, session):
        directory.mkdir()
    return fake, state, users, session


def test_untracked_local_edit_is_detected_and_pushed(tmp_path, monkeypatch):
    """没有 mark_dirty 的改动（只读实例写入）靠磁盘戳被主实例发现并上行。"""
    fake, state, users, session = _env(tmp_path, monkeypatch)
    doc = users / "张三.loadouts.json"
    doc.write_text('{"revision": 2}\n', encoding="utf-8")

    engine.sync_documents("t", users_dir=users, session_dir=session)

    assert [(p["label"], p["kind"]) for p in fake.pushes] == [("张三", "loadouts")]
    # 同步后戳被记下
    assert "张三/loadouts" in state["stamps"]


def test_unchanged_file_is_not_pushed_again(tmp_path, monkeypatch):
    """改过一次并同步之后，文件没再动就不再上行。"""
    fake, state, users, session = _env(tmp_path, monkeypatch)
    doc = users / "张三.loadouts.json"
    doc.write_text('{"revision": 2}\n', encoding="utf-8")

    engine.sync_documents("t", users_dir=users, session_dir=session)
    assert len(fake.pushes) == 1

    engine.sync_documents("t", users_dir=users, session_dir=session)
    assert len(fake.pushes) == 1        # 第二轮不再推


def test_size_change_without_mtime_change_is_detected(tmp_path, monkeypatch):
    """戳同时看 size 与 mtime：只改大小也能识别（同一秒内的写入）。"""
    fake, state, users, session = _env(tmp_path, monkeypatch)
    doc = users / "张三.loadouts.json"
    doc.write_text('{"revision": 2}\n', encoding="utf-8")
    engine.sync_documents("t", users_dir=users, session_dir=session)
    assert len(fake.pushes) == 1

    # 只改内容长度，并把 mtime 固定成原来的值
    original = state["stamps"]["张三/loadouts"][1]
    doc.write_text('{"revision": 3, "extra": true}\n', encoding="utf-8")
    os.utime(doc, ns=(original, original))

    engine.sync_documents("t", users_dir=users, session_dir=session)
    assert len(fake.pushes) == 2


def test_first_run_without_stamps_does_full_compare(tmp_path, monkeypatch):
    """没有任何戳（首次运行）时不做盲目上行，交给 mtime 全量对比。"""
    fake, state, users, session = _env(tmp_path, monkeypatch)
    state["stamps"] = {}
    doc = users / "张三.loadouts.json"
    doc.write_text('{"revision": 2}\n', encoding="utf-8")

    engine.sync_documents("t", users_dir=users, session_dir=session)

    # 云端没有该文档 → 正常上行（全量对比结果）
    assert [(p["label"], p["kind"]) for p in fake.pushes] == [("张三", "loadouts")]
    assert state["stamps"]           # 戳已建立
