"""Shared conversion from the live role panel to stored base attributes."""
from __future__ import annotations

from .combat_attrs import (
    PENETRATION_FIELDS,
    PLAY_STYLE_FIELD_GROUPS,
    SCHOOL_ATTR_FIELD_MAP,
    CombatAttributes,
)


def derive_base_attributes(
    panel_attrs: CombatAttributes,
    equipped: dict,
    gongjue_contribution: CombatAttributes,
) -> CombatAttributes:
    """Subtract real equipment and gongjue, never simulation assumptions."""
    from ...config import get_game_config
    from ..graduation.scoring import equipment_attrs

    base = (panel_attrs - equipment_attrs(equipped, get_game_config())
            - gongjue_contribution)
    for field in PENETRATION_FIELDS:
        setattr(base, field, getattr(panel_attrs, field))
    return base


def stored_base_fields(school_attr: str, base_attrs: CombatAttributes) -> dict[str, float]:
    """Use the same configured input fields as the manual creation form."""
    mapping = SCHOOL_ATTR_FIELD_MAP.get(school_attr, {})
    result: dict[str, float] = {}
    placeholders = {
        "__attr_pen__": "attr_pen",
        "__attr_bonus__": "attr_bonus",
        "__min_attr__": "min_attr",
        "__max_attr__": "max_attr",
    }
    for _, group in PLAY_STYLE_FIELD_GROUPS:
        for field, _, _ in group:
            field = mapping.get(placeholders[field], "") if field in placeholders else field
            if field:
                value = getattr(base_attrs, field, 0.0)
                if value:
                    result[field] = float(value)
    return result
