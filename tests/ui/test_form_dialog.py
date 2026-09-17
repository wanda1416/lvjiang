"""表单对话框：一次问齐多个字段，校验错误就地显示，不再逐个弹框。"""

from lvjiang.ui.form_dialog import FormDialog, FormField


def test_form_dialog_collects_values_and_blocks_on_field_error(qtbot):
    dialog = FormDialog(None, "新建", [
        FormField("sid", "id", validator=lambda v: "id 不能为空" if not v else None),
        FormField("name", "显示名", placeholder="留空 = id"),
        FormField("mode", "模式", "b", choices=[("a", "甲"), ("b", "乙")]),
    ])
    qtbot.addWidget(dialog)
    accepted = []
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog._on_accept()                       # id 为空 → 不关闭，显示错误
    assert accepted == [] and dialog.lbl_error.isVisibleTo(dialog)
    assert dialog.lbl_error.text() == "id 不能为空"

    dialog.set_value("sid", " demo ")
    dialog._on_accept()
    assert accepted == [True]
    assert dialog.values() == {"sid": "demo", "name": "", "mode": "b"}


def test_form_dialog_cross_field_validation(qtbot):
    dialog = FormDialog(None, "x", [FormField("a", "A"), FormField("b", "B")],
                        validate=lambda v: "A 与 B 不能相同" if v["a"] == v["b"] else None)
    qtbot.addWidget(dialog)
    dialog.set_value("a", "1")
    dialog.set_value("b", "1")

    dialog._on_accept()

    assert dialog.lbl_error.text() == "A 与 B 不能相同"


def test_escape_closes_clean_tool_dialog_without_asking(qtbot, monkeypatch):
    from PyQt6.QtCore import QEvent, Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from lvjiang.ui.dialog_guards import EscapeCloseConfirmationMixin

    class _Tool(EscapeCloseConfirmationMixin, QDialog):
        dirty = False

        def _escape_needs_confirmation(self) -> bool:
            return self.dirty

    asked = []
    monkeypatch.setattr(
        "lvjiang.ui.dialog_guards.exec_styled_message_box",
        lambda box: asked.append(box) or QMessageBox.StandardButton.No)
    dialog = _Tool()
    qtbot.addWidget(dialog)
    dialog.show()
    esc = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)

    dialog.keyPressEvent(esc)
    assert asked == [] and not dialog.isVisible()      # 干净：直接关

    dialog.show()
    dialog.dirty = True
    dialog.keyPressEvent(esc)
    assert len(asked) == 1 and dialog.isVisible()       # 有改动：确认后（No）保持打开
