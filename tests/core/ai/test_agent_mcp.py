"""Exercise the actual SDK handshake/transport and the packaged document boundary."""
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lvjiang.core.ai.agent_docs import AgentDocuments, DocumentLayout
from lvjiang.core.ai.agent_mcp import LocalMCPServer


def _published_tree(root: Path) -> None:
    for relative, text in {
        "guide/README.md": "# 入口\n总览说明",
        "guide/sub/details.md": "# 细节\n关键词 needle 在这里",
        "private/secret.md": "# 私有\n不能发布",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


_LAYOUT = DocumentLayout(
    directories=("guide",), manifest="guide/manifest.json",
    aliases={"entry": "guide/README.md", "schema": "guide/schema.json"}, schema_id="schema")


def test_document_layout_is_declared_by_the_caller(tmp_path):
    """宿主只按调用方（插件）声明的布局发布文档：目录层、别名、清单、schema。"""
    _published_tree(tmp_path)
    source = AgentDocuments(tmp_path, _LAYOUT, source_mode=True, schema_provider=lambda: '{"tools": []}')
    manifest = source.list_docs()
    assert {entry["id"] for entry in manifest["documents"]} == {"entry", "guide:sub:details", "schema"}
    assert {entry["category"] for entry in manifest["documents"]} == {"guide"}
    assert source.read_doc("entry")["text"].startswith("# 入口")
    assert source.read_doc("schema")["text"] == '{"tools": []}'
    assert source.search_docs("needle")["matches"][0]["id"] == "guide:sub:details"
    with pytest.raises(ValueError):
        source.read_doc("private:secret")
    with pytest.raises(ValueError, match="guide"):
        source.list_docs("private")
    # 源码模式下的编辑立即可见，不需要重新打包。
    (tmp_path / "guide/README.md").write_text("# 入口\n已更新", encoding="utf-8")
    assert "已更新" in source.read_doc("entry")["text"]

    # 安装模式只读清单里的文档，并校验内容摘要。
    (tmp_path / _LAYOUT.manifest).write_text(json.dumps(source.list_docs()), encoding="utf-8")
    (tmp_path / "guide/schema.json").write_text('{"tools": []}', encoding="utf-8")
    installed = AgentDocuments(tmp_path, _LAYOUT)
    assert installed.list_docs() == source.list_docs()
    assert installed.read_doc("entry") == source.read_doc("entry")
    with pytest.raises(ValueError):
        installed.read_doc("../../private/secret.md")
    (tmp_path / "guide/README.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="清单不符"):
        installed.read_doc("entry")


def test_missing_installed_manifest_is_reported(tmp_path):
    with pytest.raises(ValueError, match="未安装"):
        AgentDocuments(tmp_path, _LAYOUT).list_docs()


def test_official_client_can_discover_and_call_with_token_and_reject_without(free_tcp_port):
    def hello(name: str) -> dict:
        """Read a deterministic test value."""
        return {"name": name}
    server = LocalMCPServer({"hello": hello}, instructions="Test transport only", lv1_check=lambda: True, port=free_tcp_port)
    server.start()

    async def exercise():
        config = server.connection_config()["mcpServers"]["lvjiang"]
        # Network readiness, not an arbitrary timing assertion.
        for _ in range(100):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(config["url"], json={})
                if response.status_code == 401:
                    break
            except httpx.ConnectError:
                await asyncio.sleep(0.01)
        else:
            raise AssertionError("MCP did not become ready")
        async with httpx.AsyncClient(headers=config["headers"]) as client:
            async with streamable_http_client(config["url"], http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert [tool.name for tool in listed.tools] == ["hello"]
                    result = await session.call_tool("hello", {"name": "test"})
                    assert not result.isError
                    assert json.loads(result.content[0].text) == {"name": "test"}
    try:
        asyncio.run(exercise())
    finally:
        server.stop()
        server._thread.join(timeout=5)
        assert not server.running


def test_lv1_gates_whole_protocol_and_rechecks_existing_connections(monkeypatch, free_tcp_port):
    from lvjiang.core import license
    from lvjiang.core.ai.agent_mcp import LV1_REQUIRED_MESSAGE

    licensed = [False]
    calls = []

    def has_feature(level):
        assert level == "lv1"
        return licensed[0]

    monkeypatch.setattr(license, "has_feature", has_feature)

    def hello() -> str:
        calls.append("tool")
        return "hello"

    server = LocalMCPServer({"hello": hello}, instructions="Test Lv1 gate", port=free_tcp_port)

    @server.mcp.resource("lvjiang://test")
    def document() -> str:
        calls.append("document")
        return "private document"

    server.start()

    async def exercise():
        config = server.connection_config()["mcpServers"]["lvjiang"]
        async with httpx.AsyncClient(headers={**config["headers"],
                                              "Accept": "application/json, text/event-stream"}) as client:
            for _ in range(100):
                try:
                    response = await client.get(config["url"])
                    if response.status_code == 403:
                        break
                except httpx.ConnectError:
                    await asyncio.sleep(0.01)
            else:
                raise AssertionError("MCP did not become ready")
            assert response.json()["error"]["message"] == LV1_REQUIRED_MESSAGE
            requests = [
                ("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "test", "version": "1"}}),
                ("tools/list", {}), ("resources/list", {}), ("resources/templates/list", {}),
                ("tools/call", {"name": "hello", "arguments": {}}),
                ("resources/read", {"uri": "lvjiang://test"}),
            ]
            for method, params in requests:
                denied = await client.post(config["url"], json={
                    "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
                assert denied.json() == {"jsonrpc": "2.0", "id": 1, "error": {
                    "code": -32000, "message": LV1_REQUIRED_MESSAGE,
                    "data": {"code": "lv1_required"}}}
            assert not calls
            # Real SDK must expose the hint, including when it cannot initialize.
            from mcp.shared.exceptions import McpError
            async with streamable_http_client(config["url"], http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    with pytest.raises(McpError, match="Lv1"):
                        await session.initialize()
                    licensed[0] = True
                    await session.initialize()
                    assert not (await session.call_tool("hello")).isError
                    licensed[0] = False
                    with pytest.raises(McpError, match="Lv1"):
                        await session.read_resource("lvjiang://test")
                    with pytest.raises(McpError, match="Lv1"):
                        await session.list_tools()
            assert calls == ["tool"]

    try:
        asyncio.run(exercise())
    finally:
        server.stop()
        server._thread.join(timeout=5)
        assert not server.running
