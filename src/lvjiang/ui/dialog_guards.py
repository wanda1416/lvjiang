"""Shared close guards for non-modal tool dialogs."""

from typing import cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

from ..i18n import tr
from .button_styles import exec_styled_message_box


class EscapeCloseConfirmationMixin:
    """Require confirmation for Escape, while leaving title-bar close unchanged.

    对话框可实现 ``_escape_needs_confirmation() -> bool``：返回 False 时
    Esc 直接关闭（没有未保存内容就不该多问一句）；未实现则一律确认。
    """

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event is not None and event.key() == Qt.Key.Key_Escape:
            needs = getattr(self, "_escape_needs_confirmation", None)
            if callable(needs) and not needs():
                self.close()  # type: ignore[attr-defined]
                event.accept()
                return
            box = QMessageBox(cast(QWidget, self))
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle(tr("确认关闭"))
            box.setText(tr("确定要关闭此窗口吗？"))
            box.setStandardButtons(
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if exec_styled_message_box(box) == QMessageBox.StandardButton.Yes:
                self.close()
            event.accept()
            return
        super().keyPressEvent(event)  # type: ignore[misc]


__all__ = ["EscapeCloseConfirmationMixin"]
