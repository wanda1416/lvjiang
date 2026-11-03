"""用户级文档 kind 声明登记（「import 即注册」，同 telemetry/config_policy 约定）。

core/sync 只认得自己拥有的四类：``user_doc``（users/{名}.json）、``notes``
（便利贴）、``profile_yaml``（账户级定义）、``avatar``。插件领域文件（如 yysls
的出装 ``.loadouts.json``）由插件模块调用 ``register_user_document_kind``
声明 kind 与文件后缀——core 不认识任何插件领域词汇，方向是插件指向 core。
"""
from __future__ import annotations

import re

#: core 固有 kind；插件不得抢占。
RESERVED_KINDS = frozenset({"user_doc", "notes", "profile_yaml", "avatar"})
#: 禁止登记的文件后缀（core 语义/排除项）。
_RESERVED_SUFFIXES = frozenset({".json", ".notes.json", ".session.json"})

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*\.json$")

#: kind → 文件后缀（如 ".loadouts.json"），由注册顺序决定枚举次序。
_USER_DOCUMENT_KINDS: dict[str, str] = {}


def register_user_document_kind(kind: str, suffix: str) -> None:
    """声明一个插件的用户级文档 kind；重复登记同一 kind+suffix 幂等跳过。"""
    if not _KIND_RE.match(kind):
        raise ValueError(f"非法文档 kind: {kind!r}（小写字母开头的 [a-z0-9_]）")
    if kind in RESERVED_KINDS:
        raise ValueError(f"kind {kind!r} 是 core 固有语义，插件不得登记")
    if not _SUFFIX_RE.match(suffix) or suffix in _RESERVED_SUFFIXES:
        raise ValueError(f"非法文件后缀: {suffix!r}")
    existing = _USER_DOCUMENT_KINDS.get(kind)
    if existing is not None:
        if existing == suffix:
            return
        raise ValueError(f"kind {kind!r} 已登记为 {existing!r}，不能改为 {suffix!r}")
    if suffix in _USER_DOCUMENT_KINDS.values():
        raise ValueError(f"文件后缀 {suffix!r} 已被其他 kind 登记")
    _USER_DOCUMENT_KINDS[kind] = suffix


def user_document_kinds() -> dict[str, str]:
    """已登记的插件 kind → 后缀映射（只读视图）。"""
    return dict(_USER_DOCUMENT_KINDS)
