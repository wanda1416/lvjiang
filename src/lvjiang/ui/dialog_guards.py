"""Shared close guards for non-modal tool dialogs."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from ..i18n import tr
from .button_styles import exec_styled_message_box


class EscapeCloseConfirmationMixin:
    """Require confirmation for Escape, while leaving title-bar close unchanged."""

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event is not None and event.key() == Qt.Key.Key_Escape:
            box = QMessageBox(self)
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
        super().keyPressEvent(event)


__all__ = ["EscapeCloseConfirmationMixin"]
