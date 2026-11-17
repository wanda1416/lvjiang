"""来源优先级、原字节、失败和编辑竞争，不 mock 参数核心行为。"""

import json

import pytest

from lvjiang.core.config import workflow_document as doc
from lvjiang.core.fs_util import locked_paths


def sources(tmp_path, session, workflow=None):
    s, w = tmp_path / "session.json", tmp_path / "workflow.json"
    s.write_text(json.dumps(session), encoding="utf-8")
    if workflow is not None:
        w.write_text(json.dumps(workflow), encoding="utf-8")
    return s, w


@pytest.mark.parametrize(
    "new",
    [{}, {"workflow_params": {}}, {"workflow_params": {"wf": {"n": 0, "b": False}}}],
)
def test_new_document_including_empty_is_authoritative_and_archives_legacy(
    tmp_path, new
):
    s, w = sources(tmp_path, {"wf_configs": {"wf": {"n": 9}}, "users": []}, new)
    original, canonical = s.read_bytes(), w.read_bytes()
    doc.migrate_workflow_document(s, w)
    assert w.read_bytes() == canonical
    assert "wf_configs" not in json.loads(s.read_bytes())
    assert original in [
        p.read_bytes() for p in (tmp_path / ".migrations/workflow").iterdir()
    ]
    doc.migrate_workflow_document(s, w)


@pytest.mark.parametrize(
    "raw",
    [b"{", b"[]", b'{"workflow_params": null}', b'{"workflow_params": {"wf": false}}'],
)
def test_corrupt_new_does_not_fall_back_or_delete_old(tmp_path, raw):
    s, w = sources(tmp_path, {"wf_configs": {"wf": {"n": 9}}})
    w.write_bytes(raw)
    before = s.read_bytes()
    with pytest.raises(ValueError):
        doc.migrate_workflow_document(s, w)
    assert (s.read_bytes(), w.read_bytes()) == (before, raw)


def test_failed_second_write_restores_new_and_keeps_old_values(tmp_path, monkeypatch):
    s, w = sources(tmp_path, {"wf_configs": {"wf": {"n": 0, "b": False}}})
    raw = s.read_bytes()
    write = doc.atomic_write_bytes

    def fail_session(path, data, **kwargs):
        if path == s:
            raise OSError("injected")
        write(path, data, **kwargs)

    monkeypatch.setattr(doc, "atomic_write_bytes", fail_session)
    with pytest.raises(OSError, match="injected"):
        doc.migrate_workflow_document(s, w)
    assert s.read_bytes() == raw
    assert not w.exists()
    assert raw in [
        p.read_bytes() for p in (tmp_path / ".migrations/workflow").iterdir()
    ]


def test_subsequent_edit_is_not_rolled_back_after_failed_commit(tmp_path, monkeypatch):
    s, w = sources(tmp_path, {"wf_configs": {"wf": {"n": 9}}})
    write = doc.atomic_write_bytes
    edited = b'{"workflow_params":{"wf":{"n":77}}}'

    def edit_then_fail(path, data, **kwargs):
        if path == s:
            w.write_bytes(edited)
            raise OSError("later edit")
        write(path, data, **kwargs)

    monkeypatch.setattr(doc, "atomic_write_bytes", edit_then_fail)
    with pytest.raises(OSError, match="later edit"):
        doc.migrate_workflow_document(s, w)
    assert w.read_bytes() == edited
    assert "wf_configs" in json.loads(s.read_bytes())


def test_digest_check_rejects_change_since_snapshot(tmp_path):
    s, w = sources(tmp_path, {"wf_configs": {}})
    with locked_paths(s, w):
        source = doc.read_workflow_source(s, w)
        s.write_bytes(b'{"wf_configs":{"later":{"n":1}}}')
        with pytest.raises(RuntimeError, match="读取后修改"):
            doc.commit_workflow_state(source, source.document)
    assert not w.exists()


def test_digest_conflict_keeps_both_old_snapshot_and_new_edit(tmp_path):
    s, w = sources(tmp_path, {"wf_configs": {"wf": {"n": 9}}})
    source = doc.read_workflow_source(s, w)
    old = s.read_bytes()
    edited = b'{"wf_configs":{"wf":{"n":77}}}'
    s.write_bytes(edited)
    with pytest.raises(RuntimeError, match="读取后修改"):
        doc.commit_workflow_state(source, source.document)
    assert s.read_bytes() == edited
    assert old in [p.read_bytes() for p in (tmp_path / ".migrations/workflow").iterdir()]
