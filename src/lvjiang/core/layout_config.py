"""``layouts.yaml`` v2 manifest parsing and persistence.

Layout keys are stable storage identities.  Display names are metadata and must
never be used to construct paths or persisted references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config.resolver import ConfigResolver, get_resolver

LAYOUTS_SCHEMA_VERSION = 2
LAYOUT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class LayoutEntry:
    key: str
    name: str
    desc: str
    canvas: dict
    extends: str = ""


def validate_layout_key(key: str) -> str:
    value = str(key).strip()
    if LAYOUT_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "布局 key 必须以小写字母开头，且只能包含小写字母、数字和下划线"
        )
    return value


def parse_layout_entries(doc: dict) -> dict[str, LayoutEntry]:
    if not isinstance(doc, dict):
        raise ValueError("layouts.yaml 根节点必须是映射")
    version = doc.get("schema_version")
    if version != LAYOUTS_SCHEMA_VERSION or isinstance(version, bool):
        raise ValueError(
            "layouts.yaml 仅支持 schema_version: 2，旧布局配置不再兼容"
        )
    raw_layouts = doc.get("layouts")
    if not isinstance(raw_layouts, dict):
        raise ValueError("layouts.yaml 的 layouts 必须是映射")

    entries: dict[str, LayoutEntry] = {}
    names: set[str] = set()
    for raw_key, raw_entry in raw_layouts.items():
        if not isinstance(raw_key, str):
            raise ValueError("布局 key 必须是字符串")
        key = validate_layout_key(raw_key)
        if key != raw_key:
            raise ValueError(f"布局 key 不能包含首尾空白: {raw_key!r}")
        if not isinstance(raw_entry, dict):
            raise ValueError(f"布局 {key} 的定义必须是映射")
        name = raw_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"布局 {key} 必须声明非空 name")
        if name != name.strip():
            raise ValueError(f"布局 {key} 的 name 不能包含首尾空白")
        if name in names:
            raise ValueError(f"布局名称重复: {name}")
        names.add(name)
        desc = raw_entry.get("desc", "")
        if not isinstance(desc, str):
            raise ValueError(f"布局 {key} 的 desc 必须是字符串")
        canvas = raw_entry.get("canvas", {})
        if not isinstance(canvas, dict):
            raise ValueError(f"布局 {key} 的 canvas 必须是映射")
        extends = raw_entry.get("extends", "")
        if not isinstance(extends, str):
            raise ValueError(f"布局 {key} 的 extends 必须是字符串")
        if extends:
            normalized_parent = validate_layout_key(extends)
            if normalized_parent != extends:
                raise ValueError(
                    f"布局 {key} 的 extends 不能包含首尾空白")
        entries[key] = LayoutEntry(key, name, desc, canvas, extends)

    for entry in entries.values():
        if not entry.extends:
            continue
        if entry.extends == entry.key:
            raise ValueError(f"布局 {entry.key} 不能继承自身")
        parent = entries.get(entry.extends)
        if parent is None:
            raise ValueError(
                f"布局 {entry.key} 的 extends 目标不存在: {entry.extends}"
            )
        if parent.extends:
            raise ValueError(
                f"布局 {entry.key} 的 extends 只能指向根布局，禁止多级继承"
            )
    return entries


def load_layout_doc(resolver: ConfigResolver | None = None) -> dict:
    resolver = resolver or get_resolver()
    doc = resolver.load_merged("layouts.yaml")
    if not doc:
        return {"schema_version": LAYOUTS_SCHEMA_VERSION, "layouts": {}}
    parse_layout_entries(doc)
    return doc


def load_layout_entries(
    resolver: ConfigResolver | None = None,
) -> dict[str, LayoutEntry]:
    return parse_layout_entries(load_layout_doc(resolver))


def save_layout_doc(doc: dict, resolver: ConfigResolver | None = None) -> None:
    resolver = resolver or get_resolver()
    parse_layout_entries(doc)
    resolver.save_merged("layouts.yaml", doc)
