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
