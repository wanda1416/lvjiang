"""按安装目录保存 MCP 接入，合并写入外部 Agent 的用户级配置。"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fasteners import InterProcessLock

from .agent_mcp import DEFAULT_MCP_PORT
from .fs_util import atomic_write_text


def installation_id(root: Path) -> str:
    return hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()[:16]


def settings_path(root: Path) -> Path:
    return Path.home() / ".lvjiang" / "mcp" / f"{installation_id(root)}.json"


@dataclass
class AgentConnection:
    port: int = DEFAULT_MCP_PORT
    enabled: bool = False
    token: str = ""
    agent_key: str = ""
    export_paths: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path) -> AgentConnection:
        path = settings_path(root)
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(data, dict):
            raise ValueError("MCP 接入配置格式无效")
        result = cls(data.get("port", DEFAULT_MCP_PORT), data.get("enabled", False),
                     data.get("token", ""), data.get("agent_key", ""), data.get("export_paths", {}))
        if type(result.port) is not int or not 1 <= result.port <= 65535 \
                or type(result.enabled) is not bool or not isinstance(result.token, str) \
                or not isinstance(result.agent_key, str) or not isinstance(result.export_paths, dict) \
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in result.export_paths.items()):
            raise ValueError("MCP 接入配置格式无效")
        return result

    def save(self, root: Path) -> None:
        from .access import is_readonly
        if is_readonly():
            raise PermissionError("只读实例不能修改 MCP 接入配置")
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        path = settings_path(root)
        atomic_write_text(path, json.dumps(asdict(self), indent=2) + "\n", prefix=".mcp-")
        if os.name != "nt":
            path.chmod(0o600)


def export_agent(root: Path, config: dict, path: Path, *, transport: str = "streamableHttp") -> Path:
    """共同的 JSON 配置合并；各客户端由自己的适配器选择目标文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = InterProcessLock(str(path) + ".lvjiang.lock")
    with lock:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            raise ValueError("Agent 配置格式无效，未覆盖原文件")
        servers = dict(data.get("mcpServers", {}))
        servers[f"lvjiang-{installation_id(root)}"] = {**config["mcpServers"]["lvjiang"], "type": transport}
        data["mcpServers"] = servers
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", prefix=".mcp-")
        if os.name != "nt":
            path.chmod(0o600)
    return path
