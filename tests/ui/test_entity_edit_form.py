"""场景实体编辑表单。"""

from PyQt6.QtWidgets import (
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QWidget,
)

from lvjiang.ui.scene_editor.entity_edit_form import (
    add_dialog_action_row,
    validate_activation_key_edit,
)


def test_activation_key_error_is_deferred_and_concise(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    form = QFormLayout(parent)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    error_label = add_dialog_action_row(form, buttons)
    edit = QLineEdit()

    edit.setText("ES")
    assert error_label.text() == ""

    assert validate_activation_key_edit(edit, error_label) is False
    assert error_label.text() == "未知按键名：ES"
    assert not error_label.isHidden()

    edit.setText("ESC")
    assert validate_activation_key_edit(edit, error_label) is True
    assert error_label.text() == ""
