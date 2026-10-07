"""统一任务历史与批量历史查询窗口。"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from typing import TypeGuard

from loguru import logger
from PyQt6.QtCore import QDate, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.combo_box import AutoWidthComboBox, ComboWidthMode

from ..core.config import load_ui_page_state, update_ui_page_state
from ..core.daily_history import (
    BatchRunRecord,
    TaskHistoryRepository,
    TaskRunRecord,
    resolve_history_path,
)
from ..i18n import tr
from .button_styles import apply_button_style
from .history_viewer import HistoryViewer

_STATUS_LABELS = {
    "running": tr("进行中"), "completed": tr("已完成"),
    "interrupted": tr("已中断"), "failed": tr("失败"),
    "skipped": tr("跳过"),
}
_SOURCE_LABELS = {"single": tr("单独运行"), "batch": tr("批量运行")}
_SCOPE_LABELS = {"daily": tr("日常"), "dedicated": tr("专用")}
_LINK_BUTTON_STYLE = (
    "QPushButton { background: transparent; border: none; "
    "color: palette(link); padding: 0 2px; }"
    "QPushButton:hover { text-decoration: underline; }"
    "QPushButton:pressed { color: palette(highlight); }"
)


class _HistoryChoices(QWidget):
    """历史候选的搜索只影响可见性，勾选值始终保留。"""

    def __init__(self, title: str):
        super().__init__()
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self._label = QLabel()
        header.addWidget(self._label, 1)
        clear = QPushButton(tr("清除"))
        clear.setCursor(Qt.CursorShape.PointingHandCursor)
        clear.setStyleSheet(_LINK_BUTTON_STYLE)
        clear.setToolTip(tr("清除勾选，改为全部"))
        clear.clicked.connect(self.clear_checks)
        header.addWidget(clear)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("搜索") + title)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.items = QListWidget()
        self.items.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.items.setMinimumHeight(self.items.fontMetrics().height() * 4 + self.items.frameWidth() * 2)
        self.items.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.items.itemChanged.connect(self._update_label)
        layout.addWidget(self.items)
        self._update_label()

    def add_choice(self, text: str, key: str, tooltip: str = "") -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setToolTip(tooltip or text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        self.items.addItem(item)
        self._filter()

    def checked_keys(self) -> list[str]:
        return [str(item.data(Qt.ItemDataRole.UserRole))
                for index in range(self.items.count())
                if (item := self.items.item(index)) is not None
                and item.checkState() == Qt.CheckState.Checked]

    def clear_checks(self) -> None:
        for index in range(self.items.count()):
            item = self.items.item(index)
            if item is not None:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._update_label()

    def _update_label(self) -> None:
        count = len(self.checked_keys())
        selection = tr("已选") + f" {count} " + tr("项") if count else tr("全部")
        self._label.setText(f"{self._title} · {selection}")

    def _filter(self) -> None:
        query = self.search.text().strip().casefold()
        for index in range(self.items.count()):
            item = self.items.item(index)
            if item is not None:
                item.setHidden(query not in (item.text() + " " + item.toolTip()).casefold())


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _format_time(value: str) -> str:
    return value.replace("T", " ")[:23] if value else "—"


class DailyHistoryDialog(QDialog):
    """任务历史入口；批量记录可下钻到所属的单任务记录。"""

    def __init__(self, parent=None,
                 repository: TaskHistoryRepository | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("任务历史"))
        self.resize(1220, 660)
        self._repository = repository
        self._task_records: list[TaskRunRecord] = []
        self._batch_records: list[BatchRunRecord] = []
        self._batch_filter = ""
        state = load_ui_page_state("task_history")
        self._build_ui()
        self._restore_ui_state(state)
        self.refresh_options()

    def _restore_ui_state(self, state: dict) -> None:
        size = state.get("window_size")
        if self._valid_sizes(size):
            self.resize(*size)
        for key, splitter in (("task_splitter_sizes", self._task_splitter),
                              ("batch_splitter_sizes", self._batch_splitter)):
            sizes = state.get(key)
            if self._valid_sizes(sizes):
                splitter.setSizes(sizes)

    @staticmethod
    def _valid_sizes(value: object) -> TypeGuard[list[int]]:
        return (isinstance(value, list) and len(value) == 2
                and all(type(size) is int and size > 0 for size in value))

    def done(self, result: int) -> None:
        # QDialog 的窗口关闭、Esc 和 accept/reject 都经由 done 收尾。
        try:
            update_ui_page_state("task_history", {
                "window_size": [self.width(), self.height()],
                "task_splitter_sizes": self._task_splitter.sizes(),
                "batch_splitter_sizes": self._batch_splitter.sizes(),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"保存任务历史窗口状态失败: {exc}")
        super().done(result)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_task_page(), tr("任务历史"))
        self._tabs.addTab(self._build_batch_page(), tr("批量历史"))
        layout.addWidget(self._tabs)

    def _build_task_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        layout.addWidget(split)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._build_date_controls())
        self._task_filter_splitter = QSplitter(Qt.Orientation.Vertical)
        self._task_filter_splitter.setChildrenCollapsible(False)
        choices = QWidget()
        choices_layout = QHBoxLayout(choices)
        choices_layout.setContentsMargins(0, 0, 0, 0)
        self._users = _HistoryChoices(tr("用户"))
        self._tasks = _HistoryChoices(tr("任务"))
        choices_layout.addWidget(self._users, 1)
        choices_layout.addWidget(self._tasks, 1)
        self._task_filter_splitter.addWidget(choices)
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        batch_filter_row = QHBoxLayout()
        self._batch_filter_label = QLabel()
        self._batch_filter_label.setWordWrap(True)
        self._batch_filter_label.setVisible(False)
        batch_filter_row.addWidget(self._batch_filter_label, 1)
        self._clear_batch_filter_button = QPushButton(tr("返回全部任务"))
        self._clear_batch_filter_button.clicked.connect(self._clear_batch_filter)
        self._clear_batch_filter_button.setVisible(False)
        batch_filter_row.addWidget(self._clear_batch_filter_button)
        results_layout.addLayout(batch_filter_row)
        self._task_table = QTableWidget(0, 5)
        self._task_table.setHorizontalHeaderLabels([
            tr("开始时间"), tr("用户"), tr("任务"), tr("状态"), tr("耗时"),
        ])
        self._configure_table(self._task_table)
        self._task_table.itemSelectionChanged.connect(self._show_selected_task)
        self._task_table.cellDoubleClicked.connect(
            lambda _row, _column: self._open_task_result())
        results_layout.addWidget(self._task_table)
        self._task_filter_splitter.addWidget(results)
        self._task_filter_splitter.setStretchFactor(0, 0)
        self._task_filter_splitter.setStretchFactor(1, 1)
        self._task_filter_splitter.setSizes([170, 350])
        left_layout.addWidget(self._task_filter_splitter, 1)
        self._task_viewer = HistoryViewer()
        split.addWidget(left)
        split.addWidget(self._task_viewer)
        split.setSizes([540, 650])
        self._task_splitter = split
        apply_button_style(self._clear_batch_filter_button, variant="neutral")
        return page

    def _build_batch_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        layout.addWidget(split)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        dates = QHBoxLayout()
        dates.addWidget(QLabel(tr("开始日期")))
        self._batch_start_date = QDateEdit()
        self._batch_start_date.setCalendarPopup(True)
        self._batch_start_date.setDisplayFormat("yyyy-MM-dd")
        dates.addWidget(self._batch_start_date)
        dates.addWidget(QLabel(tr("结束日期")))
        self._batch_end_date = QDateEdit()
        self._batch_end_date.setCalendarPopup(True)
        self._batch_end_date.setDisplayFormat("yyyy-MM-dd")
        dates.addWidget(self._batch_end_date)
        dates.addStretch(1)
        left_layout.addLayout(dates)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("执行目标")))
        self._batch_target_kind = self._build_target_kind_combo()
        controls.addWidget(self._batch_target_kind)
        controls.addWidget(QLabel(tr("状态")))
        self._batch_status = self._build_status_combo()
        controls.addWidget(self._batch_status)
        controls.addStretch(1)
        self._batch_query_button = QPushButton(tr("查询"))
        self._batch_query_button.clicked.connect(self.refresh_batches)
        controls.addWidget(self._batch_query_button)
        left_layout.addLayout(controls)
        self._batch_table = QTableWidget(0, 5)
        self._batch_table.setHorizontalHeaderLabels([
            tr("开始时间"), tr("配置"), tr("状态"), tr("耗时"), tr("任务数"),
        ])
        self._configure_table(self._batch_table)
        self._batch_table.itemSelectionChanged.connect(self._show_selected_batch)
        self._batch_table.cellDoubleClicked.connect(
            lambda _row, _column: self._view_batch_tasks())
        left_layout.addWidget(self._batch_table, 1)
        self._view_batch_tasks_button = QPushButton(tr("查看全部单任务"))
        self._view_batch_tasks_button.clicked.connect(self._view_batch_tasks)
        self._view_batch_tasks_button.setEnabled(False)
        left_layout.addWidget(self._view_batch_tasks_button)
        self._batch_viewer = HistoryViewer(batch=True)
        split.addWidget(left)
        split.addWidget(self._batch_viewer)
        split.setSizes([540, 650])
        self._batch_splitter = split
        apply_button_style(self._batch_query_button, self._view_batch_tasks_button, variant="action")
        return page

    def _build_date_controls(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        dates = QHBoxLayout()
        dates.addWidget(QLabel(tr("开始日期")))
        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        dates.addWidget(self._start_date)
        dates.addWidget(QLabel(tr("结束日期")))
        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        dates.addWidget(self._end_date)
        dates.addStretch(1)
        layout.addLayout(dates)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("执行目标")))
        self._task_target_kind = self._build_target_kind_combo()
        controls.addWidget(self._task_target_kind)
        controls.addWidget(QLabel(tr("状态")))
        self._task_status = self._build_status_combo()
        controls.addWidget(self._task_status)
        controls.addStretch(1)
        self._query_button = QPushButton(tr("查询"))
        self._query_button.clicked.connect(self.refresh_tasks)
        controls.addWidget(self._query_button)
        layout.addLayout(controls)
        apply_button_style(self._query_button, variant="action")
        return widget

    @staticmethod
    def _build_target_kind_combo() -> QComboBox:
        combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        combo.addItem(tr("全部"), None)
        combo.addItem(tr("窗口"), "windows")
        combo.addItem(tr("设备"), "adb")
        combo.addItem(tr("未记录"), "")
        return combo

    @staticmethod
    def _build_status_combo() -> AutoWidthComboBox:
        combo = AutoWidthComboBox(width_mode=ComboWidthMode.FULL)
        combo.addItem(tr("全部"), None)
        for key, label in _STATUS_LABELS.items():
            combo.addItem(label, key)
        return combo

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        vertical_header = table.verticalHeader()
        horizontal_header = table.horizontalHeader()
        assert vertical_header is not None
        assert horizontal_header is not None
        vertical_header.setVisible(False)
        horizontal_header.setStretchLastSection(False)

    def _repo(self) -> TaskHistoryRepository:
        if self._repository is None:
            self._repository = TaskHistoryRepository()
        return self._repository

    def refresh_options(self) -> None:
        try:
            users, tasks = self._repo().filter_options()
            earliest, latest = self._repo().date_bounds()
        except Exception as exc:  # noqa: BLE001
            self._show_read_error(exc)
            return
        self._users.items.clear()
        for username in users:
            self._users.add_choice(username or tr("未记录用户"), username)
        self._users.clear_checks()
        self._tasks.items.clear()
        name_counts = Counter(name for _, name in tasks)
        for task_id, task_name in tasks:
            name = task_name or task_id
            text = f"{name} ({task_id})" if name_counts[task_name] > 1 else name
            self._tasks.add_choice(text, task_id, f"{name} ({task_id})")
        self._tasks.clear_checks()
        for start_widget in (self._start_date, self._batch_start_date):
            start_widget.setDate(_qdate(earliest))
        for end_widget in (self._end_date, self._batch_end_date):
            end_widget.setDate(_qdate(latest))
        self.refresh_tasks()
        self.refresh_batches()

    def refresh_tasks(self) -> None:
        start = self._start_date.date().toPyDate()
        end = self._end_date.date().toPyDate()
        if not self._valid_dates(start, end):
            return
        try:
            self._task_records = self._repo().list_task_runs(
                usernames=self._users.checked_keys(),
                task_ids=self._tasks.checked_keys(),
                status=self._task_status.currentData(),
                batch_run_id=self._batch_filter or None,
                target_kind=self._task_target_kind.currentData(),
                start_date=start, end_date=end,
            )
        except Exception as exc:  # noqa: BLE001
            self._show_read_error(exc)
            return
        self._task_table.setRowCount(len(self._task_records))
        for row, record in enumerate(self._task_records):
            values = (
                _format_time(record.started_at), record.username or tr("未记录用户"),
                record.task_name, _STATUS_LABELS.get(record.status, record.status),
                self._duration(record.duration_ms, bool(record.finished_at)),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._task_table.setItem(row, column, item)
        self._task_table.resizeColumnsToContents()
        header = self._task_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        if self._task_records:
            self._task_table.selectRow(0)
            self._show_selected_task()
        else:
            self._task_viewer.clear()

    def refresh_batches(self) -> None:
        start = self._batch_start_date.date().toPyDate()
        end = self._batch_end_date.date().toPyDate()
        if not self._valid_dates(start, end):
            return
        try:
            self._batch_records = self._repo().list_batch_runs(
                start_date=start, end_date=end,
                target_kind=self._batch_target_kind.currentData(),
                status=self._batch_status.currentData())
        except Exception as exc:  # noqa: BLE001
            self._show_read_error(exc)
            return
        self._batch_table.setRowCount(len(self._batch_records))
        for row, record in enumerate(self._batch_records):
            values = (
                _format_time(record.started_at), record.config_name,
                _STATUS_LABELS.get(record.status, record.status),
                self._duration(record.duration_ms, bool(record.finished_at)),
                str(record.task_count),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._batch_table.setItem(row, column, item)
        self._batch_table.resizeColumnsToContents()
        header = self._batch_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        if self._batch_records:
            self._batch_table.selectRow(0)
            self._show_selected_batch()
        else:
            self._batch_viewer.clear()
            self._view_batch_tasks_button.setEnabled(False)

    def _selected_task(self) -> TaskRunRecord | None:
        row = self._task_table.currentRow()
        return self._task_records[row] if 0 <= row < len(self._task_records) else None

    def _selected_batch(self) -> BatchRunRecord | None:
        row = self._batch_table.currentRow()
        return self._batch_records[row] if 0 <= row < len(self._batch_records) else None

    def _show_selected_task(self) -> None:
        record = self._selected_task()
        if record is None:
            self._task_viewer.clear()
            return
        self._task_viewer.set_record(
            log=resolve_history_path(record.log_path),
            content=resolve_history_path(record.result_path),
            details=self._detail_text(record, {
                "用户": record.username,
                "任务": record.task_name,
                "性质": _SCOPE_LABELS.get(record.task_scope, record.task_scope),
                "方式": _SOURCE_LABELS.get(record.source, record.source),
                "输入参数": record.params,
                "执行结果": str(resolve_history_path(record.result_path) or ""),
                "执行日志": str(resolve_history_path(record.log_path) or ""),
                "错误": record.error_message,
                "任务记录 ID": record.task_run_id,
                "批量记录 ID": record.batch_run_id,
            }))

    def _show_selected_batch(self) -> None:
        record = self._selected_batch()
        self._view_batch_tasks_button.setEnabled(record is not None)
        if record is None:
            self._batch_viewer.clear()
            return
        self._batch_viewer.set_record(
            content=resolve_history_path(record.report_path),
            details=self._detail_text(record, {
                "配置": record.config_name,
                "任务数": record.task_count,
                "批量输入快照": record.input_snapshot,
                "批量报告": str(resolve_history_path(record.report_path) or ""),
                "错误": record.error_message,
                "批量记录 ID": record.batch_run_id,
            }))

    @staticmethod
    def _detail_text(record: TaskRunRecord | BatchRunRecord, fields: dict) -> str:
        lines = [
            tr("状态") + f"：{_STATUS_LABELS.get(record.status, record.status)}",
            tr("耗时") + f"：{DailyHistoryDialog._duration(record.duration_ms, bool(record.finished_at))}",
            tr("执行目标") + f"：{record.target_label or record.target_id or '—'}",
            tr("开始时间") + f"：{_format_time(record.started_at)}",
            tr("结束时间") + f"：{_format_time(record.finished_at)}",
        ]
        for label, value in fields.items():
            if not value:
                continue
            if isinstance(value, (dict, list)):
                value = "\n" + json.dumps(value, ensure_ascii=False, indent=2, default=str)
            lines.append(f"{tr(label)}：{value}")
        return "\n\n".join(lines)

    def _view_batch_tasks(self) -> None:
        record = self._selected_batch()
        if record is None:
            return
        self._batch_filter = record.batch_run_id
        self._batch_filter_label.setText(
            tr("当前批次：") + record.config_name + " · " + _format_time(record.started_at))
        self._batch_filter_label.setVisible(True)
        self._clear_batch_filter_button.setVisible(True)
        self._users.clear_checks()
        self._tasks.clear_checks()
        self._users.search.clear()
        self._tasks.search.clear()
        self._task_status.setCurrentIndex(0)
        self._task_target_kind.setCurrentIndex(0)
        # 从批次本身及其子任务取日期，覆盖跨午夜和未收尾的批次。
        try:
            tasks = self._repo().list_task_runs(batch_run_id=record.batch_run_id)
        except Exception as exc:  # noqa: BLE001
            self._show_read_error(exc)
            return
        dates = [datetime.fromisoformat(value).date()
                 for value in (record.started_at, record.finished_at,
                               *(task.started_at for task in tasks)) if value]
        self._start_date.setDate(_qdate(min(dates)))
        self._end_date.setDate(_qdate(max(dates)))
        self.refresh_tasks()
        self._tabs.setCurrentIndex(0)

    def _clear_batch_filter(self) -> None:
        self._batch_filter = ""
        self._batch_filter_label.setVisible(False)
        self._clear_batch_filter_button.setVisible(False)
        self.refresh_tasks()

    def _open_task_result(self) -> None:
        self._task_viewer.show_content()

    def _valid_dates(self, start: date, end: date) -> bool:
        if start <= end:
            return True
        QMessageBox.information(
            self, tr("日期范围无效"), tr("开始日期不能晚于结束日期"))
        return False

    @staticmethod
    def _duration(duration_ms: int, finished: bool) -> str:
        return f"{duration_ms / 1000:.1f} s" if finished else "—"

    def _show_read_error(self, exc: Exception) -> None:
        logger.warning(f"任务历史读取失败: {exc}")
        QMessageBox.warning(self, tr("任务历史暂不可用"), str(exc))
