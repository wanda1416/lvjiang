"""来源页只读取面板快照，刷新不读写用户配置。"""

from PyQt6 import sip
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.attribute_sources import (
    build_attribute_source_report,
)
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    GraduationAttrContext,
)
from lvjiang.apps.yysls.ui.loadout.combat.attrs_tab import CombatAttrsTab
from lvjiang.apps.yysls.ui.loadout.combat.cards import CombatCardsMixin
from lvjiang.ui.combo_box import AutoWidthComboBox


def test_title_link_opens_readonly_sources_and_refreshes_snapshot(qtbot):
    report = build_attribute_source_report(
        CombatAttributes(min_outer=100, crit_rate=0.023), CombatAttributes(), {},
        context=GraduationAttrContext(0, 0, None), school="", game_config=get_game_config())

    class Cards(CombatCardsMixin, QWidget):
        _show_attribute_sources = CombatAttrsTab._show_attribute_sources
        _clear_attribute_sources_dialog = CombatAttrsTab._clear_attribute_sources_dialog

        def __init__(self, parent):
            super().__init__(parent)
            self._attr_labels = {}
            self._attribute_report = report
            self._attribute_report_caption = "当前方案快照"
            self._attribute_sources_dialog = None
            self._add_attack_card(QVBoxLayout(self))

    owner = QWidget()
    qtbot.addWidget(owner)
    cards = Cards(owner)
    cards._attribute_sources_link.linkActivated.emit("sources")
    dialog = cards._attribute_sources_dialog
    assert dialog.isVisible()
    assert dialog._caption.text() == "当前方案快照"
    tree = dialog._tree
    rows = {tree.topLevelItem(i).text(0): tree.topLevelItem(i) for i in range(tree.topLevelItemCount())}
    assert set(rows) == {"最小外功攻击", "会心率"}
    assert rows["最小外功攻击"].text(1) == "100.0"
    assert rows["会心率"].text(1) == "2.30%"
    assert rows["会心率"].text(7) == rows["会心率"].text(8)
    cards._attribute_sources_link.linkActivated.emit("sources")
    assert cards._attribute_sources_dialog is dialog
    cards._attribute_report = build_attribute_source_report(
        CombatAttributes(min_outer=200), CombatAttributes(), {},
        context=GraduationAttrContext(0, 0, None), school="", game_config=get_game_config())
    cards._attribute_report_caption = "更新后的方案快照"
    assert rows["最小外功攻击"].text(1) == "100.0"
    dialog.refresh()
    assert tree.topLevelItemCount() == 1
    assert tree.topLevelItem(0).text(1) == "200.0"
    assert dialog._caption.text() == "更新后的方案快照"
    dialog.close()
    qtbot.waitUntil(lambda: cards._attribute_sources_dialog is None)
    cards._attribute_sources_link.linkActivated.emit("sources")
    reopened = cards._attribute_sources_dialog
    assert reopened is not dialog and reopened.isVisible()
    # 关闭面板时仍打开着来源窗口；销毁子窗口不得回调已析构的面板。
    cards.deleteLater()
    qtbot.waitUntil(lambda: sip.isdeleted(cards))
    assert sip.isdeleted(reopened)


def test_preview_sources_use_preview_equipment_and_frozen_base(qtbot, monkeypatch):
    class Host(QWidget):
        user_changed = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.user_combo = AutoWidthComboBox(self)

        @staticmethod
        def active_user_name():
            return ""

    host = Host()
    qtbot.addWidget(host)
    tab = CombatAttrsTab(host, preview=True)
    qtbot.addWidget(tab)

    def forbidden(*_args):
        raise AssertionError("预览来源不能读取真实装备或保存选择")

    monkeypatch.setattr(tab, "_equipped_snapshot", forbidden)
    monkeypatch.setattr(tab, "_save_selection", forbidden)
    tab.show_preview(
        {"ring": {"level": 115, "quality": "gold"}},
        base_attrs=CombatAttributes(min_outer=100), school="鸣金·虹", world_level=115,
    )
    assert tab._attribute_report.raw == tab._current_combat_attrs
    assert tab._attribute_report.category_attrs("equipment_base").min_outer == 152
    assert tab._attribute_report.category_attrs("base").min_outer == 100
    assert "预览" in tab._attribute_report_caption
