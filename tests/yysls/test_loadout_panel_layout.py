from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.apps.yysls.ui.loadout_panel import LoadoutPanel


class Host(QWidget):
    user_changed = pyqtSignal(str)
    equipment_changed = pyqtSignal()
    graduation_updated = pyqtSignal()

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

    def ctrl_buttons(row):
        return [row.itemAt(i).widget() for i in range(row.count())
                if row.itemAt(i).widget() is not None]

    assert panel._view_mode == "sidebar"
    assert panel._equipment.isVisible() is False  # parent panel not shown yet

    panel.show()
    qtbot.wait(10)
    assert not panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()
    assert len(ctrl_buttons(panel._equip_ctrl_row)) == 2

    panel._set_view_mode("half")
    assert panel._left_shell.isVisible()
    assert panel._right_shell.isVisible()
    assert panel._equipment.isVisible()
    assert panel._character._combat_attrs_tab._attrs_scroll.isVisible()
    flow = panel._character._combat_attrs_tab._main_layout.itemAt(0).layout()
    assert flow.count() == 5  # four ordered cards + bottom stretch
    # 成对折叠按钮分别位于两个 shell 顶部的专属控制条
    left_btns = ctrl_buttons(panel._combat_ctrl_row)
    right_btns = ctrl_buttons(panel._equip_ctrl_row)
    assert len(left_btns) == 1
    assert len(right_btns) == 1
    # 控制条几何完全相同 → 按钮垂直对齐
    assert panel._combat_ctrl_row.parentWidget().height() == \
        panel._equip_ctrl_row.parentWidget().height()
    panel.resize(1200, 800)
    qtbot.wait(100)  # 确保布局完成
    left_pos = left_btns[0].mapTo(panel, left_btns[0].rect().center())
    right_pos = right_btns[0].mapTo(panel, right_btns[0].rect().center())
    assert left_pos.y() == right_pos.y()
    # 水平镜像对称：两按钮中心到面板中线的距离相等
    # （允许 splitter 分割条宽度的舍入误差）
    from PyQt6.QtWidgets import QStyle
    handle = panel._splitter.style().pixelMetric(
        QStyle.PixelMetric.PM_SplitterWidth)
    center_x = panel.width() / 2
    assert abs((center_x - left_pos.x()) - (right_pos.x() - center_x)) <= handle

    panel._set_view_mode("full")
    assert panel._left_shell.isVisible()
    assert not panel._right_shell.isVisible()
    assert len(ctrl_buttons(panel._combat_ctrl_row)) == 2
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
