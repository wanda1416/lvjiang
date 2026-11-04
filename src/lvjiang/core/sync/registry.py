"""文档 kind 声明登记（「import 即注册」，同 telemetry/config_policy 约定）。

core/sync 只认得自己拥有的四类：``user_doc``（users/{名}.json）、``notes``
（便利贴）、``profile_yaml``（账户级定义）、``avatar``。插件领域文件由插件
模块自己声明，core 不认识任何插件领域词汇，方向是插件指向 core：

- ``register_user_document_kind``：用户级文档，位于 users/ 下，按 <用户名>.json 命名；
- ``register_session_document``：账户级文档，位于会话目录的固定相对路径，
  云端 label 固定 ``__global__``（与哪个角色无关）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: core 固有 kind；插件不得抢占。
RESERVED_KINDS = frozenset({"user_doc", "notes", "profile_yaml", "avatar"})
#: 禁止登记的文件后缀（core 语义/排除项）。
_RESERVED_SUFFIXES = frozenset({".json", ".notes.json", ".session.json"})
#: 账户级文档在云端的固定 label。
GLOBAL_LABEL = "__global__"

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_SUFFIX_RE = re.compile(r"^\.[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*\.json$")
#: 会话目录内的相对路径：正斜杠分隔、不以 / 或盘符开头、逐段非空。
_REL_PATH_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+(/[A-Za-z0-9_.\-\u4e00-\u9fff]+)*$")

#: kind → 文件后缀（如 ".loadouts.json"），由注册顺序决定枚举次序。
_USER_DOCUMENT_KINDS: dict[str, str] = {}


@dataclass(frozen=True)
class SessionDocument:
    """会话目录内的一份账户级文档：本地（相对路径）+ 云端（kind 即身份）。"""

    kind: str
    rel_path: str


#: kind → 会话文档登记，由注册顺序决定枚举次序。
_SESSION_DOCUMENTS: dict[str, SessionDocument] = {}


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


def register_session_document(kind: str, rel_path: str) -> None:
    """声明一个插件的**账户级**文档：会话目录内的固定相对路径。

    与用户级 kind 的区别是归属：账户级文档与角色无关，云端只存一份
    （``label = __global__``），本地落点也是固定路径，不随用户名变化。
    重复登记同一 kind+路径幂等跳过。
    """
    if not _KIND_RE.match(kind):
        raise ValueError(f"非法文档 kind: {kind!r}（小写字母开头的 [a-z0-9_]）")
    if kind in RESERVED_KINDS:
        raise ValueError(f"kind {kind!r} 是 core 固有语义，插件不得登记")
    _check_rel_path(rel_path)
    existing = _SESSION_DOCUMENTS.get(kind)
    if existing is not None:
        if existing.rel_path == rel_path:
            return
        raise ValueError(
            f"kind {kind!r} 已登记为 {existing.rel_path!r}，不能改为 {rel_path!r}")
    if kind in _USER_DOCUMENT_KINDS:
        raise ValueError(f"kind {kind!r} 已登记为用户级文档，同一 kind 只能有一种落点")
    for other in _SESSION_DOCUMENTS.values():
        if other.rel_path == rel_path:
            raise ValueError(f"文件路径 {rel_path!r} 已被 kind {other.kind!r} 登记")
    _SESSION_DOCUMENTS[kind] = SessionDocument(kind, rel_path)


def session_documents() -> dict[str, SessionDocument]:
    """已登记的账户级文档 kind → 登记项（只读视图）。"""
    return dict(_SESSION_DOCUMENTS)


def session_document_for(kind: str) -> SessionDocument | None:
    """按 kind 反查账户级文档登记；未登记返回 None（本地没加载该插件）。"""
    return _SESSION_DOCUMENTS.get(kind)


def _check_rel_path(rel_path: str) -> None:
    if not isinstance(rel_path, str) or not rel_path:
        raise ValueError(f"非法文档路径: {rel_path!r}")
    if "\\" in rel_path or not _REL_PATH_RE.match(rel_path):
        raise ValueError(f"非法文档路径: {rel_path!r}（正斜杠分隔的相对路径）")
    segments = rel_path.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(f"非法文档路径: {rel_path!r}（不得含 . / .. 段）")
    if not rel_path.endswith(".json"):
        raise ValueError(f"非法文档路径: {rel_path!r}（仅支持 .json 文档）")
