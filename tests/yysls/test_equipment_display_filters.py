"""装备展示新增品阶、调律进度和备战状态筛选。"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QComboBox

import lvjiang.apps.yysls.config as config_module
from lvjiang.apps.yysls.ui.loadout.equip.status_tab import EquipStatusTab


def _combo(value: str) -> QComboBox:
    combo = QComboBox()
    combo.addItem(value, value)
    return combo


def _filter_stub(*, quality: str, affix: str, status: str):
    return SimpleNamespace(
        _quality_filter=_combo(quality),
        _status_filter=_combo(status),
        _get_level_threshold=lambda: 0,
        _get_affix_filter=lambda: affix,
    )


def _equip(*, quality: str | None = "gold", affix_count: int = 1) -> dict:
    equip = {"quality": quality, "level": 110}
    for index in range(1, affix_count + 1):
        equip[f"affix_{index}"] = {"name": f"词条{index}"}
    return equip


def test_white_quality_means_any_non_gold_non_purple(qtbot):
    tab = _filter_stub(quality="other", affix="all", status="all")
    qtbot.addWidget(tab._quality_filter)
    qtbot.addWidget(tab._status_filter)

    assert EquipStatusTab._equip_passes_filter(tab, _equip(quality="blue"))
    assert EquipStatusTab._equip_passes_filter(tab, _equip(quality="green"))
    assert EquipStatusTab._equip_passes_filter(tab, _equip(quality=None))
    assert not EquipStatusTab._equip_passes_filter(tab, _equip(quality="gold"))
    assert not EquipStatusTab._equip_passes_filter(tab, _equip(quality="purple"))


def test_not_full_tuning_accepts_at_most_four_affixes(qtbot):
    tab = _filter_stub(
        quality="all", affix="not_full_tuning", status="all")
    qtbot.addWidget(tab._quality_filter)
    qtbot.addWidget(tab._status_filter)

    assert EquipStatusTab._equip_passes_filter(tab, _equip(affix_count=4))
    assert not EquipStatusTab._equip_passes_filter(tab, _equip(affix_count=5))


def test_loadout_status_uses_any_plan_reference(qtbot):
    referenced = _filter_stub(
        quality="all", affix="all", status="referenced")
    unreferenced = _filter_stub(
        quality="all", affix="all", status="unreferenced")
    for tab in (referenced, unreferenced):
        qtbot.addWidget(tab._quality_filter)
        qtbot.addWidget(tab._status_filter)

    equip = _equip()
    assert EquipStatusTab._equip_passes_filter(
        referenced, equip, is_referenced=True)
    assert not EquipStatusTab._equip_passes_filter(
        referenced, equip, is_referenced=False)
    assert EquipStatusTab._equip_passes_filter(
        unreferenced, equip, is_referenced=False)
    assert not EquipStatusTab._equip_passes_filter(
        unreferenced, equip, is_referenced=True)


def test_filtered_collection_excludes_current_equipment_and_marks_references(
    qtbot, monkeypatch,
):
    source_filter = _combo("all")
    qtbot.addWidget(source_filter)
    monkeypatch.setattr(
        config_module,
        "get_game_config",
        lambda: SimpleNamespace(get_group_to_part=lambda: {"ring": "环"}),
    )
    tab = SimpleNamespace(
        _selected_slot=None,
        _inv=SimpleNamespace(
            active_plan_fps={"worn"},
            referenced_plan_fps={"worn", "standby"},
        ),
        _source_filter=source_filter,
        _bag_items={
            "ring": {
                "worn": {"_fp": "worn"},
                "standby": {"_fp": "standby"},
                "free": {"_fp": "free"},
            },
        },
        _mock_items={},
        _equip_passes_filter=lambda _equip, *, is_referenced: True,
    )

    cards = EquipStatusTab._collect_filtered_cards(tab)

    assert [(card[0]["_fp"], card[4]) for card in cards] == [
        ("standby", True),
        ("free", False),
    ]
