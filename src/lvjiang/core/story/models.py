"""剧情文案库（通用工具）：多配置文件与 system/local 双层读写。

配置目录 ``tools/auto_plot/<组名>.json``，一组一个文件，便于按剧情/活动分类
维护；激活时加载**全部**组（system ∪ local，同名 local 覆盖）并合并条目。
组文件只放 ``entries``；全局参数（匹配阈值）单独存 ``_settings.json``，
``_`` 前缀的文件不会被当成配置组。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

#: 配置目录（相对配置层根）。
AUTO_PLOT_DIR = "tools/auto_plot"

#: 全局参数文件名（下划线前缀，不参与配置组枚举）。
SETTINGS_FILE = "_settings.json"

#: 默认选项集合相似度阈值；可在 _settings.json 里覆盖。
DEFAULT_THRESHOLD = 0.7


@dataclass
class StoryEntry:
    """一条剧情选项记录。"""

    id: str
    options: tuple[str, ...]
    answer: str
    question: str = ""
    scene: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        data: dict = {
            "id": self.id,
            "options": list(self.options),
            "answer": self.answer,
        }
        if self.question:
            data["question"] = self.question
        if self.scene:
            data["scene"] = self.scene
        if self.note:
            data["note"] = self.note
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> StoryEntry | None:
        if not isinstance(raw, dict):
            return None
        entry_id = str(raw.get("id") or "").strip()
        options = raw.get("options") or []
        answer = str(raw.get("answer") or "").strip()
        if not entry_id or not isinstance(options, list) or not answer:
            return None
        clean_options = tuple(str(item).strip() for item in options if str(item).strip())
        if not clean_options:
            return None
        return cls(
            id=entry_id,
            options=clean_options,
            answer=answer,
            question=str(raw.get("question") or "").strip(),
            scene=str(raw.get("scene") or "").strip(),
            note=str(raw.get("note") or "").strip(),
        )


@dataclass
class StorySettings:
    """文案库的可调参数。"""

    match_threshold: float = DEFAULT_THRESHOLD

    def to_dict(self) -> dict:
        return {"match_threshold": self.match_threshold}

    @classmethod
    def from_dict(cls, raw: dict) -> StorySettings:
        threshold = DEFAULT_THRESHOLD
        if isinstance(raw, dict):
            value = raw.get("match_threshold")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                threshold = min(1.0, max(0.0, float(value)))
        return cls(match_threshold=threshold)


@dataclass
class StoryDoc:
    """一组（或合并后）的文案库视图。"""

    entries: list[StoryEntry] = field(default_factory=list)
    settings: StorySettings = field(default_factory=StorySettings)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def _resolver():
    from lvjiang.core.config.resolver import get_resolver

    return get_resolver()


def _layer_root(layer: str) -> Path:
    resolver = _resolver()
    return resolver.system_dir if layer == "system" else resolver.local_dir


def _read_json(path: Path) -> dict | None:
    # 文件尚不存在是正常状态（还没有任何文案），不是错误。
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning(f"story: 读取 {path} 失败: {exc}")
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning(f"story: 解析 {path} 失败: {exc}")
        return None
    return data if isinstance(data, dict) else None


def _parse_entries(data: dict | None) -> list[StoryEntry]:
    if not data:
        return []
    raw_entries = data.get("entries")
    entries: list[StoryEntry] = []
    if isinstance(raw_entries, list):
        for item in raw_entries:
            entry = StoryEntry.from_dict(item)
            if entry is not None:
                entries.append(entry)
            else:
                logger.warning(f"story: 跳过非法条目 {item!r}")
    return entries


def list_groups() -> list[str]:
    """枚举全部配置组（system ∪ local，跳过 ``_`` 前缀）。"""
    names = _resolver().enumerate_entities(AUTO_PLOT_DIR, "*.json")
    return sorted(
        name[: -len(".json")]
        for name in names
        if name.lower().endswith(".json") and not name.startswith("_")
    )


def _load_layer_group(name: str, layer: str) -> StoryDoc:
    """只读指定层的组（不合并），用于增量写前取该层底稿。"""
    path = _layer_root(layer) / AUTO_PLOT_DIR / f"{name}.json"
    return StoryDoc(entries=_parse_entries(_read_json(path)))


def load_group(name: str) -> StoryDoc:
    """读取单个配置组：system 与 local 按 id 合并，local 覆盖。"""
    merged: dict[str, StoryEntry] = {}
    for entry in _load_layer_group(name, "system").entries:
        merged[entry.id] = entry
    for entry in _load_layer_group(name, "local").entries:
        merged[entry.id] = entry
    return StoryDoc(entries=list(merged.values()))


def load_all() -> StoryDoc:
    """激活：加载全部配置组并合并条目。"""
    merged: dict[str, StoryEntry] = {}
    for name in list_groups():
        for entry in load_group(name).entries:
            merged[entry.id] = entry
    return StoryDoc(entries=list(merged.values()), settings=load_settings())


def save_group(name: str, doc: StoryDoc, *, layer: str) -> Path:
    """整份写入某个配置组到指定层。"""
    text = json.dumps(doc.to_dict(), ensure_ascii=False, indent=2) + "\n"
    return _resolver().write_entity(f"{AUTO_PLOT_DIR}/{name}.json", text, layer=layer)


def delete_group(name: str, *, layer: str) -> None:
    """删除某个配置组（仅删指定层的那份）。"""
    _resolver().delete_entity(f"{AUTO_PLOT_DIR}/{name}.json", layer=layer)


def upsert_entry(entry: StoryEntry, *, group: str, layer: str) -> Path:
    """在指定组的指定层里增量更新一条（同 id 覆盖，不重复追加）。"""
    doc = _load_layer_group(group, layer)
    doc.entries = [item for item in doc.entries if item.id != entry.id] + [entry]
    return save_group(group, doc, layer=layer)


def delete_entry(entry_id: str, *, group: str, layer: str) -> Path:
    """从指定组的指定层删除一条。"""
    doc = _load_layer_group(group, layer)
    doc.entries = [item for item in doc.entries if item.id != entry_id]
    return save_group(group, doc, layer=layer)


def load_settings() -> StorySettings:
    """读取全局参数（local 优先 system）。"""
    path = _resolver().resolve_read(f"{AUTO_PLOT_DIR}/{SETTINGS_FILE}")
    data = _read_json(path) if path else None
    return StorySettings.from_dict(data or {})


def save_settings(threshold: float, *, layer: str) -> Path:
    """只更新全局参数（匹配阈值）。"""
    from lvjiang.core.json_files import formatted_json

    settings = StorySettings(match_threshold=min(1.0, max(0.0, threshold)))
    return _resolver().write_entity(
        f"{AUTO_PLOT_DIR}/{SETTINGS_FILE}",
        formatted_json(settings.to_dict()),
        layer=layer,
    )
