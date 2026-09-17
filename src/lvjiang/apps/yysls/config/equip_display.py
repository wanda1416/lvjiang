"""yysls 装备卡片展示参数。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "name_font_size": 13,
    "level_font_size": 12,
    "affix_font_size": 11,
    "card_min_height": 180,
    "grid_columns": 4,
}


def load_equip_display() -> dict[str, Any]:
    from .manager import get_game_config

    value = get_game_config().get_raw().get("equip_display")
    merged = dict(DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def save_equip_display(params: dict[str, Any]) -> None:
    from .manager import get_game_config

    manager = get_game_config()
    data = manager.get_raw()
    data["equip_display"] = dict(params)
    manager.save(data)
