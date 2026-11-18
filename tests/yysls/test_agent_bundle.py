"""智能调律 Agent 发行文档包：插件声明的文档布局在源码与安装包中一致且封闭。"""
import importlib.util
import json
from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.agent.documents import (
    AGENT_DOCUMENT_LAYOUT,
    DOCUMENT_DIRECTORIES,
)
from lvjiang.core.ai.agent_docs import AgentDocuments

BASELINE = json.loads(
    (Path(__file__).parent / "data/agent_docs_baseline.json").read_text(encoding="utf-8"))


def test_published_id_baseline_unchanged():
    """已发布 ID 基线是外部 Agent 依赖的稳定契约：不丢 ID、不换路径、不换分类。

    有意搬迁文档时必须同步更新 fixture（tests/yysls/data/agent_docs_baseline.json）
    并提供兼容映射，让旧 ID 继续可读；直接改断言放行丢失是失败，不是重构。
    """
    source = AgentDocuments(Path(__file__).parents[2] / "docs", AGENT_DOCUMENT_LAYOUT,
                            source_mode=True, schema_provider=lambda: "{}\n")
    catalog = source.layout.catalog(source.root)
    expected = {entry["id"]: entry["path"] for entry in BASELINE["documents"]}
    assert catalog == expected
    categories = {entry["id"]: entry["category"] for entry in BASELINE["documents"]}
    assert {entry["id"]: entry["path"].split("/", 1)[0]
            for entry in BASELINE["documents"]} == categories
    for doc_id in ("entry", "permissions", "dsl", "tool-schema"):
        assert source.read_doc(doc_id)["text"]


def test_bundle_is_closed_and_readable_without_source_checkout(tmp_path):
    script = Path(__file__).parents[2] / "scripts/build_agent_bundle.py"
    spec = importlib.util.spec_from_file_location("build_agent_bundle", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "installed/docs"
    module.build_bundle(output, archive=tmp_path / "agent.zip")
    tools = json.loads((output / "70-agent/schemas/tools.json").read_text(encoding="utf-8"))
    # schema 由真实工具声明生成：公开 schema 的名字集合与注册进 MCP 的一致
    from lvjiang.apps.yysls.core.agent.catalog import tool_catalog
    from lvjiang.apps.yysls.core.agent.service import AgentService
    service = AgentService(Path(__file__).parents[2], dispatch=lambda *_: {})
    assert {tool["name"] for tool in tools} == set(
        tool_catalog(service, AgentDocuments(output, AGENT_DOCUMENT_LAYOUT)))
    generation = next(tool for tool in tools if tool["name"] == "create_generated_tuning")
    payload = generation["inputSchema"]["properties"]["payload"]
    assert payload["required"] == ["base_group", "rules"]
    assert not payload["additionalProperties"]
    assert "plan_targets" in payload["properties"]
    assert "skip_tuning" not in payload["properties"]["run_config"]["properties"]
    docs = AgentDocuments(output, AGENT_DOCUMENT_LAYOUT)
    source = AgentDocuments(Path(__file__).parents[2] / "docs", AGENT_DOCUMENT_LAYOUT, source_mode=True,
                            schema_provider=lambda: (output / "70-agent/schemas/tools.json").read_text(encoding="utf-8"))
    assert source.list_docs() == docs.list_docs()
    for entry in source.list_docs()["documents"]:
        assert source.read_doc(entry["id"]) == docs.read_doc(entry["id"])
    # A source edit is immediately visible without a package build.
    edited = AgentDocuments(tmp_path / "source/docs", AGENT_DOCUMENT_LAYOUT, source_mode=True)
    (edited.root / "70-agent").mkdir(parents=True)
    (edited.root / "70-agent/README.md").write_text("live source", encoding="utf-8")
    assert edited.read_doc("entry")["text"] == "live source"
    (edited.root / "70-agent/README.md").write_text("updated source", encoding="utf-8")
    assert edited.read_doc("entry")["text"] == "updated source"
    manifest = docs.list_docs()
    assert "生成" in docs.read_doc("entry")["text"]
    assert docs.search_docs("培养", "10-game")["matches"]
    assert docs.list_docs("60-userguide")["documents"]
    assert docs.search_docs("连接", "60-userguide")["matches"]
    assert docs.list_docs("30-architecture")["documents"]
    assert "语法" in docs.read_doc("dsl")["text"]
    assert {entry["path"] for entry in manifest["documents"]} >= {
        "10-game/01-equipment-system.md", "60-userguide/01-quick-start.md",
        "30-architecture/32-grammar/01-basics.md", "70-agent/README.md"}
    assert {entry["category"] for entry in manifest["documents"]} == set(DOCUMENT_DIRECTORIES)
    with pytest.raises(ValueError):
        source.read_doc("20-requirements:README")
    with pytest.raises(ValueError):
        source.read_doc("40-development:README")
    with pytest.raises(ValueError):
        docs.list_docs("40-development")

    with pytest.raises(ValueError):
        docs.read_doc("../../config/session/session.json")
    manifest = docs.list_docs()
    assert {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()} == {
        "70-agent/manifest.json", *[d["path"] for d in manifest["documents"]]}
    entry = output / "70-agent/README.md"
    entry.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="清单不符"):
        docs.read_doc("entry")
