"""外部 Agent 的全局目录；名称、路径和下载入口以 app.yaml 为准。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config.resolver import load_app_config


@dataclass(frozen=True)
class AgentTarget:
    key: str
    name: str
    export_path: str
    transport: str = "streamable-http"

    @property
    def path(self) -> Path:
        return Path(self.export_path).expanduser()


@dataclass(frozen=True)
class AgentSettings:
    items: tuple[AgentTarget, ...] = ()
    download_url: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> AgentSettings:
        if not isinstance(data, dict) or not isinstance(data.get("items", []), list):
            raise ValueError("Agent 配置格式无效")
        items = []
        keys = set()
        for item in data.get("items", []):
            if not isinstance(item, dict) or any(not isinstance(item.get(field), str)
                                                 or not item[field].strip()
                                                 for field in ("key", "name", "export_path")):
                raise ValueError("请填写 Agent 名称和导出文件路径")
            key = item["key"].strip()
            transport = item.get("transport", "streamable-http")
            if key in keys or transport not in {"streamable-http", "streamableHttp", "http"}:
                raise ValueError("Agent 标识重复或接入格式无效")
            # 这些值是同一协议的客户端拼写，读取旧配置后统一为标准名称。
            target = AgentTarget(key, item["name"].strip(), item["export_path"].strip())
            if not target.path.is_absolute():
                raise ValueError("Agent 导出路径需为绝对路径或以 ~/ 开头")
            keys.add(key)
            items.append(target)
        download = data.get("download_agent", {})
        if not isinstance(download, dict) or not isinstance(download.get("url", ""), str):
            raise ValueError("Agent 下载链接格式无效")
        url = download.get("url", "").strip()
        if url and (urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc):
            raise ValueError("Agent 下载链接需为 HTTP 或 HTTPS 地址")
        return cls(tuple(items), url)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return {"items": [asdict(item) for item in self.items],
                "download_agent": {"url": self.download_url}}


def load_agent_settings() -> AgentSettings:
    return AgentSettings.from_dict(load_app_config().get("agents", {}))
