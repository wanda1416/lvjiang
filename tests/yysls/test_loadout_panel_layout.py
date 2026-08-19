from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.apps.yysls.ui.loadout import LoadoutPanel


class Host(QWidget):
    user_changed = pyqtSignal(str)
    equipment_changed = pyqtSignal()
    graduation_updated = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.user_combo = QComboBox(self)
        self.user_combo.addItem("alice")

    def active_user_name(self):
        return "alice"

    def navigate_user(self, _delta):
        pass


def test_three_view_modes(qtbot, tmp_path, monkeypatch):
    import lvjiang.constants
    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        lvjiang.constants, "SESSION_PATH", tmp_path / "session.json")
    host = Host()
    panel = LoadoutPanel(host)
    qtbot.addWidget(host)
    qtbot.addWidget(panel)

    assert panel._view_mode == "sidebar"
    assert panel._equipment.isVisible() is False  # parent panel not shown yet

    panel.show()
    qtbot.wait(10)
    assert not panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()

    panel._set_view_mode("half")
    assert panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()
    assert panel._equipment.isVisible()
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

    # 默认无记录时为 sidebar
    assert panel._view_mode == "sidebar"

    panel._set_view_mode("half")
    assert load_ui_page_state("loadout_panel")["view_mode"] == "half"

    # 模拟半屏下拖动分割条 → 记录值即实际展示值
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
