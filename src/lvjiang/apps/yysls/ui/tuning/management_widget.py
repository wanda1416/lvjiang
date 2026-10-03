"""调律管理：固定历史首页与按 task_run_id 隔离的运行页。"""
from __future__ import annotations

from PyQt6.QtWidgets import QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget

from .....i18n import tr
from ...core.tuning_history.repository import TuningHistoryRepository
from .history_widget import TuningHistoryWidget
from .progress_hub import TuningProgressHub
from .progress_widget import TuningProgressWidget


class TuningManagementWidget(QWidget):
    def __init__(self, parent=None, *,
                 history_repository: TuningHistoryRepository | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.tabCloseRequested.connect(self._close_run_page)
        self.history_widget = TuningHistoryWidget(history_repository)
        self._tabs.addTab(self.history_widget, tr("历史记录"))
        self._hide_close_button(0)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs)
        self._pages: dict[str, TuningProgressWidget] = {}
        self._hubs: dict[str, TuningProgressHub] = {}
        self._terminal: set[str] = set()
        self._titles: dict[str, str] = {}
        self._last_run_id = ""

    @property
    def progress_widget(self) -> TuningProgressWidget | None:
        """迁移期兼容：返回最近创建的任务页。"""
        return self._pages.get(self._last_run_id)

    def register_run(
        self, task_run_id: str, hub: TuningProgressHub, *, title: str,
    ) -> TuningProgressWidget:
        """为一次自动调律创建不可复用的独立进度页。"""
        existing = self._pages.get(task_run_id)
        if existing is not None:
            return existing
        page = TuningProgressWidget(hub)
        page.reset_state()
        self._pages[task_run_id] = page
        self._hubs[task_run_id] = hub
        self._titles[task_run_id] = title
        self._last_run_id = task_run_id
        index = self._tabs.addTab(page, title)
        self._hide_close_button(index)
        hub.tuning_finished.connect(
            lambda _info, run_id=task_run_id: self.mark_run_done(run_id))
        self._tabs.setCurrentWidget(page)
        return page

    def mark_run_done(self, task_run_id: str, status: str = "completed") -> None:
        page = self._pages.get(task_run_id)
        if page is None:
            return
        page.mark_done()
        self._terminal.add(task_run_id)
        index = self._tabs.indexOf(page)
        if index >= 0:
            status_text = {
                "completed": tr("已完成"),
                "interrupted": tr("已中断"),
                "failed": tr("失败"),
            }.get(status, tr("已完成"))
            self._tabs.setTabText(
                index, f"{self._titles.get(task_run_id, tr('调律'))} · {status_text}")
            self._show_close_button(index)
        self.history_widget.refresh()

    def set_run_paused(self, task_run_id: str, paused: bool) -> None:
        """只更新指定运行页，避免当前视角误改另一个目标的提示。"""
        page = self._pages.get(task_run_id)
        if page is not None:
            page.set_paused(paused)

    def reconnect(self, hub: TuningProgressHub) -> None:
        """旧调用兼容；正式运行必须使用 :meth:`register_run`。"""
        self.register_run(f"legacy-{id(hub)}", hub, title=tr("当前任务"))

    def reset_state(self) -> None:
        page = self.progress_widget
        if page is not None:
            page.reset_state()
            self._tabs.setCurrentWidget(page)

    def mark_done(self) -> None:
        """兼容原进度组件接口，由调律页的自动化状态回调调用。"""
        if self._last_run_id:
            self.mark_run_done(self._last_run_id)

    def set_paused(self, paused: bool) -> None:
        """将暂停状态转发给当前任务页。"""
        page = self.progress_widget
        if page is not None:
            page.set_paused(paused)

    def _on_tab_changed(self, index: int) -> None:
        if self._tabs.widget(index) is self.history_widget:
            self.history_widget.refresh()

    def _close_run_page(self, index: int) -> None:
        page = self._tabs.widget(index)
        if page is self.history_widget:
            return
        run_id = next(
            (key for key, value in self._pages.items() if value is page), "")
        if not run_id or run_id not in self._terminal:
            return
        self._tabs.removeTab(index)
        self._pages.pop(run_id, None)
        self._hubs.pop(run_id, None)
        self._titles.pop(run_id, None)
        self._terminal.discard(run_id)
        page.deleteLater()

    def _hide_close_button(self, index: int) -> None:
        bar = self._tabs.tabBar()
        bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, None)
        bar.setTabButton(index, QTabBar.ButtonPosition.LeftSide, None)

    def _show_close_button(self, index: int) -> None:
        button = QToolButton(self._tabs.tabBar())
        button.setText("×")
        button.setToolTip(tr("关闭已结束的任务页"))
        button.setAutoRaise(True)
        page = self._tabs.widget(index)
        button.clicked.connect(
            lambda _checked=False, page=page:
            self._close_page_widget(page))
        self._tabs.tabBar().setTabButton(
            index, QTabBar.ButtonPosition.RightSide, button)

    def _close_page_widget(self, page: QWidget | None) -> None:
        if page is None:
            return
        index = self._tabs.indexOf(page)
        if index >= 0:
            self._close_run_page(index)
