"""调律历史运行列表。"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .....ui.button_styles import apply_button_style
from ...config.tune_slots import SLOT_LABELS
from ...tuning_history.repository import TuningHistoryRepository
from .result_dialog import TuningResultsDialog
from .result_store import TuningResultStore
from .styles import HEADER_TITLE_STYLE

_ANOMALY_COLUMN = 5

_STATUS_LABELS = {
    "running": tr("进行中"),
    "completed": tr("已完成"),
    "interrupted": tr("已中断"),
    "material_exhausted": tr("材料耗尽"),
    "failed": tr("失败"),
}


class TuningHistoryWidget(QWidget):
    """显示总历史和每次运行摘要，双击后复用装备总览。"""

    def __init__(self, repository: TuningHistoryRepository | None = None,
                 parent=None):
        super().__init__(parent)
        # 默认仓库延迟到 refresh 创建；即使历史库损坏，调律页本身仍可打开。
        self._repository = repository
        self._uses_default_repository = repository is None
        self._detail_dialog: TuningResultsDialog | None = None
        self._detail_store: TuningResultStore | None = None
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self._title_label = QLabel(tr("历史调律记录"))
        self._title_label.setStyleSheet(HEADER_TITLE_STYLE)
        header.addWidget(self._title_label)
        header.addStretch(1)
        self._refresh_button = QPushButton(tr("刷新记录"))
        self._refresh_button.clicked.connect(self.refresh)
        header.addWidget(self._refresh_button)
        self._delete_button = QPushButton(tr("删除记录"))
        self._delete_button.setEnabled(False)
        self._delete_button.clicked.connect(self._delete_selected)
        header.addWidget(self._delete_button)
        self._open_button = QPushButton(tr("查看详情"))
        self._open_button.setEnabled(False)
        self._open_button.clicked.connect(self._open_selected)
        header.addWidget(self._open_button)
        apply_button_style(self._refresh_button, variant="neutral")
        apply_button_style(self._delete_button, variant="danger")
        apply_button_style(self._open_button, variant="action")
        layout.addLayout(header)

        self._stats = QLabel()
        self._stats.setWordWrap(True)
        self._stats.setStyleSheet(
            "background: palette(alternate-base); border-radius: 7px;"
            "padding: 8px 10px; color: palette(mid);")
        layout.addWidget(self._stats)

        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            tr("开始时间"), tr("状态"), tr("用户"), tr("部位"),
            tr("处理结果"), tr("异常"), tr("总轮次"), tr("规则"),
        ])
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._selection_changed)
        self._table.cellDoubleClicked.connect(
            lambda _row, _column: self._open_selected())
        self._content = QStackedWidget()
        self._content.addWidget(self._table)
        self._empty = QLabel(tr("暂无调律历史记录"))
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            "color: palette(mid); padding: 48px 24px; font-size: 14px;")
        self._content.addWidget(self._empty)
        layout.addWidget(self._content, 1)

    def refresh(self) -> None:
        selected_run_id = self._selected_run_id()
        try:
            if self._repository is None:
                self._repository = TuningHistoryRepository()
            counts = self._repository.aggregate_counts()
            runs = self._repository.list_runs()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史读取失败: {exc}")
            if self._uses_default_repository:
                self._repository = None
            self._table.setRowCount(0)
            self._empty.setText(tr("历史记录暂不可用"))
            self._content.setCurrentWidget(self._empty)
            self._stats.setVisible(False)
            self._stats.setText(tr("历史记录暂不可用"))
            self._open_button.setEnabled(False)
            self._delete_button.setEnabled(False)
            return
        self._empty.setText(tr("暂无调律历史记录"))
        self._stats.setText(tr(
            "历史 {runs} 次 · 处理 {total} 件 · 调律 {tuned} · 回收 {recycled} · 跳过 {skipped} · 重置 {reset} · 异常 {anomaly}"
        ).format(**counts))
        self._table.setRowCount(len(runs))
        selected_row = 0
        for row, run in enumerate(runs):
            if run.run_id == selected_run_id:
                selected_row = row
            started = run.started_at.replace("T", " ")[:19]
            slots = "、".join(SLOT_LABELS.get(key, key)
                             for key in run.selected_slots) or tr("全部")
            result = tr("处理 {total} · 调律 {tuned} · 回收 {recycled} · 跳过 {skipped} · 重置 {reset}").format(
                total=run.total_equipment, tuned=run.tuned_count,
                recycled=run.recycled_count, skipped=run.skipped_count,
                reset=run.reset_count)
            rules = "、".join(str(item.get("key") or "")
                             for item in run.rule_snapshot if item.get("key"))
            values = (
                started, _STATUS_LABELS.get(run.status, run.status),
                run.username, slots, result,
                str(run.anomaly_count) if run.anomaly_count else "",
                str(run.total_rounds), rules or tr("默认规则"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, run.run_id)
                # 异常列非零时标红：整表扫一眼就能定位到需要复核 OCR 的运行，
                # 不必逐条打开详情去找那张卡片。
                if column == _ANOMALY_COLUMN and run.anomaly_count:
                    item.setForeground(QColor("#D32F2F"))
                    item.setToolTip(tr("本次运行有 {n} 件装备识别异常").format(
                        n=run.anomaly_count))
                self._table.setItem(row, column, item)
        self._table.resizeColumnsToContents()
        self._stats.setVisible(bool(runs))
        self._content.setCurrentWidget(self._table if runs else self._empty)
        if runs:
            self._table.setCurrentCell(selected_row, 0)
            self._table.selectRow(selected_row)
        self._open_button.setEnabled(bool(runs))
        self._delete_button.setEnabled(bool(runs))

    def _selection_changed(self) -> None:
        selected = bool(self._table.selectedItems())
        self._open_button.setEnabled(selected)
        self._delete_button.setEnabled(selected)

    def _selected_run_id(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _open_selected(self) -> None:
        run_id = self._selected_run_id()
        if not run_id or self._repository is None:
            return
        try:
            run = self._repository.get_run(run_id)
            results = self._repository.get_results(run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史详情读取失败: {exc}")
            self.refresh()
            return
        self._detail_store = TuningResultStore(parent=self)
        self._detail_store.replace_results(results)
        if self._detail_dialog is not None:
            self._detail_dialog.close()
        self._detail_dialog = TuningResultsDialog(
            self._detail_store, self.window())
        if run is not None:
            timestamp = run.started_at.replace("T", " ")[:19]
            self._detail_dialog.setWindowTitle(
                tr("调律记录详情") + " · " + timestamp)
        self._detail_dialog.show()
        self._detail_dialog.raise_()
        self._detail_dialog.activateWindow()

    def _delete_selected(self) -> None:
        run_id = self._selected_run_id()
        if not run_id or self._repository is None:
            return
        try:
            run = self._repository.get_run(run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史读取失败: {exc}")
            QMessageBox.warning(
                self, tr("删除调律记录失败"), str(exc))
            return
        timestamp = (run.started_at.replace("T", " ")[:19]
                     if run is not None else run_id[:8])
        answer = QMessageBox.question(
            self,
            tr("删除调律记录"),
            tr("确定删除 {time} 的调律任务及其全部装备详情吗？未上报的统计记录也会删除；此操作无法撤销。").format(
                time=timestamp),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_run(run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史删除失败: {exc}")
            QMessageBox.warning(
                self, tr("删除调律记录失败"), str(exc))
            return
        if self._detail_dialog is not None:
            self._detail_dialog.close()
            self._detail_dialog = None
            self._detail_store = None
        self.refresh()
