"""yysls 装备卡片展示参数。"""

from __future__ import annotations

from typing import Any

from lvjiang.core.config.session import load_settings, save_settings

DEFAULTS: dict[str, Any] = {
    "name_font_size": 13,
    "level_font_size": 12,
    "affix_font_size": 11,
    "card_min_height": 180,
    "grid_columns": 4,
}


def load_equip_display() -> dict[str, Any]:
    value = load_settings().get("equip_display")
    merged = dict(DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def save_equip_display(params: dict[str, Any]) -> None:
    save_settings({"equip_display": params})
