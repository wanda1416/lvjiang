"""yysls 装备卡片展示参数（字号、卡片高度、网格列数）。

这是本机的界面偏好，不是游戏规则：存在 session.json 的 ``settings.equip_display``
节点，不进游戏配置 YAML（那套按 system/local/remote 分层分发的是游戏事实）。
默认值只在代码里给一份，缺失项按默认补齐。
"""

from __future__ import annotations

from typing import Any

from lvjiang.core.config.session import load_settings, save_settings

DEFAULTS: dict[str, Any] = {
    "name_font_size": 13,
    "level_font_size": 12,
    "affix_font_size": 12,
    "card_min_height": 180,
    "grid_columns": 4,
}


def load_equip_display() -> dict[str, Any]:
    value = load_settings().get("equip_display")
    merged = dict(DEFAULTS)
    if isinstance(value, dict):
        merged.update({key: value[key] for key in DEFAULTS if key in value})
    return merged


def save_equip_display(params: dict[str, Any]) -> None:
    save_settings({"equip_display": {
        key: params[key] for key in DEFAULTS if key in params}})
