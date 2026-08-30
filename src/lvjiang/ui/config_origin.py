"""配置来源（本地 / 系统 / 远程）的统一展示文案

场景编辑器和调律规则页都要标「这份配置来自哪一层、是第几版」。两边各写
一套必然漂移，而这块信息恰恰是最不能含糊的：远程下发会**顶替**系统那一份，
不说清楚的话，开发者看到的和线上真正生效的不是同一份内容却毫不知情。

只放纯文案与配色，不碰 resolver 的判定逻辑——层名由 ``describe_entity``
给出，这里只负责把它翻译成人能读懂的一句话。
"""
from __future__ import annotations

from ..core.config.resolver import (
    LAYER_LOCAL,
    LAYER_REMOTE,
    LAYER_SYSTEM,
    EntityOrigin,
)
from ..i18n import tr

#: 远程来源用醒目的琥珀色——它是唯一一种「你看到的不是仓库里那份」的状态
_REMOTE_COLOR = "#D97706"


def layer_label(layer: str) -> str:
    return {
        LAYER_LOCAL: tr("本地"),
        LAYER_SYSTEM: tr("系统"),
        LAYER_REMOTE: tr("远程"),
    }.get(layer, tr("未知"))


def layer_style(layer: str) -> str:
    if layer == LAYER_REMOTE:
        return f"color: {_REMOTE_COLOR};"
    if layer == LAYER_LOCAL:
        return "color: palette(highlight);"
    return "color: palette(mid);"


def _origin_text(origin: EntityOrigin) -> str:
    version = "-" if origin.version is None else f"v{origin.version}"
    return f"{layer_label(origin.layer)} · {version}"


def origin_tooltip(current: EntityOrigin,
                   available: tuple[EntityOrigin, ...],
                   pending: int | None = None) -> str:
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
    if pending is not None:
        lines.extend(("", tr("待保存：系统将提升至 v{version}")
                      .format(version=pending)))
    return "\n".join(lines)
