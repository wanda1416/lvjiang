"""Read only explicitly packaged Agent documents, never the source checkout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class AgentDocuments:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _manifest(self) -> dict:
        path = self.root / "manifest.json"
        if not path.is_file():
            raise ValueError("Agent 文档包未安装，请重新安装完整发行包")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_docs(self) -> dict:
        """发现随发行版提供的文档，先读 entry，再按主题检索。"""
        return self._manifest()

    def read_doc(self, doc_id: str) -> dict:
        """按稳定文档 ID 读取包内正文；不接受磁盘路径或维护文档。"""
        entry = next((item for item in self._manifest()["documents"] if item["id"] == doc_id), None)
        if entry is None:
            raise ValueError("文档不在公开清单中")
        path = (self.root / entry["path"]).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("文档路径越界")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("文档包内容与清单不符，请重新安装")
        return {"id": doc_id, "text": data.decode("utf-8")}

    def search_docs(self, query: str) -> dict:
        """按词检索公开文档标题与正文，返回匹配片段和文档 ID。"""
        if not query.strip() or len(query) > 100:
            raise ValueError("检索词应为 1～100 字符")
        matches = []
        for entry in self._manifest()["documents"]:
            body = self.read_doc(entry["id"])["text"]
            position = body.casefold().find(query.casefold())
            if position >= 0:
                matches.append({"id": entry["id"], "excerpt": body[max(0, position - 60):position + 240]})
        return {"matches": matches[:20]}
