"""脚本编辑器按住 Ctrl 点击 call，打开对应 def 所在文件和行。"""

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent, QTextCursor
from PyQt6.QtWidgets import QApplication, QMessageBox

from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver


def _resolver(tmp_path, monkeypatch) -> ConfigResolver:
    resolver = ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=True)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    return resolver


def _click_name(editor, token: str) -> None:
    text = editor.toPlainText()
    line_no = next(i for i, line in enumerate(text.splitlines()) if f"call {token}" in line)
    block = editor.document().findBlockByNumber(line_no)
    column = block.text().index(token) + 1
    cursor = QTextCursor(block)
    cursor.setPosition(block.position() + column)
    editor.setTextCursor(cursor)
    editor.ensureCursorVisible()
    point = editor.cursorRect(cursor).center()
    local = QPointF(point)
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        local,
        QPointF(editor.viewport().mapToGlobal(QPoint(int(local.x()), int(local.y())))),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
    )
    QApplication.sendEvent(editor.viewport(), event)


def test_ctrl_click_opens_imported_definition(qtbot, tmp_path, monkeypatch) -> None:
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = _resolver(tmp_path, monkeypatch)
    resolver.write_entity(
        "workflows/subcall/navigation.wf",
        "def navigate($target)\n    return $target\nend\n",
    )
    resolver.write_entity(
        "workflows/main.wf",
        'import "subcall/navigation.wf"\ncall navigate("home")\n',
    )
    dialog = ScriptEditorDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.tree.setCurrentItem(dialog._file_items["main.wf"])
    qtbot.waitUntil(lambda: dialog.editor.toPlainText().startswith("import"))

    _click_name(dialog.editor, "navigate")

    assert dialog._current is not None
    assert dialog._current.rel_path == "subcall/navigation.wf"
    assert dialog.editor.textCursor().blockNumber() == 0


def test_ctrl_click_keeps_unsaved_script_when_discard_declined(
        qtbot, tmp_path, monkeypatch) -> None:
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = _resolver(tmp_path, monkeypatch)
    resolver.write_entity(
        "workflows/subcall/navigation.wf",
        "def navigate($target)\n    return $target\nend\n",
    )
    resolver.write_entity(
        "workflows/main.wf",
        'import "subcall/navigation.wf"\ncall navigate("home")\n',
    )
    dialog = ScriptEditorDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.waitExposed(dialog)
    dialog.tree.setCurrentItem(dialog._file_items["main.wf"])
    qtbot.waitUntil(lambda: dialog.editor.toPlainText().startswith("import"))
    dialog.editor.appendPlainText("# dirty")
    asked: list[str] = []

    def decline(*_args, **_kwargs):
        asked.append("discard")
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", decline)

    _click_name(dialog.editor, "navigate")

    assert asked == ["discard"]
    assert dialog._current is not None
    assert dialog._current.rel_path == "main.wf"
    assert "# dirty" in dialog.editor.toPlainText()
