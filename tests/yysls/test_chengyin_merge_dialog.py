from __future__ import annotations

from PyQt6.QtWidgets import QGridLayout, QScrollArea

from lvjiang.apps.yysls.core.loadout import ChengyinMergeCandidate
from lvjiang.apps.yysls.ui.loadout.equip.chengyin_merge_dialog import (
    ChengyinMergeDialog,
)


def _equip(name: str, value: int) -> dict:
    equip = {
        "type": "剑",
        "name": name,
        "level": 105,
        "quality": "gold",
        "is_chengyin": True,
        "dingyin": {"name": "定音", "value": 1},
    }
    for index in range(1, 6):
        equip[f"affix_{index}"] = {
            "name": f"词条{index}",
            "value": value + index,
        }
    return equip


def _candidate(index: int) -> ChengyinMergeCandidate:
    return ChengyinMergeCandidate(
        old_fp=f"old-{index}",
        new_fp=f"new-{index}",
        old=_equip(f"旧装备{index}", index),
        new=_equip(f"新装备{index}", index + 10),
    )


def test_dialog_uses_two_column_scroll_grid_and_fixed_footer(qtbot):
    dialog = ChengyinMergeDialog([_candidate(i) for i in range(3)], {})
    qtbot.addWidget(dialog)

    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    grid = scroll.widget().layout()
    assert isinstance(grid, QGridLayout)
    assert grid.indexOf(dialog._pairs[0]) == 0
    assert grid.indexOf(dialog._pairs[1]) == 1
    assert grid.indexOf(dialog._pairs[2]) == 2
    assert dialog.layout().indexOf(scroll) >= 0
    assert dialog.layout().indexOf(dialog.merge_button) == -1


def test_only_checked_candidates_are_returned(qtbot):
    candidates = [_candidate(1), _candidate(2)]
    dialog = ChengyinMergeDialog(candidates, {})
    qtbot.addWidget(dialog)

    assert not dialog.merge_button.isEnabled()
    dialog._pairs[1].checkbox.setChecked(True)

    assert dialog.merge_button.isEnabled()
    assert dialog.selected_candidates() == [candidates[1]]
