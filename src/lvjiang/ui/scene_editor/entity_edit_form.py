"""Region / Point 编辑表单的共享布局组件。"""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ...core.key_names import normalize_key
from ...i18n import tr


def add_attribute_row(
    form: QFormLayout,
    is_text_check: QCheckBox,
    is_clickable_check: QCheckBox,
) -> None:
    """把实体布尔属性收拢为一行，避免两个孤立复选框占满表单。"""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(is_text_check)
    layout.addWidget(is_clickable_check)
    layout.addStretch()
    form.addRow(tr("属性:"), row)


def add_activation_key_row(
    form: QFormLayout,
    activation_key: str,
    *,
    enabled: bool,
) -> QLineEdit:
    """添加布局级激活按键输入；空值表示使用默认坐标点击。"""
    edit = QLineEdit(activation_key)
    edit.setPlaceholderText(tr("留空则点击坐标，如 SPACE / ESC / R"))
    edit.setEnabled(enabled)
    if enabled:
        edit.setToolTip(tr("仅作用于当前布局；click 将改为按下该按键"))
    else:
        edit.setToolTip(tr("请先在当前布局中放置该实体，再设置激活按键"))
    form.addRow(tr("按键:"), edit)
    return edit


def add_definition_separator(form: QFormLayout) -> None:
    """分隔布局级激活属性与场景定义归属属性。"""
    separator = QFrame()
    separator.setFrameShape(QFrame.Shape.NoFrame)
    separator.setFixedHeight(9)
    separator.setStyleSheet("border-top: 1px dashed palette(mid);")
    form.addRow(separator)


def add_dialog_action_row(
    form: QFormLayout,
    buttons: QDialogButtonBox,
    leading_button: QPushButton | None = None,
) -> QLabel:
    """添加固定高度的底部校验提示和对话框按钮。"""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    error_label = QLabel()
    error_label.setStyleSheet("color: #c62828;")
    layout.addWidget(error_label)
    layout.addStretch()
    if leading_button is not None:
        layout.addWidget(leading_button)
    layout.addWidget(buttons)
    form.addRow(row)
    return error_label


def validate_activation_key_edit(edit: QLineEdit, error_label: QLabel) -> bool:
    """提交时校验按键输入，并用固定的简短文案提示。"""
    activation = edit.text().strip()
    if activation:
        try:
            normalize_key(activation)
        except ValueError:
            error_label.setText(f"未知按键名：{activation}")
            return False
    error_label.clear()
    return True
