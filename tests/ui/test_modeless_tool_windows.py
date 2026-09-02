"""Main-window modeless tool lifetime and re-entry guard tests."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QMainWindow, QMessageBox

import lvjiang.ui.dialog_guards as dialog_guards
from lvjiang.ui.dialog_guards import EscapeCloseConfirmationMixin
from lvjiang.ui.main.menu_ops import MenuOpsMixin
from lvjiang.ui.reference.dialog import ReferenceManagerDialog
from lvjiang.ui.scene_editor.dialog import SceneEditorDialog
from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog
from lvjiang.ui.scripts.record_dialog import ScriptRecordDialog


class _Host(MenuOpsMixin, QMainWindow):
    pass


def test_modeless_tool_is_single_instance_and_can_reopen(qtbot, monkeypatch):
    host = _Host()
    qtbot.addWidget(host)
    created = []
    finished = []

    def factory():
        dialog = QDialog(host)
        created.append(dialog)
        return dialog

    first = host._show_modeless_tool("scene", factory, finished.append)
    monkeypatch.setattr(first, "raise_", lambda: None)
    monkeypatch.setattr(first, "activateWindow", lambda: None)
    second = host._show_modeless_tool("scene", factory)

    assert first is second
    assert len(created) == 1
    assert first.isModal() is False
    assert first.windowModality() == Qt.WindowModality.NonModal

    first.close()
    qtbot.waitUntil(lambda: "scene" not in host._modeless_tool_windows)
    assert finished == [first]
    third = host._show_modeless_tool("scene", factory)
    assert third is not first
    assert len(created) == 2


def test_modeless_tool_guard_blocks_reentrant_factory(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    nested_results = []

    def factory():
        nested_results.append(host._show_modeless_tool("record", factory))
        return QDialog(host)

    dialog = host._show_modeless_tool("record", factory)

    assert dialog is not None
    assert nested_results == [None]
    assert host._modeless_tool_windows == {"record": dialog}


def test_close_modeless_tools_honors_rejected_close(qtbot):
    class RefusingDialog(QDialog):
        def closeEvent(self, event):  # noqa: N802
            event.ignore()

    host = _Host()
    qtbot.addWidget(host)
    dialog = host._show_modeless_tool(
        "editor", lambda: RefusingDialog(host),
    )
    assert dialog is not None and dialog.isVisible()

    assert host._close_modeless_tools() is False
    assert dialog.isVisible()


def test_escape_confirms_but_title_bar_close_does_not(qtbot, monkeypatch):
    class GuardedDialog(EscapeCloseConfirmationMixin, QDialog):
        pass

    dialog = GuardedDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    responses = iter((
        QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    ))
    confirmations = []

    def confirm(box):
        confirmations.append(box.text())
        return next(responses)

    monkeypatch.setattr(dialog_guards, "exec_styled_message_box", confirm)

    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.isVisible()
    assert confirmations == ["确定要关闭此窗口吗？"]

    dialog.close()  # 模拟右上角 X，不经过 Esc 通用确认。
    assert not dialog.isVisible()
    assert len(confirmations) == 1

    dialog.show()
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert len(confirmations) == 2


def test_four_modeless_tools_share_escape_guard():
    for dialog_type in (
        SceneEditorDialog,
        ReferenceManagerDialog,
        ScriptRecordDialog,
        ScriptEditorDialog,
    ):
        assert issubclass(dialog_type, EscapeCloseConfirmationMixin)
