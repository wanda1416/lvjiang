"""一次问齐多个字段的小表单对话框。

新建脚本、新建地图这类操作需要 2–3 个输入，逐个弹 ``QInputDialog`` 会让人
连按三次回车、错一个还得从头来。这里用一个表单一次问完，校验错误就地
显示在表单底部，不再另弹警告框。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


@dataclass
class FormField:
    """一个输入项。``choices`` 非空时渲染为下拉框，值为选项的 data。"""

    key: str
    label: str
    default: str = ""
    placeholder: str = ""
    choices: Sequence[tuple[str, str]] = field(default_factory=tuple)  # (data, 显示文本)
    #: 单字段校验：返回错误文案，None 表示通过
    validator: Callable[[str], str | None] | None = None


class FormDialog(QDialog):
    """表单对话框；``validate`` 做跨字段校验，返回错误文案或 None。"""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        fields: Sequence[FormField],
        *,
        validate: Callable[[dict[str, str]], str | None] | None = None,
        hint: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._fields = list(fields)
        self._validate = validate
        self._widgets: dict[str, QLineEdit | QComboBox] = {}

        root = QVBoxLayout(self)
        if hint:
            lbl_hint = QLabel(hint)
            lbl_hint.setWordWrap(True)
            lbl_hint.setStyleSheet("color: palette(mid);")
            root.addWidget(lbl_hint)
        form = QFormLayout()
        for item in self._fields:
            widget: QLineEdit | QComboBox
            if item.choices:
                combo = QComboBox()
                for data, text in item.choices:
                    combo.addItem(text, data)
                index = combo.findData(item.default)
                combo.setCurrentIndex(max(index, 0))
                widget = combo
            else:
                edit = QLineEdit(item.default)
                if item.placeholder:
                    edit.setPlaceholderText(item.placeholder)
                widget = edit
            self._widgets[item.key] = widget
            form.addRow(item.label, widget)
        root.addLayout(form)

        self.lbl_error = QLabel("")
        self.lbl_error.setWordWrap(True)
        self.lbl_error.setStyleSheet("color: #c62828;")
        self.lbl_error.hide()
        root.addWidget(self.lbl_error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.setMinimumWidth(420)

        first = self._widgets[self._fields[0].key] if self._fields else None
        if first is not None:
            first.setFocus()

    def values(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in self._fields:
            widget = self._widgets[item.key]
            if isinstance(widget, QComboBox):
                result[item.key] = str(widget.currentData() or "")
            else:
                result[item.key] = widget.text().strip()
        return result

    def set_value(self, key: str, value: str) -> None:
        widget = self._widgets[key]
        if isinstance(widget, QComboBox):
            widget.setCurrentIndex(max(widget.findData(value), 0))
        else:
            widget.setText(value)

    def _on_accept(self) -> None:
        values = self.values()
        for item in self._fields:
            if item.validator is not None:
                error = item.validator(values[item.key])
                if error:
                    self._show_error(error, item.key)
                    return
        if self._validate is not None:
            error = self._validate(values)
            if error:
                self._show_error(error)
                return
        self.accept()

    def _show_error(self, text: str, focus_key: str | None = None) -> None:
        self.lbl_error.setText(text)
        self.lbl_error.show()
        if focus_key is not None:
            self._widgets[focus_key].setFocus()


def ask_form(
    parent: QWidget | None,
    title: str,
    fields: Sequence[FormField],
    *,
    validate: Callable[[dict[str, str]], str | None] | None = None,
    hint: str = "",
) -> dict[str, str] | None:
    """弹出表单，确认返回 ``{key: value}``，取消返回 None。"""
    dialog = FormDialog(parent, title, fields, validate=validate, hint=hint)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.values()
