"""调律管理：当前任务进度与历史记录的统一入口。"""
from __future__ import annotations

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from .....i18n import tr
from .history_widget import TuningHistoryWidget
from .progress_hub import TuningProgressHub
from .progress_widget import TuningProgressWidget


class TuningManagementWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tabs = QTabWidget()
        self.progress_widget = TuningProgressWidget()
        self.history_widget = TuningHistoryWidget()
        self._tabs.addTab(self.progress_widget, tr("当前任务"))
        self._tabs.addTab(self.history_widget, tr("历史记录"))
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)
        self._hub: TuningProgressHub | None = None

    def reconnect(self, hub: TuningProgressHub) -> None:
        if self._hub is not None:
            try:
                self._hub.tuning_finished.disconnect(self._on_tuning_finished)
            except TypeError:
                pass
        self._hub = hub
        self.progress_widget.reconnect(hub)
        hub.tuning_finished.connect(self._on_tuning_finished)

    def reset_state(self) -> None:
        self.progress_widget.reset_state()
        self._tabs.setCurrentWidget(self.progress_widget)

    def mark_done(self) -> None:
        """兼容原进度组件接口，由调律页的自动化状态回调调用。"""
        self.progress_widget.mark_done()

    def set_paused(self, paused: bool) -> None:
        """将暂停状态转发给当前任务页。"""
        self.progress_widget.set_paused(paused)

    def _on_tuning_finished(self, _info: dict) -> None:
        self.history_widget.refresh()

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self.history_widget:
            self.history_widget.refresh()
