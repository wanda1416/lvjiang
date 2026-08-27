from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QWidget

from lvjiang.apps.yysls.ui.loadout import LoadoutPanel
from lvjiang.apps.yysls.ui.loadout.combat.layout import (
    DISPLAY_MODE_FULL,
    DISPLAY_MODE_HALF,
    DISPLAY_MODE_HALF_COMPACT,
)
from lvjiang.apps.yysls.ui.loadout.combat.layout_strategies import (
    FullCardLayout,
    HalfCardLayout,
)


class Host(QWidget):
    user_changed = pyqtSignal(str)
    app_event = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._active_user = "alice"
        self.user_combo = QComboBox(self)
        self.user_combo.addItems(["alice", "bob"])

    def active_user_name(self):
        return self._active_user

    def navigate_user(self, _delta):
        pass

    def switch_user(self, name):
        self._active_user = name
        self.user_changed.emit(name)


def test_three_view_modes(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)

    assert panel._view_mode == "half"

    panel.show()
    qtbot.wait(10)
    assert panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()

    panel._set_view_mode("sidebar")
    assert not panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()

    panel._set_view_mode("half")
    assert panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()
    assert panel._equipment.isVisible()
    for combo in (
        panel._equipment._sort_filter,
        panel._equipment._type_filter,
        panel._equipment._source_filter,
    ):
        assert combo.minimumWidth() >= 104
        assert combo.view().minimumWidth() >= combo.minimumWidth()
    filter_row = panel._equipment._info_widget.layout().itemAt(1).layout()
    assert isinstance(filter_row, QHBoxLayout)
    assert filter_row.count() == 11  # five labels, five combos, trailing stretch
    assert panel._character._combat_attrs_tab._attrs_scroll.isVisible()
    flow = panel._character._combat_attrs_tab._main_layout.itemAt(0).layout()
    assert flow.count() == 5  # four ordered cards + bottom stretch

    panel._set_view_mode("full")
    assert panel._left_shell.isVisible()
    assert not panel._right_shell.isVisible()
    grid = panel._character._combat_attrs_tab._main_layout.itemAt(0).layout()
    assert grid.itemAtPosition(0, 0).widget() is panel._character._combat_attrs_tab._attack_card
    assert grid.itemAtPosition(1, 0).widget() is panel._character._combat_attrs_tab._judgment_card

    panel._set_view_mode("sidebar")
    assert not panel._character._combat_attrs_tab._attrs_scroll.isVisible()
    assert panel._right_shell.isVisible()


def test_full_mode_cards_expand_into_real_grid_rows(qtbot, tmp_path, monkeypatch):
    """full 模式的伸展空间属于两行卡片，不能落到不存在的第3行。"""
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)
    panel.resize(1200, 900)
    panel.show()
    panel._set_view_mode("full")
    qtbot.wait(20)

    combat = panel._character._combat_attrs_tab
    grid = combat._main_layout.itemAt(0).layout()
    cards = (
        combat._attack_card, combat._judgment_card,
        combat._gain_card, combat._damage_card,
    )

    assert combat._display_mode == DISPLAY_MODE_FULL
    assert grid.rowStretch(0) == 1
    assert grid.rowStretch(1) == 1
    # Qt 按整数像素分配网格高度；可用高度为奇数时，两行即使
    # 伸展系数相同也会相差 1px（Windows offscreen CI 即为此情况）。
    heights = [card.height() for card in cards]
    assert max(heights) - min(heights) <= 1
    assert max(card.geometry().bottom() for card in cards) >= (
        combat._attrs_widget.height() - 16)


def test_hidden_compact_user_refresh_keeps_card_grid_items(
        qtbot, tmp_path, monkeypatch):
    """父页面隐藏不等于属性行被过滤；切用户后不能把网格清空。"""
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)
    # 半屏左栏约 440px，稳定进入 half_compact。
    panel.resize(900, 800)
    panel.show()
    qtbot.wait(20)
    combat = panel._character._combat_attrs_tab
    assert combat._display_mode == DISPLAY_MODE_HALF_COMPACT

    panel.hide()
    host.switch_user("bob")
    qtbot.wait(10)
    panel.show()
    qtbot.wait(20)

    pairs = (
        (combat._attack_grid, combat._attack_grid_items),
        (combat._judgment_grid, combat._judgment_grid_items),
        (combat._gain_grid, combat._gain_grid_items),
        (combat._damage_grid, combat._damage_grid_items),
    )
    for grid, items in pairs:
        assert grid.count() == sum(
            1 for widget, _row, _col in items if not widget.isHidden())
    assert combat._judgment_grid.count() > 0
    assert combat._gain_grid.count() > 0
    assert combat._judgment_card.height() > 52
    assert combat._gain_card.height() > 52


def test_stale_compact_resize_callback_cannot_override_full_mode(
        qtbot, tmp_path, monkeypatch):
    """紧凑策略的延迟回调执行时，必须尊重已经完成的模式切换。"""
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)
    panel.resize(900, 800)
    panel.show()
    qtbot.wait(20)
    combat = panel._character._combat_attrs_tab
    assert combat._display_mode == DISPLAY_MODE_HALF_COMPACT

    # 模拟 compact resizeEvent 已排队，但回调前用户切换到了 full。
    combat.resize(700, combat.height())
    combat._strategy.on_resize(combat)
    panel._set_view_mode("full")
    qtbot.wait(20)

    assert combat._display_mode == DISPLAY_MODE_FULL
    assert isinstance(combat._strategy, FullCardLayout)
    grid = combat._main_layout.itemAt(0).layout()
    assert grid.itemAtPosition(0, 0).widget() is combat._attack_card
    assert grid.itemAtPosition(1, 1).widget() is combat._damage_card
    # 从 compact 退出时，所有原始网格项均已恢复。
    assert combat._attack_grid.count() == len(combat._attack_grid_items)
    assert combat._judgment_grid.count() == len(combat._judgment_grid_items)


def test_half_compact_recovers_to_half_when_width_expands(
        qtbot, tmp_path, monkeypatch):
    """half_compact 的退出必须恢复标准网格和 half 策略。"""
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)
    panel.resize(900, 800)
    panel.show()
    qtbot.wait(20)
    combat = panel._character._combat_attrs_tab
    assert combat._display_mode == DISPLAY_MODE_HALF_COMPACT

    panel.resize(1400, 800)
    qtbot.wait(30)

    assert combat.width() >= 568
    assert combat._display_mode == DISPLAY_MODE_HALF
    assert isinstance(combat._strategy, HalfCardLayout)
    flow = combat._main_layout.itemAt(0).layout()
    assert flow.count() == 5
    assert combat._attack_grid.count() == len(combat._attack_grid_items)
    assert combat._judgment_grid.count() == len(combat._judgment_grid_items)
    assert combat._gain_grid.count() == len(combat._gain_grid_items)
    assert combat._damage_grid.count() == len(combat._damage_grid_items)


def test_view_mode_persisted_in_ui_state(qtbot, tmp_path, monkeypatch):
    """展开模式与半屏分割宽度独立持久化到 ui_state.loadout_panel"""
    import lvjiang.constants
    from lvjiang.core.config import load_ui_page_state

    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)

    # 默认无记录时为 half
    assert panel._view_mode == "half"

    panel._set_view_mode("full")
    assert load_ui_page_state("loadout_panel")["view_mode"] == "full"

    # 模拟半屏下拖动分割条 → 记录值即实际展示值
    panel._set_view_mode("half")
    panel.show()
    panel.resize(1200, 800)
    qtbot.wait(10)
    panel._splitter.setSizes([700, 484])
    dragged = panel._splitter.sizes()
    panel._on_splitter_moved()
    state = load_ui_page_state("loadout_panel")
    assert state["half_split_sizes"] == dragged

    # 切换模式不应覆盖分割宽度（两字段独立保存）
    panel._set_view_mode("sidebar")
    state = load_ui_page_state("loadout_panel")
    assert state["view_mode"] == "sidebar"
    assert state["half_split_sizes"] == dragged

    # 重建面板后恢复展开模式与半屏分割宽度
    panel2 = LoadoutPanel(host)
    qtbot.addWidget(panel2)
    assert panel2._view_mode == "sidebar"
    assert panel2._half_split_sizes == dragged
    panel2._set_view_mode("half")
    panel2.show()
    panel2.resize(1200, 800)
    qtbot.wait(10)
    assert panel2._splitter.sizes() == dragged


def test_panel_toolbar_exposes_every_equipment_action(qtbot, tmp_path, monkeypatch):
    """EquipStatusTab 的动作必须全部能从面板工具栏**看得见地**触达。

    EquipStatusTab 自带的按钮行在面板里被 set_embedded_mode(True) 整体隐藏，
    面板改用自己的工具栏逐个转发。这个转发是手抄的清单，漏一条就等于该功能
    在界面上彻底消失——「承音装备合并」曾经就这样掉了：core 逻辑、对话框、
    测试都在，只是没有任何地方能点到它。

    必须按**可见性**断言：被隐藏的那排按钮仍然是 panel 的子控件，findChildren
    照样找得到，只查存在性的话这个测试恒真。
    """
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)
    panel.show()
    qtbot.wait(10)

    from PyQt6.QtWidgets import QPushButton
    visible = {b.text() for b in panel.findChildren(QPushButton)
               if b.text() and b.isVisible()}

    # status_tab 按钮行里每个 _on_* 动作对应的入口（刷新与用户切换由面板
    # 自己的头部提供，不在这份清单里）。
    for label in ("最优组合", "培养建议", "承音装备合并",
                  "创建模拟装备", "清空真实装备", "导出数据"):
        assert label in visible, f"面板工具栏缺少可见入口：{label}"

    # 转发目标必须真实存在，否则点下去才报 AttributeError
    for handler in ("_on_optimal_combo", "_on_affix_impact", "_on_chengyin_merge",
                    "_on_mock_create", "_on_clear_real", "_on_export"):
        assert callable(getattr(panel._equipment, handler)), handler
