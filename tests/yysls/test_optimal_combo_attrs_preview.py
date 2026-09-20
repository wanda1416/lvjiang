"""最优结果卡片动作、全局排名与组合预览。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QTabWidget,
)

from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
from lvjiang.apps.yysls.ui.loadout.optimal_combo import (
    OptimalComboPage,
    _global_top_results,
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


def test_gongjue_select_all_shortcut(qtbot, monkeypatch):
    from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes

    monkeypatch.setattr(OptimalComboPage, "_load_candidates", lambda self: None)
    monkeypatch.setattr(OptimalComboPage, "_load_tuning_options", lambda self: None)
    page = OptimalComboPage(
        _Host(), "鸣金·虹", "基础方案", CombatAttributes(), gongjue="会意",
    )
    qtbot.addWidget(page)
    button = page.findChild(QPushButton, "selectAllGongjueButton")
    assert button is not None and button.text() == "全选"
    assert _layout_of(button) is _layout_of(page._btn_gongjue)
    assert page._selected_gongjues() == ["会意"]

    button.click()
    assert page._selected_gongjues() == ["会意", "精准", "会心"]
    assert page._btn_gongjue.text() == "会意 / 精准 / 会心"
    button.click()
    assert all(action.isChecked() for action in page._gongjue_actions.values())

    page._set_search_controls_enabled(False)
    assert not button.isEnabled()
    page._set_search_controls_enabled(True)
    assert button.isEnabled()
    assert page._results_scroll.verticalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    assert page._results_inner.sizeConstraint() == (
        QLayout.SizeConstraint.SetMinimumSize)
    assert page._results_inner.alignment() == Qt.AlignmentFlag.AlignTop


def test_apply_tuning_switch_controls_rating_filter(qtbot, monkeypatch):
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QCheckBox

    from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes

    monkeypatch.setattr(OptimalComboPage, "_load_candidates", lambda self: None)
    page = OptimalComboPage(_Host(), "鸣金·虹", "基础方案", CombatAttributes())
    qtbot.addWidget(page)
    assert page._chk_apply_tuning.isChecked()
    page._tuning_selection = [("test_rule", "test_playstyle")]
    checkbox = QCheckBox(page)
    ratings = []
    row = SimpleNamespace(checkbox=checkbox, equip={}, set_rating=ratings.append)
    page._slot_groups = {"head": SimpleNamespace(rows=[row], setEnabled=lambda _: None)}
    calls = []

    def judge(equip, pairs):
        calls.append(pairs)
        return SimpleNamespace(meets=lambda _: False, label="垃圾")

    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.combo_rules.judge_best_rating", judge)
    page._on_tuning_changed()
    assert not checkbox.isChecked() and len(calls) == 1

    page._chk_apply_tuning.setChecked(False)
    assert checkbox.isChecked() and ratings[-1] == "-"
    assert len(calls) == 1
    assert page._effective_tuning_selection() == []
    assert not page._edit_tuning.isEnabled()
    assert not page._combo_min_rating.isEnabled()
    page._set_search_controls_enabled(False)
    page._set_search_controls_enabled(True)
    assert not page._edit_tuning.isEnabled()

    page._chk_apply_tuning.setChecked(True)
    assert not checkbox.isChecked() and len(calls) == 2
    assert page._edit_tuning.isEnabled() and page._combo_min_rating.isEnabled()
    assert page._tuning_selection == [("test_rule", "test_playstyle")]


def test_analysis_options_restore_and_persist_without_call_time_inputs(
    qtbot, monkeypatch,
):
    from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes

    monkeypatch.setattr(OptimalComboPage, "_load_candidates", lambda self: None)

    def load_options(page):
        page._tuning_options = [
            ("rule-a", "playstyle-a", "规则A-玩法A", "plan"),
            ("rule-b", "playstyle-b", "规则B-玩法B", "school"),
        ]
        page._tuning_selection = [("rule-a", "playstyle-a")]
        page._refresh_tuning_display()

    monkeypatch.setattr(OptimalComboPage, "_load_tuning_options", load_options)
    saved = []
    page = OptimalComboPage(
        _Host(), "鸣金·虹", "基础方案", CombatAttributes(),
        gongjue="会意",
        assumptions_provider=lambda: Assumptions(full_chengyin=True),
        analysis_settings={
            "apply_tuning_rules": False,
            "candidate_rating_rules": [["rule-b", "playstyle-b"]],
            "minimum_rating": "优秀",
            "exclude_mock": False,
            "smart_analysis": False,
            "season_chengyin": True,
        },
        settings_changed=saved.append,
    )
    qtbot.addWidget(page)

    assert not page._chk_apply_tuning.isChecked()
    assert page._tuning_selection == [("rule-b", "playstyle-b")]
    assert page._min_rating() == "优秀"
    assert not page._chk_exclude_mock.isChecked()
    assert not page._chk_pruning.isChecked()
    assert page._chk_season_chengyin.isChecked()
    assert saved == []

    page._chk_pruning.setChecked(True)
    assert saved[-1] == {
        "apply_tuning_rules": False,
        "candidate_rating_rules": [["rule-b", "playstyle-b"]],
        "minimum_rating": "优秀",
        "exclude_mock": False,
        "smart_analysis": True,
        "season_chengyin": True,
    }
    assert "gongjue" not in saved[-1]
    assert "assumptions" not in saved[-1]


def test_result_card_actions_share_one_row(qtbot):
    card = _ResultCard(1, _result(), {"main_weapon": "主武器"})
    qtbot.addWidget(card)
    buttons = {
        name: card.findChild(QPushButton, name)
        for name in ("resultApplyButton", "resultViewButton")
    }
    assert all(button is not None for button in buttons.values())
    rows = {
        button.parentWidget() and _layout_of(button) for button in buttons.values()
    }
    assert len(rows) == 1 and isinstance(next(iter(rows)), QHBoxLayout)

    seen: list[dict] = []
    card.view_clicked.connect(seen.append)
    buttons["resultViewButton"].click()
    assert seen and seen[0]["gongjue"] == "会意"
    assert card.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum


def test_multiple_gongjues_share_one_global_top_ten():
    results = [
        {"rate": rate, "gongjue": gongjue}
        for gongjue, rates in (
            ("会意", (0.99, 0.70, 0.60, 0.50, 0.40, 0.30)),
            ("会心", (0.98, 0.97, 0.96, 0.95, 0.94, 0.93)),
        )
        for rate in rates
    ]

    top = _global_top_results(results)

    assert len(top) == 10
    assert [item["rate"] for item in top] == sorted(
        (item["rate"] for item in results), reverse=True)[:10]
    assert [item["gongjue"] for item in top[:3]] == ["会意", "会心", "会心"]


def test_view_result_refreshes_both_preview_tabs(qtbot):
    page = OptimalComboPage.__new__(OptimalComboPage)
    page._tab_widget = QTabWidget()
    qtbot.addWidget(page._tab_widget)
    for title in ("候选装备", "最优结果", "组合详情", "战斗属性"):
        page._tab_widget.addTab(QLabel(), title)
    calls: list[tuple[str, dict, bool]] = []
    page._on_show_detail = lambda result, *, activate=True: calls.append(
        ("detail", result, activate))
    page._on_show_attrs = lambda result, *, activate=True: calls.append(
        ("attrs", result, activate))
    result = _result()

    OptimalComboPage._on_show_result(page, result)

    assert calls == [("detail", result, False), ("attrs", result, False)]
    assert page._tab_widget.currentIndex() == 2


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
