"""插入当前实体：[scene].[entity]

原来的「输入当前场景」只插一个场景名，脚本里引用实体从来都是两段式的，用户还得自己
补 `.[...]`，省不了多少事还容易漏括号。现在直接插完整引用。

启用条件两个都要成立：脚本编辑区握着光标、右侧实体区确实选中了一项。按钮自身必须是
NoFocus——否则点一下焦点就被它抢走，光标条件立刻不成立，连插第二次都做不到。
"""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QTextEdit, QWidget

from lvjiang.ui.scene_editor.script_ops import _InsertEntityButton

pytestmark = pytest.mark.usefixtures('qapp')


def _button(qtbot, scene: str, entity: str):
    """返回 host 一并持有：没有 Python 引用时宿主会被回收，连带销毁子控件"""
    host = QWidget()
    qtbot.addWidget(host)
    editor = QTextEdit(host)
    state = {"scene": scene, "entity": entity}
    button = _InsertEntityButton(
        lambda: state["scene"], lambda: state["entity"], host)
    button.set_target(editor)
    host.show()
    return host, button, editor, state


class TestReference:
    def test_builds_two_part_reference(self, qtbot):
        _host, button, _, _ = _button(qtbot, "game_menu_page", "menu")
        assert button.reference() == "[game_menu_page].[menu]"

    def test_blank_without_entity(self, qtbot):
        _host, button, _, _ = _button(qtbot, "game_menu_page", "")
        assert button.reference() == ""

    def test_blank_without_scene(self, qtbot):
        _host, button, _, _ = _button(qtbot, "", "menu")
        assert button.reference() == ""


class TestEnablement:
    def test_disabled_until_editor_holds_cursor(self, qtbot):
        _host, button, editor, _ = _button(qtbot, "game_menu_page", "menu")
        assert not button.isEnabled()        # 构造时就是禁用的

        editor.setFocus()
        qtbot.waitUntil(editor.hasFocus, timeout=1000)
        button.refresh_enabled()
        assert button.isEnabled()

    def test_disabled_when_no_entity_selected(self, qtbot):
        _host, button, editor, state = _button(qtbot, "game_menu_page", "menu")
        editor.setFocus()
        qtbot.waitUntil(editor.hasFocus, timeout=1000)
        state["entity"] = ""
        button.refresh_enabled()
        assert not button.isEnabled()

    def test_button_does_not_steal_focus(self, qtbot):
        """NoFocus 是功能要求：抢了焦点就只能插一次。"""
        _host, button, _, _ = _button(qtbot, "game_menu_page", "menu")
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus


class TestInsert:
    def test_inserts_at_cursor(self, qtbot):
        _host, button, editor, _ = _button(qtbot, "game_menu_page", "menu")
        editor.setPlainText("click ")
        cursor = editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        editor.setTextCursor(cursor)

        button._on_clicked()

        assert editor.toPlainText() == "click [game_menu_page].[menu]"

    def test_can_insert_twice(self, qtbot):
        """焦点不被抢走，所以连续插入两次是可行的。"""
        _host, button, editor, _ = _button(qtbot, "a", "b")
        button._on_clicked()
        button._on_clicked()
        assert editor.toPlainText() == "[a].[b][a].[b]"

    def test_does_nothing_without_reference(self, qtbot):
        _host, button, editor, state = _button(qtbot, "a", "")
        button._on_clicked()
        assert editor.toPlainText() == ""
        state["entity"] = "b"
        button._on_clicked()
        assert editor.toPlainText() == "[a].[b]"


class TestFocusWatchLifetime:
    """焦点监听不能活过对话框

    focusChanged 挂在 QApplication 上，生命周期比对话框长得多。最初用 lambda 连，
    lambda 没有关联的 QObject，连接在窗口关闭后依然存在，于是之后每次焦点变化都去
    碰已销毁的 QTextEdit —— 退出场景管理后仍然持续崩溃，而且崩在跟场景管理毫无关系
    的操作上。
    """

    def test_connection_is_dropped_on_close(self, qtbot):
        from PyQt6.QtWidgets import QApplication

        from lvjiang.core.layout_manager import LayoutConfigManager
        from lvjiang.ui.scene_editor import SceneEditorDialog

        app = QApplication.instance()
        assert app is not None
        before = app.receivers(app.focusChanged)

        dialog = SceneEditorDialog(
            layout_manager=LayoutConfigManager(), refresh_callback=lambda: None)
        qtbot.addWidget(dialog)
        assert app.receivers(app.focusChanged) == before + 1

        dialog.close()
        assert app.receivers(app.focusChanged) == before, (
            "关闭后仍挂着监听，之后每次焦点变化都会回调到已销毁的控件")

    def test_focus_changes_after_close_do_not_raise(self, qtbot):
        """真实现场：退出场景管理后继续在别处切焦点。"""
        from PyQt6.QtWidgets import QApplication, QLineEdit

        from lvjiang.core.layout_manager import LayoutConfigManager
        from lvjiang.ui.scene_editor import SceneEditorDialog

        host = QWidget()
        qtbot.addWidget(host)
        first, second = QLineEdit(host), QLineEdit(host)
        host.show()

        dialog = SceneEditorDialog(
            layout_manager=LayoutConfigManager(), refresh_callback=lambda: None)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.close()

        app = QApplication.instance()
        assert app is not None
        for _ in range(3):
            first.setFocus()
            app.processEvents()
            second.setFocus()
            app.processEvents()
