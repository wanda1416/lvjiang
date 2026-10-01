"""非模态工具窗不锁死在主界面之上

"永远压在宿主之上"来自**父子关系**，不是窗口类型标志：带 parent 的顶层窗口在 Windows
上是 owned window，而 owned window 的 Z 序永远高于 owner。所以只改 Dialog/Window 标志
位没有用（第一版就是这么写的，实机依旧压在上面），必须真的脱开父子关系。

这些工具（场景管理、图库管理、脚本工作台、任务历史、地图管理）是长时间并排使用的：
一边看场景区域、一边在主界面刷新截图。

脱开父子关系后，回主窗口的路径改成显式的 ``_owner_window``——脚本工作台要读当前用户、
连接后端与设备状态，不能靠 ``parent()``。
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


def test_modeless_tool_has_no_parent(qtbot):
    """关键断言：父子关系必须断开，否则窗管照旧把它压在主界面之上。"""
    host = _host(qtbot)
    dialog = host._show_modeless_tool("probe", lambda: QDialog(host))
    assert dialog is not None
    qtbot.addWidget(dialog)

    assert dialog.parent() is None
    flags = dialog.windowFlags()
    assert flags & Qt.WindowType.WindowType_Mask == Qt.WindowType.Window


def test_owner_window_is_still_reachable(qtbot):
    """脱开父子关系不等于失联：脚本工作台靠它读用户与连接状态。"""
    host = _host(qtbot)
    dialog = host._show_modeless_tool("probe", lambda: QDialog(host))
    assert dialog is not None
    qtbot.addWidget(dialog)
    assert dialog._owner_window is host


def test_scene_editor_resolves_owner_without_parent(qtbot):
    """场景编辑器的 _owner_main_window 要能在无 parent 时拿到宿主。"""
    from lvjiang.ui.scene_editor.script_ops import ScriptOpsMixin

    class _Editor(QDialog, ScriptOpsMixin):
        pass

    host = _host(qtbot)
    editor = _Editor()
    qtbot.addWidget(editor)
    assert editor._owner_main_window() is None
    editor._owner_window = host
    assert editor._owner_main_window() is host


def test_modeless_tool_stays_non_modal(qtbot):
    """改窗口归属不能顺手把非模态属性也改掉。"""
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
