"""剧情文案库（通用工具）：多配置文件与 system/local 双层读写。

配置目录 ``tools/auto_plot/<组名>.json``，一组一个文件，便于按剧情/活动分类
维护；激活时加载**全部**组（system ∪ local，同名 local 覆盖）并合并条目。
每个文件自带 ``entries`` 与 ``match_threshold``，**一个文件就是一个可搬运的
完整文案库**（连同它自己的匹配阈值）；``_`` 前缀的文件不会被当成配置文件。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

#: 配置目录（相对配置层根）。
AUTO_PLOT_DIR = "tools/auto_plot"

#: 全局参数文件名（下划线前缀，不参与配置组枚举）。


#: 默认选项集合相似度阈值；每个配置文件可以用 ``match_threshold`` 覆盖自己的。
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


def _parse_threshold(data: dict | None) -> float:
    """取某个文件的匹配阈值；字段缺失或非法时回落默认值。"""
    if not isinstance(data, dict):
        return DEFAULT_THRESHOLD
    value = data.get("match_threshold")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _clamp_threshold(float(value))
    return DEFAULT_THRESHOLD


def _clamp_threshold(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


@dataclass
class StoryDoc:
    """一组（或合并后）的文案库视图。"""

    entries: list[StoryEntry] = field(default_factory=list)
    #: 本文件的匹配阈值。单文件视图（load_group）直接读它；写盘时一并带上，
    #: 所以一个 ``<文件>.json`` 搬到别的机器也是完整可用的。
    match_threshold: float = DEFAULT_THRESHOLD
    #: 合并视图（load_all）里每条条目**来源文件**的阈值：entry id → threshold。
    thresholds: dict[str, float] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "match_threshold": self.match_threshold,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    def threshold_for(self, entry: StoryEntry) -> float:
        """这条条目该用哪个阈值：合并视图按来源文件，单文件视图用本文件的。"""
        return self.thresholds.get(entry.id, self.match_threshold)


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
    data = _read_json(path)
    return StoryDoc(
        entries=_parse_entries(data),
        match_threshold=_parse_threshold(data),
    )


def load_group(name: str) -> StoryDoc:
    """读取单个文件：system 与 local 按 id 合并条目，local 覆盖。

    阈值跟随"存在该文件的那一层"：只有 local 有就用 local 的，两层都有仍以
    local 为准（与条目覆盖规则一致）。
    """
    merged: dict[str, StoryEntry] = {}
    threshold = DEFAULT_THRESHOLD
    for layer in ("system", "local"):
        path = _layer_root(layer) / AUTO_PLOT_DIR / f"{name}.json"
        if not path.exists():
            continue
        doc = _load_layer_group(name, layer)
        threshold = doc.match_threshold
        for entry in doc.entries:
            merged[entry.id] = entry
    return StoryDoc(entries=list(merged.values()), match_threshold=threshold)


def load_all() -> StoryDoc:
    """激活：加载全部文件并合并条目，同时记住每条条目来源文件的阈值。

    阈值随文件走之后，"同一画面命中哪条用哪个阈值"必须按来源判——否则 A 文件
    调到 0.6、B 文件保持 0.9 就互相污染了。
    """
    merged: dict[str, StoryEntry] = {}
    thresholds: dict[str, float] = {}
    for name in list_groups():
        doc = load_group(name)
        for entry in doc.entries:
            merged[entry.id] = entry
            thresholds[entry.id] = doc.match_threshold
    return StoryDoc(entries=list(merged.values()), thresholds=thresholds)


def save_group(name: str, doc: StoryDoc, *, layer: str) -> Path:
    """整份写入某个配置组到指定层。"""
    text = json.dumps(doc.to_dict(), ensure_ascii=False, indent=2) + "\n"
    return _resolver().write_entity(f"{AUTO_PLOT_DIR}/{name}.json", text, layer=layer)


def delete_group(name: str, *, layer: str) -> None:
    """删除某个配置组（仅删指定层的那份）。"""
    _resolver().delete_entity(f"{AUTO_PLOT_DIR}/{name}.json", layer=layer)


def upsert_entry(entry: StoryEntry, *, group: str, layer: str,
                 match_threshold: float | None = None) -> Path:
    """在指定组的指定层里增量更新一条（同 id 覆盖，不重复追加）。

    ``match_threshold`` 给了就顺手写进同一个文件，避免"保存文案"和"保存阈值"
    变成两次落盘、两个可能失败的中间态。
    """
    doc = _load_layer_group(group, layer)
    doc.entries = [item for item in doc.entries if item.id != entry.id] + [entry]
    if match_threshold is not None:
        doc.match_threshold = _clamp_threshold(match_threshold)
    return save_group(group, doc, layer=layer)


def set_group_threshold(group: str, threshold: float, *, layer: str) -> Path:
    """只改某个文件的匹配阈值（不动条目）。"""
    doc = _load_layer_group(group, layer)
    doc.match_threshold = _clamp_threshold(threshold)
    return save_group(group, doc, layer=layer)


def delete_entry(entry_id: str, *, group: str, layer: str) -> Path:
    """从指定组的指定层删除一条。"""
    doc = _load_layer_group(group, layer)
    doc.entries = [item for item in doc.entries if item.id != entry_id]
    return save_group(group, doc, layer=layer)



