"""Read the same published documentation layers in checkouts and installations."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

# Publish complete layers without relocating their body text or changing their relative paths.
DOCUMENT_DIRECTORIES = ("10-game", "30-architecture", "60-userguide", "70-agent")

# Keep convenient stable IDs for the Agent's entry and most-used business documents.
AGENT_DOCUMENTS = {
    "entry": "70-agent/README.md",
    "permissions": "70-agent/10-contracts/01-context-and-permissions.md",
    "analysis": "70-agent/20-operations/01-analysis.md",
    "generation": "70-agent/20-operations/02-generation.md",
    "workbuddy": "70-agent/30-examples/01-workbuddy.md",
    "equipment": "10-game/01-equipment-system.md",
    "schools": "10-game/02-school-system.md",
    "damage": "10-game/03-damage-mechanics.md",
    "domain": "10-game/README.md",
    "rules": "10-game/10-tuning-rules/README.md",
    "huiyi": "10-game/10-tuning-rules/01-huiyi.md",
    "huixin-big": "10-game/10-tuning-rules/02-huixin-big.md",
    "huixin-small": "10-game/10-tuning-rules/03-huixin-small.md",
    "heal-pure": "10-game/10-tuning-rules/04-heal-pure.md",
    "heal-fire": "10-game/10-tuning-rules/05-heal-fire.md",
    "modao": "10-game/10-tuning-rules/06-weiwei-dawang.md",
    "userguide": "60-userguide/README.md",
    "architecture": "30-architecture/README.md",
    "dsl": "30-architecture/32-grammar/README.md",
    "tool-schema": "70-agent/schemas/tools.json",
}


def document_id(relative: str) -> str:
    return next((key for key, value in AGENT_DOCUMENTS.items() if value == relative),
                Path(relative).with_suffix("").as_posix().replace("/", ":"))


def document_catalog(root: Path) -> dict[str, str]:
    """New Markdown chapters in published layers are available without a hand-maintained list."""
    paths = {path.relative_to(root).as_posix()
             for directory in DOCUMENT_DIRECTORIES for path in (root / directory).rglob("*.md")}
    paths.update(AGENT_DOCUMENTS.values())
    return {document_id(path): path for path in sorted(paths)}


class AgentDocuments:
    def __init__(self, root: Path, *, source_mode: bool = False,
                 schema_provider: Callable[[], str] | None = None):
        self.root = root.resolve()
        self.source_mode = source_mode
        self.schema_provider = schema_provider

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not any(path.is_relative_to(self.root / directory) for directory in DOCUMENT_DIRECTORIES):
            raise ValueError("文档路径越界")
        return path

    def _source_bytes(self, relative: str) -> bytes:
        if relative == AGENT_DOCUMENTS["tool-schema"]:
            if self.schema_provider is None:
                raise ValueError("工具 schema 尚未初始化")
            return self.schema_provider().encode("utf-8")
        return self._path(relative).read_bytes()

    def _manifest(self) -> dict:
        if self.source_mode:
            from .._version import __version__
            documents = []
            for doc_id, relative in document_catalog(self.root).items():
                data = self._source_bytes(relative)
                heading = next((line.lstrip("# ") for line in data.decode("utf-8").splitlines()
                                if line.startswith("# ")), "工具 schema")
                documents.append({"id": doc_id, "path": relative, "title": heading,
                                  "category": relative.split("/", 1)[0],
                                  "sha256": hashlib.sha256(data).hexdigest()})
            return {"version": __version__, "api_version": 2, "documents": documents}
        path = self.root / "70-agent/manifest.json"
        if not path.is_file():
            raise ValueError("发行文档未安装，请重新安装完整发行包")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_docs(self, category: str = "") -> dict:
        """发现游戏机制、架构/DSL、用户指南与 Agent 契约；可按原目录分类筛选。"""
        if category and category not in DOCUMENT_DIRECTORIES:
            raise ValueError("文档分类为 10-game、30-architecture、60-userguide、70-agent")
        manifest = self._manifest()
        if category:
            manifest = {**manifest, "documents": [entry for entry in manifest["documents"]
                                                  if entry["category"] == category]}
        return manifest

    def read_doc(self, doc_id: str) -> dict:
        """按 list_docs 返回的 ID 读取正文；保持原目录归属，不接受任意磁盘路径。"""
        if self.source_mode:
            relative = document_catalog(self.root).get(doc_id)
            if relative is None:
                raise ValueError("文档不在发布目录中")
            return {"id": doc_id, "text": self._source_bytes(relative).decode("utf-8")}
        entry = next((item for item in self._manifest()["documents"] if item["id"] == doc_id), None)
        if entry is None or document_id(entry["path"]) != doc_id:
            raise ValueError("文档不在发布清单中")
        data = self._path(entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("文档包内容与清单不符，请重新安装")
        return {"id": doc_id, "text": data.decode("utf-8")}

    def search_docs(self, query: str, category: str = "") -> dict:
        """按关键词检索四个发布层；操作问题查用户指南，DSL 问题查架构文档。"""
        if not query.strip() or len(query) > 100:
            raise ValueError("检索词应为 1～100 字符")
        matches = []
        for entry in self.list_docs(category)["documents"]:
            body = self.read_doc(entry["id"])["text"]
            position = body.casefold().find(query.casefold())
            if position >= 0:
                matches.append({"id": entry["id"], "title": entry["title"], "category": entry["category"],
                                "excerpt": body[max(0, position - 60):position + 240]})
        return {"matches": matches[:20]}
