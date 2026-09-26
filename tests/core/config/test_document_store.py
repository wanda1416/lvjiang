"""多文档目录存储的公共契约。"""
from __future__ import annotations

import json

import pytest

from lvjiang.core.config.document_store import DocumentDirectoryStore


def test_documents_are_isolated_and_results_are_copies(tmp_path):
    store = DocumentDirectoryStore(tmp_path, {"left": "left.json", "right": "right.json"})

    result = store.mutate("left", lambda data: {**data, "value": 1})
    result["value"] = 2

    assert store.load("left") == {"value": 1}
    assert store.load("right") == {}
    assert json.loads((tmp_path / "_meta.json").read_text()) == {"version": 1}


def test_interrupted_multi_document_write_is_recovered(tmp_path, monkeypatch):
    store = DocumentDirectoryStore(tmp_path, {"left": "left.json", "right": "right.json"})
    store.ensure_initialized()
    original_write = store._write
    failed = False

    def fail_right(path, data):
        nonlocal failed
        if path.name == "right.json" and not failed:
            failed = True
            raise OSError("interrupted")
        original_write(path, data)

    monkeypatch.setattr(store, "_write", fail_right)
    with pytest.raises(OSError, match="interrupted"):
        store.mutate_many(
            ("left", "right"),
            lambda _: {"left": {"value": 1}, "right": {"value": 2}},
        )

    recovered = DocumentDirectoryStore(
        tmp_path, {"left": "left.json", "right": "right.json"}
    )
    assert recovered.load("left") == {"value": 1}
    assert recovered.load("right") == {"value": 2}
    assert not (tmp_path / "_transaction.json").exists()


def test_rejects_unknown_and_invalid_multi_document_updates(tmp_path):
    store = DocumentDirectoryStore(tmp_path, {"known": "known.json"})

    with pytest.raises(KeyError, match="未登记"):
        store.load("unknown")
    with pytest.raises(ValueError, match="无重复"):
        store.mutate_many(("known", "known"), lambda data: data)
    with pytest.raises(ValueError, match="全部请求"):
        store.mutate_many(("known",), lambda _: {})
