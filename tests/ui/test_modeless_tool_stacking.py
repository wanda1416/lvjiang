"""非模态工具窗不锁死在主界面之上

带 parent 的 QDialog 在窗管层是宿主的**瞬态窗口**，永远压在主界面上方——点主界面
也换不回来。而这些工具（场景管理、图库管理、图像识别、任务历史…）是长时间并排使用
的：一边看场景区域、一边在主界面刷新截图。所以它们要按点击顺序排序，谁被点谁在上。

仍然保留 parent：生命周期跟随、居中定位、关闭时统一收尾都依赖它。
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QWidget

from lvjiang.ui.main.menu_ops import MenuOpsMixin

pytestmark = pytest.mark.usefixtures('qapp')


class _Host(QWidget, MenuOpsMixin):
    pass


def _host(qtbot) -> _Host:
    host = _Host()
    qtbot.addWidget(host)
    return host


def test_modeless_tool_is_an_independent_window(qtbot):
    host = _host(qtbot)
    dialog = host._show_modeless_tool("probe", lambda: QDialog(host))
    assert dialog is not None
    qtbot.addWidget(dialog)

    flags = dialog.windowFlags()
    type_bits = flags & Qt.WindowType.WindowType_Mask
    assert type_bits == Qt.WindowType.Window, (
        "工具窗必须是独立顶层窗口，瞬态 Dialog 会被窗管压在主界面之上")
    # parent 不能丢：生命周期、定位与统一关闭都靠它
    assert dialog.parent() is host


def test_modeless_tool_stays_non_modal(qtbot):
    """改窗口类型不能顺手把非模态属性也改掉。"""
    host = _host(qtbot)
    dialog = host._show_modeless_tool("probe", lambda: QDialog(host))
    assert dialog is not None
    qtbot.addWidget(dialog)
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.WindowModality.NonModal


def test_repeat_open_reuses_the_same_window(qtbot):
    """重复触发只前置现有窗口，不该再建一个。"""
    host = _host(qtbot)
    first = host._show_modeless_tool("probe", lambda: QDialog(host))
    second = host._show_modeless_tool("probe", lambda: QDialog(host))
    assert first is second
