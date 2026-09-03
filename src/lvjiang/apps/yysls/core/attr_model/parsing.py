"""属性来源 YAML 的解析与 schema 校验

每个来源类别一个文件，文件内 schema 统一，于是新增一类来源不需要
新增解析代码：

.. code-block:: yaml

    kind: inner_way
    entries:
      易水歌·二重:
        full_affix: 外功攻击      # 一整条词条，数值从 affix_caps 生成
      易水歌·五重:
        stats:
          direct_crit: 0.046      # 常数
      罗汉伏魔·外功增幅:
        stats:
          min_outer:              # 公式：敏 → 外功攻击，上限 73.9
            formula: { source: dim_min, multiplier: 0.2639, max: 73.9 }
      八珍玉食:
        scope: combat             # 只在战斗内生效，不进角色面板
        stats: { min_outer: 120, max_outer: 240 }
      尚未测量的条目:
        modeled: false            # 登记但未填，贡献 0，由反解兜底

校验从严：未知字段名、未知词条类别、非法作用域一律解析失败，不静默
跳过——静默跳过会让面板对账在几十个来源里无从定位。
"""

from __future__ import annotations

from typing import Any

from .....i18n import tr
from .models import (
    DEFAULT_AFFIX_SPLIT,
    SCOPES,
    SOURCE_KINDS,
    AttrModelError,
    Formula,
    FullAffix,
    StatEffect,
)

#: entries 条目内允许的键
_ENTRY_KEYS = {
    "label", "scope", "stats", "extra", "full_affix", "split", "modeled",
    "no_effect",
}

#: 公式内允许的键
_FORMULA_KEYS = {"source", "multiplier", "offset", "min", "max", "round"}


def _require_mapping(value: Any, what: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AttrModelError(
            tr("{what} 必须是映射，实际是 {kind}").format(
                what=what, kind=type(value).__name__
            )
        )
    return value


def _parse_number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AttrModelError(
            tr("{what} 必须是数值，实际是 {kind}").format(
                what=what, kind=type(value).__name__
            )
        )
    return float(value)


def parse_formula(raw: Any, what: str) -> Formula:
    data = _require_mapping(raw, what)
    unknown = set(data) - _FORMULA_KEYS
    if unknown:
        raise AttrModelError(
            tr("{what} 含未知公式字段: {keys}").format(
                what=what, keys="、".join(sorted(unknown))
            )
        )
    source = data.get("source")
    if not isinstance(source, str) or not source:
        raise AttrModelError(tr("{what} 缺少 source").format(what=what))
    ndigits = data.get("round")
    if ndigits is not None and (isinstance(ndigits, bool) or not isinstance(ndigits, int)):
        raise AttrModelError(tr("{what} 的 round 必须是整数").format(what=what))
    return Formula(
        source=source,
        multiplier=_parse_number(data.get("multiplier", 1.0), f"{what}.multiplier"),
        offset=_parse_number(data.get("offset", 0.0), f"{what}.offset"),
        minimum=(
            None if data.get("min") is None
            else _parse_number(data["min"], f"{what}.min")
        ),
        maximum=(
            None if data.get("max") is None
            else _parse_number(data["max"], f"{what}.max")
        ),
        ndigits=ndigits,
    )


def _parse_split(raw: Any, what: str) -> tuple[int, int]:
    if raw is None:
        return DEFAULT_AFFIX_SPLIT
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != 2
        or any(isinstance(part, bool) or not isinstance(part, int) for part in raw)
    ):
        raise AttrModelError(
            tr("{what} 的 split 必须是两个整数，如 [1, 2]").format(what=what)
        )
    return int(raw[0]), int(raw[1])


def parse_entry(source_id: str, raw: Any, kind: str) -> StatEffect:
    """解析单个来源条目"""
    data = _require_mapping(raw, source_id)
    unknown = set(data) - _ENTRY_KEYS
    if unknown:
        raise AttrModelError(
            tr("{source} 含未知字段: {keys}").format(
                source=source_id, keys="、".join(sorted(unknown))
            )
        )

    scope = data.get("scope", SCOPES[0])
    if scope not in SCOPES:
        raise AttrModelError(
            tr("{source} 的 scope 只能是 {allowed}").format(
                source=source_id, allowed="、".join(SCOPES)
            )
        )

    label = data.get("label") or source_id
    if not isinstance(label, str):
        raise AttrModelError(tr("{source} 的 label 必须是字符串").format(source=source_id))

    stats: dict[str, float | Formula] = {}
    for name, value in _require_mapping(data.get("stats"), f"{source_id}.stats").items():
        what = f"{source_id}.stats.{name}"
        if isinstance(value, dict):
            if "formula" not in value:
                raise AttrModelError(
                    tr("{what} 是映射时必须含 formula").format(what=what)
                )
            stats[name] = parse_formula(value["formula"], what)
        else:
            stats[name] = _parse_number(value, what)

    extra: dict[str, float] = {}
    for name, value in _require_mapping(data.get("extra"), f"{source_id}.extra").items():
        extra[name] = _parse_number(value, f"{source_id}.extra.{name}")

    full_affix = None
    raw_affix = data.get("full_affix")
    if raw_affix is not None:
        if not isinstance(raw_affix, str) or not raw_affix:
            raise AttrModelError(
                tr("{source} 的 full_affix 必须是词条类别名").format(source=source_id)
            )
        full_affix = FullAffix(
            category=raw_affix,
            split=_parse_split(data.get("split"), source_id),
        )
    elif "split" in data:
        raise AttrModelError(
            tr("{source} 声明了 split 却没有 full_affix").format(source=source_id)
        )

    modeled = data.get("modeled", True)
    if not isinstance(modeled, bool):
        raise AttrModelError(
            tr("{source} 的 modeled 必须是布尔值").format(source=source_id)
        )
    no_effect = data.get("no_effect", False)
    if not isinstance(no_effect, bool):
        raise AttrModelError(
            tr("{source} 的 no_effect 必须是布尔值").format(source=source_id)
        )
    has_values = bool(stats or extra or full_affix)
    if no_effect and has_values:
        raise AttrModelError(
            tr("{source} 既声明无贡献又填了数值，取哪个无从判断").format(
                source=source_id
            )
        )
    # 什么都没填的条目视作未建模，避免「填了个空壳却当成已完成」
    if modeled and not has_values:
        modeled = False

    return StatEffect(
        source_id=source_id,
        label=label,
        kind=kind,
        scope=scope,
        stats=stats,
        full_affix=full_affix,
        extra=extra,
        modeled=modeled,
        no_effect=no_effect,
    )


def parse_source_file(data: Any, *, filename: str) -> list[StatEffect]:
    """解析一个来源文件，返回其中的全部条目"""
    payload = _require_mapping(data, filename)
    kind = payload.get("kind")
    if kind not in SOURCE_KINDS:
        raise AttrModelError(
            tr("{filename} 的 kind 无效: {kind}；可选 {allowed}").format(
                filename=filename, kind=kind, allowed="、".join(SOURCE_KINDS)
            )
        )
    entries = _require_mapping(payload.get("entries"), f"{filename}.entries")
    return [
        parse_entry(str(source_id), raw, kind)
        for source_id, raw in entries.items()
    ]
