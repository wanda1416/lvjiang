"""Exercise the actual SDK handshake/transport and the packaged document boundary."""
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lvjiang.core.agent_docs import DOCUMENT_DIRECTORIES, AgentDocuments
from lvjiang.core.agent_mcp import LocalMCPServer


def test_bundle_is_closed_and_readable_without_source_checkout(tmp_path):
    script = Path(__file__).parents[2] / "scripts/build_agent_bundle.py"
    spec = importlib.util.spec_from_file_location("build_agent_bundle", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "installed/docs"
    module.build_bundle(output, archive=tmp_path / "agent.zip")
    docs = AgentDocuments(output)
    source = AgentDocuments(Path(__file__).parents[2] / "docs", source_mode=True,
                            schema_provider=lambda: (output / "70-agent/schemas/tools.json").read_text(encoding="utf-8"))
    assert source.list_docs() == docs.list_docs()
    for entry in source.list_docs()["documents"]:
        assert source.read_doc(entry["id"]) == docs.read_doc(entry["id"])
    # A source edit is immediately visible without a package build.
    edited = AgentDocuments(tmp_path / "source/docs", source_mode=True)
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
    from lvjiang.core.agent_mcp import LV1_REQUIRED_MESSAGE

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
