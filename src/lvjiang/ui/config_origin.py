"""配置来源（本地 / 系统）的统一文案；在线修订由设置页单独展示。"""
from __future__ import annotations

from ..core.config.resolver import (
    LAYER_LOCAL,
    LAYER_SYSTEM,
    EntityOrigin,
)
from ..i18n import tr


def layer_label(layer: str) -> str:
    return {
        LAYER_LOCAL: tr("本地"),
        LAYER_SYSTEM: tr("系统"),
    }.get(layer, tr("未知"))


def layer_style(layer: str) -> str:
    if layer == LAYER_LOCAL:
        return "color: palette(highlight);"
    return "color: palette(mid);"


def _origin_text(origin: EntityOrigin) -> str:
    return layer_label(origin.layer)


def origin_tooltip(current: EntityOrigin,
                   available: tuple[EntityOrigin, ...]) -> str:
    """展示当前生效副本，以及所有配置层现存的版本。"""
    current_text = _origin_text(current) if current.layer else tr("无")
    source_count = len({origin.layer for origin in available})
    lines = [
        tr("当前生效：{current}").format(current=current_text),
        "",
        tr("现有版本：{count} 份，来自 {sources} 个来源").format(
            count=len(available), sources=source_count),
    ]
    for origin in available:
        marker = " ✓" if origin.layer == current.layer else ""
        lines.append(f"{_origin_text(origin)}{marker}")
    return "\n".join(lines)
