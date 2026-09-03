"""统一任务历史与批量历史查询窗口。"""
from __future__ import annotations

import json
from datetime import date

from loguru import logger
from PyQt6.QtCore import QDate, QSize, Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.daily_history import (
    BatchRunRecord,
    TaskHistoryRepository,
    TaskRunRecord,
    resolve_history_path,
)
from ..i18n import tr
from .button_styles import apply_button_style

_STATUS_LABELS = {
    "running": tr("进行中"), "completed": tr("已完成"),
    "interrupted": tr("已中断"), "failed": tr("失败"),
}
_SOURCE_LABELS = {"single": tr("单独运行"), "batch": tr("批量运行")}
_SCOPE_LABELS = {"daily": tr("日常"), "dedicated": tr("专用")}


class _MultiColumnListWidget(QListWidget):
    """按固定列数横向排列的筛选列表。"""

    def __init__(self, column_count: int, parent=None):
        super().__init__(parent)
        self.column_count = column_count
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        available_width = (self.viewport().width()
                           - self.verticalScrollBar().sizeHint().width()
                           - self.column_count)
        width = max(1, available_width // self.column_count)
        row_height = max(self.fontMetrics().height() + 8,
                         self.sizeHintForRow(0))
        self.setGridSize(QSize(width, row_height))


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
        self.resize(1220, 780)
        self._repository = repository
        self._task_records: list[TaskRunRecord] = []
        self._batch_records: list[BatchRunRecord] = []
        self._batch_filter = ""
        self._build_ui()
        self.refresh_options()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_task_page(), tr("任务历史"))
        self._tabs.addTab(self._build_batch_page(), tr("批量历史"))
        layout.addWidget(self._tabs)

    def _build_task_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        filters = QGroupBox(tr("筛选条件（用户和任务可多选；不选择表示全部）"))
        grid = QGridLayout(filters)
        grid.addWidget(QLabel(tr("用户")), 0, 0)
        self._users = _MultiColumnListWidget(3)
        self._users.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection)
        self._users.setMaximumHeight(90)
        grid.addWidget(self._users, 1, 0)
        grid.addWidget(QLabel(tr("任务")), 0, 1)
        self._tasks = _MultiColumnListWidget(2)
        self._tasks.setSelectionMode(
            QAbstractItemView.SelectionMode.MultiSelection)
        self._tasks.setMaximumHeight(90)
        grid.addWidget(self._tasks, 1, 1)
        dates = self._build_date_controls()
        grid.addWidget(dates, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(filters)

        batch_filter_row = QHBoxLayout()
        self._batch_filter_label = QLabel()
        self._batch_filter_label.setVisible(False)
        batch_filter_row.addWidget(self._batch_filter_label, 1)
        self._clear_batch_filter_button = QPushButton(tr("返回全部任务"))
        self._clear_batch_filter_button.clicked.connect(self._clear_batch_filter)
        self._clear_batch_filter_button.setVisible(False)
        batch_filter_row.addWidget(self._clear_batch_filter_button)
        layout.addLayout(batch_filter_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self._task_table = QTableWidget(0, 10)
        self._task_table.setHorizontalHeaderLabels([
            tr("开始时间"), tr("结束时间"), tr("耗时"), tr("用户"),
            tr("任务"), tr("性质"), tr("方式"), tr("状态"),
            tr("任务记录 ID"), tr("批量记录 ID"),
        ])
        self._configure_table(self._task_table)
        self._task_table.itemSelectionChanged.connect(self._show_selected_task)
        self._task_table.cellDoubleClicked.connect(
            lambda _row, _column: self._open_task_result())
        splitter.addWidget(self._task_table)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        header = QHBoxLayout()
        self._task_summary = QLabel(tr("选择一条记录查看输入参数和文件路径"))
        self._task_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(self._task_summary, 1)
        self._open_result_button = QPushButton(tr("打开结果 JSON"))
        self._open_result_button.clicked.connect(self._open_task_result)
        header.addWidget(self._open_result_button)
        self._open_log_button = QPushButton(tr("打开执行日志"))
        self._open_log_button.clicked.connect(self._open_task_log)
        header.addWidget(self._open_log_button)
        detail_layout.addLayout(header)
        self._task_detail = QTextEdit()
        self._task_detail.setReadOnly(True)
        detail_layout.addWidget(self._task_detail)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        apply_button_style(
            self._clear_batch_filter_button,
            self._open_result_button,
            self._open_log_button,
            variant="neutral",
        )
        self._set_task_buttons(None)
        return page

    def _build_batch_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("开始日期")))
        self._batch_start_date = QDateEdit()
        self._batch_start_date.setCalendarPopup(True)
        self._batch_start_date.setDisplayFormat("yyyy-MM-dd")
        controls.addWidget(self._batch_start_date)
        controls.addWidget(QLabel(tr("结束日期")))
        self._batch_end_date = QDateEdit()
        self._batch_end_date.setCalendarPopup(True)
        self._batch_end_date.setDisplayFormat("yyyy-MM-dd")
        controls.addWidget(self._batch_end_date)
        self._batch_query_button = QPushButton(tr("查询"))
        self._batch_query_button.clicked.connect(self.refresh_batches)
        controls.addWidget(self._batch_query_button)
        controls.addStretch(1)
        layout.addLayout(controls)

        self._batch_table = QTableWidget(0, 8)
        self._batch_table.setHorizontalHeaderLabels([
            tr("开始时间"), tr("结束时间"), tr("耗时"), tr("配置"),
            tr("状态"), tr("任务数"), tr("批量记录 ID"), tr("批量报告"),
        ])
        self._configure_table(self._batch_table)
        self._batch_table.itemSelectionChanged.connect(self._show_selected_batch)
        self._batch_table.cellDoubleClicked.connect(
            lambda _row, _column: self._view_batch_tasks())
        layout.addWidget(self._batch_table, 3)

        header = QHBoxLayout()
        self._batch_summary = QLabel(tr("选择一条批量记录查看输入快照"))
        self._batch_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(self._batch_summary, 1)
        self._view_batch_tasks_button = QPushButton(tr("查看全部单任务"))
        self._view_batch_tasks_button.clicked.connect(self._view_batch_tasks)
        header.addWidget(self._view_batch_tasks_button)
        self._open_batch_report_button = QPushButton(tr("打开批量报告"))
        self._open_batch_report_button.clicked.connect(self._open_batch_report)
        header.addWidget(self._open_batch_report_button)
        layout.addLayout(header)
        self._batch_detail = QTextEdit()
        self._batch_detail.setReadOnly(True)
        layout.addWidget(self._batch_detail, 2)
        self._set_batch_buttons(None)
        apply_button_style(
            self._batch_query_button, self._view_batch_tasks_button,
            variant="action")
        apply_button_style(self._open_batch_report_button, variant="neutral")
        return page

    def _build_date_controls(self) -> QWidget:
        widget = QWidget()
        layout = QGridLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(tr("开始日期")), 0, 0)
        self._start_date = QDateEdit()
        self._start_date.setCalendarPopup(True)
        self._start_date.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(self._start_date, 0, 1)
        layout.addWidget(QLabel(tr("结束日期")), 1, 0)
        self._end_date = QDateEdit()
        self._end_date.setCalendarPopup(True)
        self._end_date.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(self._end_date, 1, 1)
        self._query_button = QPushButton(tr("查询"))
        self._query_button.clicked.connect(self.refresh_tasks)
        layout.addWidget(self._query_button, 2, 0, 1, 2)
        apply_button_style(self._query_button, variant="action")
        return widget

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
        horizontal_header.setStretchLastSection(True)

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
        self._users.clear()
        self._users.addItems(users)
        self._tasks.clear()
        for task_id, task_name in tasks:
            item = QListWidgetItem(f"{task_name} ({task_id})")
            item.setData(Qt.ItemDataRole.UserRole, task_id)
            self._tasks.addItem(item)
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
                usernames=[item.text() for item in self._users.selectedItems()],
                task_ids=[str(item.data(Qt.ItemDataRole.UserRole))
                          for item in self._tasks.selectedItems()],
                batch_run_id=self._batch_filter or None,
                start_date=start, end_date=end,
            )
        except Exception as exc:  # noqa: BLE001
            self._show_read_error(exc)
            return
        self._task_table.setRowCount(len(self._task_records))
        for row, record in enumerate(self._task_records):
            values = (
                _format_time(record.started_at), _format_time(record.finished_at),
                self._duration(record.duration_ms, bool(record.finished_at)),
                record.username, record.task_name,
                _SCOPE_LABELS.get(record.task_scope, record.task_scope),
                _SOURCE_LABELS.get(record.source, record.source),
                _STATUS_LABELS.get(record.status, record.status),
                record.task_run_id[:12], record.batch_run_id[:12],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    record.task_run_id if column == 8 else
                    record.batch_run_id if column == 9 else "")
                self._task_table.setItem(row, column, item)
        self._task_table.resizeColumnsToContents()
        if self._task_records:
            self._task_table.selectRow(0)
        else:
            self._task_summary.setText(tr("没有符合条件的任务历史"))
            self._task_detail.clear()
            self._set_task_buttons(None)

    def refresh_batches(self) -> None:
        start = self._batch_start_date.date().toPyDate()
        end = self._batch_end_date.date().toPyDate()
        if not self._valid_dates(start, end):
            return
        try:
            self._batch_records = self._repo().list_batch_runs(
                start_date=start, end_date=end)
        except Exception as exc:  # noqa: BLE001
            self._show_read_error(exc)
            return
        self._batch_table.setRowCount(len(self._batch_records))
        for row, record in enumerate(self._batch_records):
            values = (
                _format_time(record.started_at), _format_time(record.finished_at),
                self._duration(record.duration_ms, bool(record.finished_at)),
                record.config_name, _STATUS_LABELS.get(record.status, record.status),
                str(record.task_count), record.batch_run_id[:12],
                tr("有") if record.report_path else tr("无"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 6:
                    item.setToolTip(record.batch_run_id)
                self._batch_table.setItem(row, column, item)
        self._batch_table.resizeColumnsToContents()
        if self._batch_records:
            self._batch_table.selectRow(0)
        else:
            self._batch_summary.setText(tr("没有符合条件的批量历史"))
            self._batch_detail.clear()
            self._set_batch_buttons(None)

    def _selected_task(self) -> TaskRunRecord | None:
        row = self._task_table.currentRow()
        return self._task_records[row] if 0 <= row < len(self._task_records) else None

    def _selected_batch(self) -> BatchRunRecord | None:
        row = self._batch_table.currentRow()
        return self._batch_records[row] if 0 <= row < len(self._batch_records) else None

    def _show_selected_task(self) -> None:
        record = self._selected_task()
        self._set_task_buttons(record)
        if record is None:
            return
        self._task_summary.setText(
            f"task_run_id: {record.task_run_id}    "
            f"batch_run_id: {record.batch_run_id or '—'}")
        self._task_detail.setPlainText(json.dumps({
            "输入参数": record.params,
            "结果 JSON": str(resolve_history_path(record.result_path) or ""),
            "执行日志": str(resolve_history_path(record.log_path) or ""),
            "错误": record.error_message,
        }, ensure_ascii=False, indent=2, default=str))

    def _show_selected_batch(self) -> None:
        record = self._selected_batch()
        self._set_batch_buttons(record)
        if record is None:
            return
        self._batch_summary.setText(f"batch_run_id: {record.batch_run_id}")
        self._batch_detail.setPlainText(json.dumps({
            "批量输入快照": record.input_snapshot,
            "批量报告": str(resolve_history_path(record.report_path) or ""),
            "错误": record.error_message,
        }, ensure_ascii=False, indent=2, default=str))

    def _view_batch_tasks(self) -> None:
        record = self._selected_batch()
        if record is None:
            return
        self._batch_filter = record.batch_run_id
        self._batch_filter_label.setText(
            tr("当前仅显示批量记录：") + record.batch_run_id)
        self._batch_filter_label.setVisible(True)
        self._clear_batch_filter_button.setVisible(True)
        self._users.clearSelection()
        self._tasks.clearSelection()
        self.refresh_tasks()
        self._tabs.setCurrentIndex(0)

    def _clear_batch_filter(self) -> None:
        self._batch_filter = ""
        self._batch_filter_label.setVisible(False)
        self._clear_batch_filter_button.setVisible(False)
        self.refresh_tasks()

    def _set_task_buttons(self, record: TaskRunRecord | None) -> None:
        result = resolve_history_path(record.result_path) if record else None
        log = resolve_history_path(record.log_path) if record else None
        self._open_result_button.setEnabled(bool(result and result.is_file()))
        self._open_log_button.setEnabled(bool(log and log.is_file()))

    def _set_batch_buttons(self, record: BatchRunRecord | None) -> None:
        report = resolve_history_path(record.report_path) if record else None
        self._view_batch_tasks_button.setEnabled(record is not None)
        self._open_batch_report_button.setEnabled(bool(report and report.is_file()))

    def _open_task_result(self) -> None:
        record = self._selected_task()
        self._open_path(record.result_path if record else "")

    def _open_task_log(self) -> None:
        record = self._selected_task()
        self._open_path(record.log_path if record else "")

    def _open_batch_report(self) -> None:
        record = self._selected_batch()
        self._open_path(record.report_path if record else "")

    def _open_path(self, stored_path: str) -> None:
        path = resolve_history_path(stored_path)
        if path is None or not path.is_file():
            QMessageBox.information(
                self, tr("文件不存在"), tr("对应文件不存在或已被移动"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))

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
