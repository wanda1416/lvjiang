"""装备槽位唯一定义：八个槽位、七个部位，主副武器同属“武器”。"""
from __future__ import annotations

from lvjiang.apps.yysls.config.equipment_slots import (
    EQUIPMENT_SLOTS,
    SLOT_BY_KEY,
    SLOT_LABELS,
    SLOT_SPECS,
    WEAPON_SLOTS,
    grid_layout,
    slot_part,
)


def test_eight_slots_seven_parts_two_weapon_slots():
    assert len(SLOT_SPECS) == 8 and len(EQUIPMENT_SLOTS) == 8
    parts = {spec.part for spec in SLOT_SPECS}
    assert len(parts) == 7
    assert WEAPON_SLOTS == ("main_weapon", "sub_weapon")
    assert slot_part("main_weapon") == slot_part("sub_weapon") == "武器"
    assert SLOT_BY_KEY["main_weapon"].filter_type == SLOT_BY_KEY["sub_weapon"].filter_type == "weapon"
    assert slot_part("ring") == "环" and slot_part("unknown") == ""


def test_grid_is_four_by_two_without_gaps():
    cells = {(row, col) for row, col, _key in grid_layout()}
    assert cells == {(r, c) for r in range(2) for c in range(4)}


def test_every_derived_constant_agrees_with_the_spec():
    from lvjiang.apps.yysls.config.tune_slots import SLOT_GROUPS
    from lvjiang.apps.yysls.config.tune_slots import SLOT_LABELS as TUNE_LABELS
    from lvjiang.apps.yysls.core.graduation.optimal_combo import SLOT_KEYS
    from lvjiang.apps.yysls.core.loadout.models import EQUIPMENT_SLOTS as CORE_SLOTS
    from lvjiang.apps.yysls.ui.loadout.affix_analysis_pages import DEFAULT_SLOT_LAYOUT
    from lvjiang.apps.yysls.ui.loadout.equip.status_tab import _SLOT_LAYOUT
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import _SLOT_ORDER

    assert tuple(SLOT_KEYS) == CORE_SLOTS == EQUIPMENT_SLOTS
    assert TUNE_LABELS == SLOT_LABELS
    assert {key for _g, slots in SLOT_GROUPS for key, _l in slots} == set(EQUIPMENT_SLOTS)
    assert list(_SLOT_ORDER) == [
        (s.key, s.label, s.filter_type) for s in SLOT_SPECS]
    assert list(_SLOT_LAYOUT) == [
        (s.row, s.col, s.key, s.label, s.filter_type) for s in SLOT_SPECS]
    assert DEFAULT_SLOT_LAYOUT == grid_layout()
