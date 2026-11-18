"""Read the same published documentation layers in checkouts and installations.

宿主只提供通用机制：发布哪些目录层、哪些稳定别名、清单放在哪里，都由使用方
（插件）通过 ``DocumentLayout`` 声明，core 不认识任何具体文档。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class DocumentLayout:
    """一套已发布文档的布局声明。

    - ``directories``：完整发布的目录层（保持原相对路径与正文不变）。
    - ``aliases``：常用文档的稳定 ID → 相对路径（进入列表与搜索）。
    - ``manifest``：安装包内清单文件的相对路径。
    - ``schema_id``：内容由 ``schema_provider`` 生成而非读磁盘的别名（可空）。
    - ``compat``：已退役的旧 ID → 现行 ID。旧 ID 继续可读，但不进入列表
      与搜索，避免同一正文重复出现；由插件声明，core 只做通用解析。
    - ``categories``：物理路径前缀 → 逻辑分类键。未命中的路径沿用首段
      目录名。逻辑分类与磁盘目录解耦后，旧的分类键靠它维持。
    """

    directories: tuple[str, ...]
    manifest: str
    aliases: Mapping[str, str] = field(default_factory=dict)
    schema_id: str = ""
    compat: Mapping[str, str] = field(default_factory=dict)
    categories: Mapping[str, str] = field(default_factory=dict)

    def document_id(self, relative: str) -> str:
        return next((key for key, value in self.aliases.items() if value == relative),
                    Path(relative).with_suffix("").as_posix().replace("/", ":"))

    def resolve_id(self, doc_id: str) -> str:
        """把已退役的旧 ID 解析到现行 ID；循环映射视为声明错误。"""
        seen: set[str] = set()
        current = doc_id
        while current in self.compat and current not in seen:
            seen.add(current)
            current = self.compat[current]
        if current in self.compat:
            raise ValueError("文档 ID 映射成环：" + doc_id)
        return current

    def logical_category(self, relative: str) -> str:
        """声明过的前缀取逻辑分类键，否则沿用物理首段目录。"""
        for prefix, key in sorted(self.categories.items(), key=lambda item: -len(item[0])):
            if relative == prefix or relative.startswith(prefix + "/"):
                return key
        return relative.split("/", 1)[0]

    def catalog(self, root: Path) -> dict[str, str]:
        """New Markdown chapters in published layers are available without a hand-maintained list."""
        paths = {path.relative_to(root).as_posix()
                 for directory in self.directories for path in (root / directory).rglob("*.md")}
        paths.update(self.aliases.values())
        catalog = {self.document_id(path): path for path in sorted(paths)}
        # 稳定别名与路径派生 ID 撞车会让 read_doc 的唯一性检查失义，直接拒绝。
        collisions = [key for key, path in catalog.items()
                      if key in self.aliases and self.aliases[key] != path]
        if collisions:
            raise ValueError("文档 ID 冲突：" + "、".join(sorted(collisions)))
        return catalog

    @property
    def schema_path(self) -> str:
        return str(self.aliases.get(self.schema_id, "")) if self.schema_id else ""


class AgentDocuments:
    def __init__(self, root: Path, layout: DocumentLayout, *, source_mode: bool = False,
                 schema_provider: Callable[[], str] | None = None):
        self.root = root.resolve()
        self.layout = layout
        self.source_mode = source_mode
        self.schema_provider = schema_provider

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not any(path.is_relative_to(self.root / directory)
                   for directory in self.layout.directories):
            raise ValueError("文档路径越界")
        return path

    def _source_bytes(self, relative: str) -> bytes:
        if relative and relative == self.layout.schema_path:
            if self.schema_provider is None:
                raise ValueError("工具 schema 尚未初始化")
            return self.schema_provider().encode("utf-8")
        return self._path(relative).read_bytes()

    def _manifest(self) -> dict:
        if self.source_mode:
            from ..._version import __version__
            documents = []
            for doc_id, relative in self.layout.catalog(self.root).items():
                data = self._source_bytes(relative)
                heading = next((line.lstrip("# ") for line in data.decode("utf-8").splitlines()
                                if line.startswith("# ")), "工具 schema")
                documents.append({"id": doc_id, "path": relative, "title": heading,
                                  "category": self.layout.logical_category(relative),
                                  "sha256": hashlib.sha256(data).hexdigest()})
            return {"version": __version__, "api_version": 2, "documents": documents}
        path = self.root / self.layout.manifest
        if not path.is_file():
            raise ValueError("发行文档未安装，请重新安装完整发行包")
        return json.loads(path.read_text(encoding="utf-8"))

    def list_docs(self, category: str = "") -> dict:
        """发现已发布的文档层；按逻辑分类筛选（与磁盘目录解耦）。"""
        manifest = self._manifest()
        available = sorted({entry["category"] for entry in manifest["documents"]})
        if category and category not in available:
            raise ValueError("文档分类为 " + "、".join(available))
        if category:
            manifest = {**manifest, "documents": [entry for entry in manifest["documents"]
                                                  if entry["category"] == category]}
        return manifest

    def read_doc(self, doc_id: str) -> dict:
        """按 list_docs 返回的 ID 读取正文；已退役旧 ID 经 compat 解析到现行正文。

        保持原目录归属，不接受任意磁盘路径；未登记的 ID 一律拒绝。
        """
        canonical = self.layout.resolve_id(doc_id)
        if self.source_mode:
            relative = self.layout.catalog(self.root).get(canonical)
            if relative is None:
                raise ValueError("文档不在发布目录中")
            return {"id": doc_id, "text": self._source_bytes(relative).decode("utf-8")}
        entry = next((item for item in self._manifest()["documents"] if item["id"] == canonical), None)
        if entry is None or self.layout.document_id(entry["path"]) != canonical:
            raise ValueError("文档不在发布清单中")
        data = self._path(entry["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("文档包内容与清单不符，请重新安装")
        return {"id": doc_id, "text": data.decode("utf-8")}

    def search_docs(self, query: str, category: str = "") -> dict:
        """按关键词检索已发布的文档层，可按逻辑分类收窄。"""
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
