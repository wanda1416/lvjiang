"""装备卡片名称行多状态标签测试。"""

from lvjiang.apps.yysls.ui.equip_status_tab import (
    _CompactEquipCard,
    _SlotCard,
)


def test_equipped_mock_can_show_filtered_and_mock_tags(qtbot):
    card = _SlotCard("main_weapon", "主武器", "weapon")
    qtbot.addWidget(card)
    card.set_equip({
        "name": "模拟剑",
        "level": 110,
        "_extra": {"is_mock": True},
    })
    card.set_selected(True)

    assert card.status_tags.is_visible("filtered")
    assert card.status_tags.is_visible("mock")

    card.set_selected(False)
    assert not card.status_tags.is_visible("filtered")
    assert card.status_tags.is_visible("mock")


def test_compact_card_uses_same_status_tag_bar(qtbot):
    card = _CompactEquipCard()
    qtbot.addWidget(card)
    card.set_equip(
        {"name": "模拟环", "level": 110, "_extra": {"is_mock": True}},
        "环",
    )
    assert card.status_tags.is_visible("mock")


def test_compact_card_can_show_loadout_tag(qtbot):
    card = _CompactEquipCard()
    qtbot.addWidget(card)
    card.set_equip(
        {"name": "备用环", "level": 110}, "环", is_loadout=True,
    )
    assert card.status_tags.is_visible("loadout")
    assert not card.status_tags.is_visible("mock")
