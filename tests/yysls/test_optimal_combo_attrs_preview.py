"""最优结果卡片三个动作同排；「战斗属性 (#N)」页预览该组合的属性面板。"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTabWidget

from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
from lvjiang.apps.yysls.ui.loadout.optimal_combo import (
    OptimalComboPage,
    _ResultCard,
)
from tests.yysls.test_loadout_panel_layout import _Host


def _result(rank: int = 2) -> dict:
    return {
        "rate": 0.8, "dps": 1000, "gongjue": "会意", "rank": rank,
        "equipped": {
            "main_weapon": {
                "type": "剑", "name": "剑", "level": 110, "quality": "gold",
                "affix_1": {"name": "最大外功攻击", "value": 100},
            },
            "ring": {
                "type": "环", "name": "环", "level": 110, "quality": "gold",
                "is_chengyin": False,
                "affix_1": {"name": "最大外功攻击", "value": 100},
            },
        },
        "assumptions": {"ring": ["同等级承音假设"]},
    }


def test_result_card_actions_share_one_row(qtbot):
    card = _ResultCard(1, _result(), {"main_weapon": "主武器"})
    qtbot.addWidget(card)
    buttons = {
        name: card.findChild(QPushButton, name)
        for name in ("resultApplyButton", "resultDetailButton", "resultAttrsButton")
    }
    assert all(button is not None for button in buttons.values())
    rows = {
        button.parentWidget() and _layout_of(button) for button in buttons.values()
    }
    assert len(rows) == 1 and isinstance(next(iter(rows)), QHBoxLayout)

    seen: list[dict] = []
    card.attrs_clicked.connect(seen.append)
    buttons["resultAttrsButton"].click()
    assert seen and seen[0]["gongjue"] == "会意"


def _layout_of(widget):
    parent = widget.parentWidget()
    assert parent is not None
    for layout in parent.findChildren(QHBoxLayout):
        if layout.indexOf(widget) >= 0:
            return layout
    return None


class _FakePreview:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, str | None]] = []

    def show_preview(self, equipped, *, gongjue=None):
        self.calls.append((equipped, gongjue))


def _page(qtbot) -> tuple[OptimalComboPage, _FakePreview]:
    page = OptimalComboPage.__new__(OptimalComboPage)
    page._playstyle = "无名"
    page._assumptions_provider = lambda: Assumptions(full_chengyin=True)
    page._searched_assumptions = Assumptions(full_chengyin=True, playstyle="无名")
    page._tab_widget = QTabWidget()
    qtbot.addWidget(page._tab_widget)
    for title in ("候选装备", "最优结果", "组合详情", "战斗属性"):
        page._tab_widget.addTab(QLabel(), title)
    page._attrs_hint = QLabel()
    preview = _FakePreview()
    page._attrs_preview = preview
    return page, preview


def test_show_attrs_projects_per_slot_and_labels_tab_with_rank(qtbot):
    page, preview = _page(qtbot)
    result = _result(rank=3)

    OptimalComboPage._on_show_attrs(page, result)

    assert page._tab_widget.currentIndex() == 3
    assert page._tab_widget.tabText(3) == "战斗属性 (#3)"
    assert "#3" in page._attrs_hint.text() and "会意" in page._attrs_hint.text()
    (equipped, gongjue), = preview.calls
    assert gongjue == "会意"
    # 只有标了「同等级承音假设」的部位按承音投影；主武器保持原样
    assert equipped["ring"]["is_chengyin"] is True
    assert equipped["ring"]["affix_1"]["value"] != 100
    assert equipped["main_weapon"]["affix_1"]["value"] == 100
    # 原始结果不被改写
    assert result["equipped"]["ring"].get("is_chengyin") is False


def test_combat_attrs_preview_instance_is_read_only(qtbot, monkeypatch):
    from lvjiang.apps.yysls.ui.loadout.combat.attrs_tab import CombatAttrsTab

    host = _Host()
    tab = CombatAttrsTab(host, preview=True)
    qtbot.addWidget(tab)
    tab.set_embedded_mode("full")
    assert not tab._select_group.isVisible() and not tab._toolbar_widget.isVisible()

    saved: list[int] = []
    monkeypatch.setattr(
        "lvjiang.core.config.session.save_settings", lambda *a, **k: saved.append(1))
    generation = tab._graduation_generation
    tab.show_preview(_result()["equipped"], gongjue="会意")
    # 装备属性进了面板（最大外功 100 + 100），弓玦按传入套装
    assert tab._current_combat_attrs.max_outer >= 200
    assert tab._get_current_gongjue() == "会意"
    # 不发布毕业率、不保存选择
    assert tab._graduation_generation == generation
    assert tab._pending_graduation is None
    tab._save_selection()
    assert saved == []


def test_read_only_slot_card_swallows_clicks_and_hover(qtbot):
    """分析对话框里的卡片只看不点：不能顺着父链触发备战方案页的部位筛选。"""
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtWidgets import QWidget

    from lvjiang.apps.yysls.ui.loadout.equip.cards import _SlotCard
    from lvjiang.apps.yysls.ui.loadout.equip.status_tab import EquipStatusTab

    class _FakeStatusTab(QWidget):
        def __init__(self):
            super().__init__()
            self.clicked: list[str] = []

        def _on_slot_clicked(self, slot_key):
            self.clicked.append(slot_key)

    # 让父链上的假页面通过 isinstance 检查
    _FakeStatusTab.__bases__ = (EquipStatusTab,)
    host = _FakeStatusTab.__new__(_FakeStatusTab)
    QWidget.__init__(host)
    host.clicked = []
    qtbot.addWidget(host)
    card = _SlotCard("ring", "环", "ring", read_only=True, parent=host)
    card.set_equip(_result()["equipped"]["ring"])
    qtbot.mouseClick(card, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
    assert host.clicked == []
    assert not card._hovered


def test_detail_panel_reserves_two_strip_rows(qtbot):
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import (
        _STRIP_ROW_HEIGHT,
        _SlotDetailPanel,
    )

    panel = _SlotDetailPanel("ring", "环", "ring")
    qtbot.addWidget(panel)
    assert panel.strip.height() == _STRIP_ROW_HEIGHT * 2 + 2
    panel.show_equip(_result()["equipped"]["ring"], ["同等级承音假设", "满定音假设"], None)
    assert panel.strip.height() == _STRIP_ROW_HEIGHT * 2 + 2
    assert panel.card._read_only
    # 假设标签在第二行（assumption_slot），换装状态在第一行
    assert panel.assumption_slot.count() == 2
    assert panel.change_slot.count() == 1


def test_both_card_kinds_render_affixes_identically(qtbot):
    """词条区域只有一份实现：穿戴槽卡片与背包卡片对同一件装备渲染出相同的行。"""
    from PyQt6.QtWidgets import QLabel

    from lvjiang.apps.yysls.ui.loadout.equip.cards import (
        _CompactEquipCard,
        _SlotCard,
    )

    equip = {
        "type": "环", "name": "环", "level": 110, "quality": "gold",
        "is_chengyin": True,
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "会意率", "value": 6.6, "unit": "%",
                    "is_transferred": True,
                    "target_transmute_name": "会心率",
                    "target_transmute_value": 13.2},
        "dingyin": {"name": "外功穿透", "value": 14.2},
    }

    def texts(card):
        return [
            label.text() for label in card.affix_container.findChildren(QLabel)
            if label.text() and label is not card.cooldown_label
        ]

    slot = _SlotCard("ring", "环", "ring")
    qtbot.addWidget(slot)
    slot.set_equip(equip)
    bag = _CompactEquipCard({})
    qtbot.addWidget(bag)
    bag.set_equip(equip, "环", "ring")

    assert texts(slot) == texts(bag)
    assert "会意率 ⟳" in texts(slot) and "(会心率)" in texts(slot)
    assert "14.2%" in texts(slot)          # 定音按百分比显示
    assert "承音" in slot.lbl_info.text() and "承音" in bag.lbl_level.text()
