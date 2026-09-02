"""Per-tab, process-local workflow execution user selector."""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

from ..i18n import tr


class ExecutionUserSelector(QWidget):
    """Select a fixed execution user or resolve the active user at start time.

    The selection intentionally lives only in the widget.  It never reads from or
    writes to session/config storage, so every application start defaults to
    ``follow current user``.
    """

    def __init__(self, user_manager, parent=None):
        super().__init__(parent)
        self._user_manager = user_manager
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(QLabel(tr("执行用户：")))
        self.combo = QComboBox()
        self.combo.setObjectName("execution_user_combo")
        self.combo.setMinimumHeight(32)
        self.combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo.setMinimumContentsLength(12)
        layout.addWidget(self.combo, stretch=1)
        self.refresh_users()

    def refresh_users(self) -> None:
        """Refresh available users while preserving a valid fixed selection."""
        selected = self.combo.currentData() if self.combo.count() else None
        users = list(self._user_manager.list_users())
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(tr("跟随当前用户"), None)
        for username in users:
            self.combo.addItem(str(username), str(username))
        if selected in users:
            self.combo.setCurrentIndex(self.combo.findData(selected))
        else:
            self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

    def resolve_username(self) -> str:
        """Resolve the launch-time username; empty means no valid user exists."""
        fixed = self.combo.currentData()
        if fixed is None:
            return str(self._user_manager.get_active_user_name() or "")
        username = str(fixed)
        return username if username in self._user_manager.list_users() else ""


__all__ = ["ExecutionUserSelector"]
