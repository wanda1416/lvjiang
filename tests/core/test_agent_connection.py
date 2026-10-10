"""接入跨重启保持稳定，多安装与其他 Agent 配置互不覆盖。"""
import json

import pytest

from lvjiang.core import agent_connection, agent_mcp
from lvjiang.core.agent_connection import (
    AgentConnection,
    export_agent,
)


def test_connection_persists_enabled_port_token_and_isolates_installations(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_connection.Path, "home", lambda: tmp_path / "home")
    first, second = tmp_path / "first", tmp_path / "second"
    setting = AgentConnection(port=19001, enabled=True)
    setting.save(first)
    loaded = AgentConnection.load(first)
    assert loaded == setting and loaded.token
    assert AgentConnection.load(second).port == agent_mcp.DEFAULT_MCP_PORT
    loaded.enabled = False
    loaded.save(first)
    assert not AgentConnection.load(first).enabled
    assert AgentConnection.load(first).token == setting.token


def test_exports_preserve_other_settings_and_use_configured_connection_names(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"custom": True, "mcpServers": {"other": {"command": "other"}}}), encoding="utf-8")
    config = {"mcpServers": {"lvjiang": {"type": "streamableHttp", "url": "http://127.0.0.1:19001/mcp"}}}
    export_agent(config, path, server_name="律匠(lvjiang)")
    export_agent(config, path, server_name="律匠二号(lvjiang-2)")
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["custom"] is True
    assert result["mcpServers"]["other"] == {"command": "other"}
    assert set(result["mcpServers"]) == {"other", "律匠(lvjiang)",
                                          "律匠二号(lvjiang-2)"}
    qoder = tmp_path / "qoderwork/mcp.json"
    qoder.parent.mkdir()
    qoder.write_bytes(path.read_bytes())
    export_agent(config, qoder, server_name="律匠(lvjiang)", transport="streamable-http")
    qoder_result = json.loads(qoder.read_text(encoding="utf-8"))
    assert qoder_result["mcpServers"]["other"] == {"command": "other"}
    assert qoder_result["mcpServers"]["律匠(lvjiang)"]["type"] == "streamable-http"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        export_agent(config, path, server_name="律匠(lvjiang)")
    assert path.read_text(encoding="utf-8") == "broken"


def test_readonly_and_occupied_fixed_port_do_not_start_or_choose_another(tmp_path, monkeypatch):
    import socket

    from lvjiang.core import access
    monkeypatch.setattr(access, "is_readonly", lambda: True)
    with pytest.raises(PermissionError, match="主实例"):
        agent_mcp.LocalMCPServer({}, instructions="test").start()
    monkeypatch.setattr(access, "is_readonly", lambda: False)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        server = agent_mcp.LocalMCPServer({}, instructions="test", port=port)
        with pytest.raises(OSError, match="手动修改端口"):
            server.start()
        assert server.port == port and not server.running
