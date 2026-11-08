"""配置里「可本地化显示文本」的读写约定。

同一个显示字段有两种合法的值形态：

- ``str``：字面值，不随界面语言变化。用户自己起的名字（方案名、环境名、
  等待参数标签……）恒为这种形态，读写都是原样字符串。
- ``dict[str, str]``：按语言标签取值。随安装包分发的系统内容用它做中英双语，
  例如 ``{"zh_CN": "安卓", "en_US": "Android"}``。

**只有纯展示字段允许出现 dict。** 承担计算、OCR 匹配或作为稳定 id 的字段
（``plan.id``、``schools`` 的键、词条名、属性名、``affix_caps`` 的键）永远保持
单个字符串——把计算键变成随语言变化的实体，识别和判定会直接失效。

读侧用 :func:`resolve_localized` 归一成当前语言的一个字符串；写侧用
:func:`preserve_edited` / :func:`preserve_sequence` /
:func:`preserve_mapping_field` 保证「改了哪种语言就只改哪种」：

- 内容没变 → 原值原样写回，多语言键一个都不丢；
- 内容变了且原值是 dict → 只覆盖当前语言那一项；
- 原值是字面值（用户自己的）→ 始终写回单个字符串，不会被升级成 dict。

形态本身携带语义，因此不需要额外的标记位，也不需要迁移脚本：旧配置继续是
纯字符串，逐文件换成 dict 即逐文件生效。
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: 语言标签里能接受的形态：``zh_CN`` / ``zh-CN`` / ``en`` 都按同一套匹配。
FALLBACK_LANGUAGE = "zh_CN"
#: 非中文界面的兜底语言。随包 12 种语言里 8 种本身就是英文回退（见
#: ``docs/20-requirements/50-platform/01-i18n.md``），配置显示名没写该语言时
#: 跟着落到英文，界面才不会一半英文按钮、一半中文名字。
ENGLISH_FALLBACK_LANGUAGE = "en_US"

_LANGUAGE_SEP = "-"


def is_localized(value: object) -> bool:
    """值是否为「按语言取值」的映射形态。"""
    return isinstance(value, Mapping) and bool(value)


def _normalize_tag(tag: object) -> str:
    return str(tag).strip().replace(_LANGUAGE_SEP, "_")


def _current_language() -> str:
    """当前界面语言；i18n 未初始化或导入失败时按中文处理。"""
    try:
        from ...i18n import current_language
        return current_language() or FALLBACK_LANGUAGE
    except Exception:  # noqa: BLE001 纯显示回退，不能因取语言失败而炸配置读取
        return FALLBACK_LANGUAGE


def _is_chinese(language: str) -> bool:
    return _normalize_tag(language).lower().partition("_")[0] == "zh"


def _matching_key(value: Mapping, language: str) -> Any | None:
    """在映射里找对应语言的键：先精确匹配，再退到同语种其他区域。"""
    target = _normalize_tag(language).lower()
    for key in value:
        if isinstance(key, str) and _normalize_tag(key).lower() == target:
            return key
    base = target.partition("_")[0]
    if base:
        for key in value:
            if (isinstance(key, str)
                    and _normalize_tag(key).lower().partition("_")[0] == base):
                return key
    return None


def resolve_localized(
    value: object, language: str | None = None, *, fallback: str = "",
) -> str:
    """把配置里的显示文本归一成 ``language`` 下的一个字符串。

    回退链：精确语言 → 同语种其他区域（``en_GB`` 落到 ``en_US``）→
    ``en_US``（仅非中文界面）→ ``zh_CN`` → 映射里第一个非空值 → ``fallback``。

    中文界面不插英文兜底：``zh_TW`` 经同语种匹配拿到 ``zh_CN`` 的原文，
    不该被英文顶掉。非中文界面则优先英文——随包 8 种语言本身就是英文回退，
    配置名再掉回中文会与同一界面的按钮、菜单脱节。

    字面值直接返回；``None``、空映射、非字符串叶子一律返回 ``fallback``，
    调用方不需要自己做类型判断。
    """
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping) or not value:
        return fallback

    language = language or _current_language()
    candidates = [language]
    if not _is_chinese(language):
        candidates.append(ENGLISH_FALLBACK_LANGUAGE)
    candidates.append(FALLBACK_LANGUAGE)

    for tag in candidates:
        key = _matching_key(value, tag)
        if key is None:
            continue
        text = value.get(key)
        if isinstance(text, str) and text:
            return text

    for text in value.values():
        if isinstance(text, str) and text:
            return text
    return fallback


def preserve_edited(
    original: object, edited_text: object, language: str | None = None,
) -> object:
    """把一次编辑落成写回值，返回 ``str`` 或 ``dict``。

    * 编辑内容为空、或与 ``original`` 在当前语言下的取值相同 → 原值原样返回；
    * ``original`` 是字面值 → 返回编辑后的字符串（单语言，不升级成 dict）；
    * ``original`` 是 dict → 只覆盖当前语言那一项，其他语言原样保留。

    只有空白的编辑视同未修改：调用方普遍已经在取文本时 strip，这里再判一次，
    避免把「只剩空格」写进配置后渲染成一行空白。
    """
    edited = "" if edited_text is None else str(edited_text)

    if not isinstance(original, Mapping):
        if edited.strip():
            return edited
        return original if isinstance(original, str) else ""

    if not edited.strip() or edited == resolve_localized(original, language):
        return dict(original)

    language = language or _current_language()
    key = _matching_key(original, language)
    if key is None:
        key = _normalize_tag(language)
    updated = dict(original)
    updated[key] = edited
    return updated


def preserve_sequence(
    original: object,
    new_items: object,
    *,
    key_field: str,
    text_field: str,
    language: str | None = None,
) -> object:
    """列表形态的写回：按 ``key_field`` 对齐旧条目后处理 ``text_field``。

    用于 ``app.yaml`` 的 ``envs``（``key`` / ``name``）这类带稳定 key 的列表。
    """
    if not isinstance(new_items, list):
        return new_items

    previous: dict[str, Mapping] = {}
    if isinstance(original, list):
        for item in original:
            if isinstance(item, Mapping) and key_field in item:
                previous[str(item[key_field])] = item

    result: list[Any] = []
    for item in new_items:
        if not isinstance(item, Mapping) or key_field not in item or text_field not in item:
            result.append(item)
            continue
        merged = dict(item)
        base = previous.get(str(item[key_field]))
        merged[text_field] = preserve_edited(
            base.get(text_field) if isinstance(base, Mapping) else None,
            merged[text_field],
            language,
        )
        result.append(merged)
    return result


def preserve_mapping_field(
    original: object,
    new_items: object,
    *,
    text_field: str,
    language: str | None = None,
) -> object:
    """映射形态的写回：同名键对齐后处理 ``text_field``。

    用于 ``app.yaml`` 的 ``delay_params``（键为参数 key、显示名在 ``label``）。
    """
    if not isinstance(new_items, Mapping):
        return new_items

    previous = original if isinstance(original, Mapping) else {}
    result: dict[Any, Any] = {}
    for key, item in new_items.items():
        if not isinstance(item, Mapping) or text_field not in item:
            result[key] = item
            continue
        merged = dict(item)
        base = previous.get(key)
        merged[text_field] = preserve_edited(
            base.get(text_field) if isinstance(base, Mapping) else None,
            merged[text_field],
            language,
        )
        result[key] = merged
    return result
