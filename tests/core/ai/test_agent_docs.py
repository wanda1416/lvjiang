"""文档身份与磁盘路径解耦：compat 旧 ID、逻辑分类与 ID 冲突负例。

契约（见 docs 重构方案 §6）：
- 退役旧 ID 经 compat 解析到现行正文，但不进入列表与搜索（alias 不重复显示）。
- 逻辑分类键与物理目录解耦，由插件按前缀声明；未命中的沿用首段目录。
- 循环 alias、与路径派生 ID 撞车的别名都是声明错误，必须拒绝。
- 未登记 ID、越界路径继续拒绝，不为兼容放宽。
"""
import json
from pathlib import Path

import pytest

from lvjiang.core.ai.agent_docs import AgentDocuments, DocumentLayout


def make_layout(**kwargs) -> DocumentLayout:
    return DocumentLayout(
        directories=("guide",), manifest="guide/manifest.json", **kwargs)


def make_source(tmp_path: Path, layout: DocumentLayout, **kwargs) -> AgentDocuments:
    root = tmp_path / "docs"
    (root / "guide").mkdir(parents=True, exist_ok=True)
    (root / "guide/README.md").write_text("# 入门\n正文\n", encoding="utf-8")
    (root / "guide/advanced.md").write_text("# 进阶\n高级内容\n", encoding="utf-8")
    return AgentDocuments(root, layout, source_mode=True, **kwargs)


def test_compat_id_reads_canonical_but_stays_out_of_listing(tmp_path):
    layout = make_layout(aliases={"start": "guide/README.md"},
                         compat={"guide:README": "start"})
    docs = make_source(tmp_path, layout)
    ids = {entry["id"] for entry in docs.list_docs()["documents"]}
    assert ids == {"start", "guide:advanced"}
    assert docs.read_doc("guide:README")["text"] == docs.read_doc("start")["text"]
    assert docs.search_docs("入门")["matches"][0]["id"] == "start"
    assert not any(m["id"] == "guide:README" for m in docs.search_docs("入门")["matches"])


def test_logical_category_decoupled_from_directories(tmp_path):
    layout = make_layout(categories={"guide": "60-userguide"})
    docs = make_source(tmp_path, layout)
    assert {entry["category"] for entry in docs.list_docs()["documents"]} == {"60-userguide"}
    assert docs.list_docs("60-userguide")["documents"]
    with pytest.raises(ValueError):
        docs.list_docs("guide")


def test_cyclic_compat_is_rejected(tmp_path):
    layout = make_layout(aliases={"a": "guide/README.md"},
                         compat={"x": "y", "y": "x"})
    docs = make_source(tmp_path, layout)
    with pytest.raises(ValueError, match="成环"):
        docs.read_doc("x")


def test_alias_colliding_with_derived_id_is_rejected(tmp_path):
    layout = make_layout(aliases={"guide:advanced": "guide/README.md"})
    docs = make_source(tmp_path, layout)
    with pytest.raises(ValueError, match="ID 冲突"):
        docs.list_docs()


def test_unregistered_and_out_of_root_ids_stay_rejected(tmp_path):
    layout = make_layout(aliases={"start": "guide/README.md"})
    docs = make_source(tmp_path, layout)
    with pytest.raises(ValueError):
        docs.read_doc("no-such-doc")
    with pytest.raises(ValueError):
        docs.read_doc("../secret")


def test_installed_mode_resolves_compat_and_verifies_digest(tmp_path):
    layout = make_layout(aliases={"start": "guide/README.md"},
                         compat={"guide:README": "start"})
    installed = tmp_path / "installed/docs"
    (installed / "guide").mkdir(parents=True)
    body = "# 入门\n正文\n"
    (installed / "guide/README.md").write_bytes(body.encode("utf-8"))
    import hashlib
    manifest = {"version": "1", "api_version": 2, "documents": [
        {"id": "start", "path": "guide/README.md", "title": "入门",
         "category": "60-userguide", "sha256": hashlib.sha256(body.encode()).hexdigest()}]}
    (installed / "guide/manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    docs = AgentDocuments(installed, layout)
    assert docs.read_doc("guide:README")["text"] == body
    assert docs.read_doc("start")["text"] == body
    (installed / "guide/README.md").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="清单不符"):
        docs.read_doc("guide:README")
