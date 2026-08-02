"""批量执行 Tab — 脚本勾选 + 触发 + 进度

挂载于主窗口左侧 Tab「批量」。
条目配置由工具菜单「批量配置」对话框管理，本 Tab 只负责触发与进度展示。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.batch_config import BatchConfig, BatchEntry, load_batch_config, save_batch_config
from .batch_runner import (
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchScript,
)

# 状态 → 表格背景色
_STATUS_COLORS = {
    ST_PENDING: QColor("#f5f5f5"),
    ST_RUNNING: QColor("#e3f2fd"),
    ST_SUCCESS: QColor("#e8f5e9"),
    ST_FAILED: QColor("#ffebee"),
    ST_SKIPPED: QColor("#fff8e1"),
}

_STYLE_BTN_RUN = (
    "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
)
_STYLE_BTN_STOP = (
    "background-color: #f44336; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
)
_STYLE_BTN_NOT_READY = (
    "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
)


class BatchTab(QWidget):
    """批量执行页面

    Args:
        host: MainWindow 实例（提供 run_batch / request_stop / 信号等）
    """

    def __init__(self, host):
        super().__init__()
        self._host = host
        self._running = False
        self._setup_ui()

        # 宿主状态信号
        host.automation_state_changed.connect(self._on_automation_state)

        self._refresh_script_list()
        self._refresh_entry_summary()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 执行按钮（第一行）──
        self._btn_run = QPushButton("开始批量执行 (F9)")
        self._btn_run.setStyleSheet(_STYLE_BTN_RUN)
        self._btn_run.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._btn_run)

        # ── 条目概览 ──
        self._lbl_summary = QLabel()
        self._lbl_summary.setStyleSheet("font-weight: bold; font-size: 12px; color: #333;")
        layout.addWidget(self._lbl_summary)

        # ── 脚本列表 ──
        layout.addWidget(self._section_label("执行脚本（勾选）"))

        self._script_list = QListWidget()
        self._script_list.setMaximumHeight(140)
        layout.addWidget(self._script_list)

        # ── 进度表 ──
        layout.addWidget(self._section_label("执行进度"))

        self._progress_table = QTableWidget(0, 3)
        self._progress_table.setHorizontalHeaderLabels(["条目", "脚本", "状态"])
        self._progress_table.horizontalHeader().setStretchLastSection(True)
        self._progress_table.setColumnWidth(0, 100)
        self._progress_table.setColumnWidth(1, 110)
        self._progress_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._progress_table.verticalHeader().setVisible(False)
        layout.addWidget(self._progress_table, stretch=1)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: bold; font-size: 12px; color: #333;")
        return label

    # ─── 条目概览 ─────────────────────────────────────────

    def _refresh_entry_summary(self):
        """刷新条目概览（从 batch_config 读取）"""
        cfg = load_batch_config()
        n = len(cfg.entries)
        if n == 0:
            self._lbl_summary.setText("暂无执行条目（工具 → 批量配置）")
        else:
            accounts = sorted(set(e.account for e in cfg.entries))
            self._lbl_summary.setText(
                f"已配置 {n} 个条目（{len(accounts)} 个账号）— 工具 → 批量配置"
            )

    # ─── 脚本列表 ─────────────────────────────────────────

    def _refresh_script_list(self, checked_ids: set[str] | None = None):
        """刷新脚本勾选列表（数据源与日常下拉一致）"""
        from ..workflows.discovery import list_exposed_scripts

        if checked_ids is None:
            cfg = load_batch_config()
            checked_ids = set(cfg.script_ids)

        self._script_list.blockSignals(True)
        self._script_list.clear()
        try:
            configs = list_exposed_scripts()
        except Exception:
            configs = []
        for cfg in configs:
            item = QListWidgetItem(cfg["name"])
            item.setData(Qt.ItemDataRole.UserRole, cfg)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = cfg["id"] in checked_ids
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self._script_list.addItem(item)
        self._script_list.blockSignals(False)

    def _checked_script_ids(self) -> list[str]:
        """获取勾选的脚本 ID 列表"""
        ids: list[str] = []
        for i in range(self._script_list.count()):
            item = self._script_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                cfg = item.data(Qt.ItemDataRole.UserRole)
                if cfg:
                    ids.append(cfg["id"])
        return ids

    def _checked_scripts(self) -> list[BatchScript]:
        """获取勾选的脚本 BatchScript 列表"""
        scripts: list[BatchScript] = []
        for i in range(self._script_list.count()):
            item = self._script_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            cfg = item.data(Qt.ItemDataRole.UserRole)
            scripts.append(BatchScript(
                id=cfg["id"],
                name=cfg["name"],
                wf_file=cfg.get("wf_file", ""),
                class_name=cfg.get("class", ""),
            ))
        return scripts

    # ─── 执行控制 ─────────────────────────────────────────

    def f9_run(self):
        """F9 快捷键入口：运行中 → 停止；否则启动批量"""
        if self._host.is_running:
            self._host.request_stop()
            return
        self._start_batch()

    def _on_run_clicked(self):
        if self._running:
            self._host.request_stop()
            return
        self._start_batch()

    def _start_batch(self):
        cfg = load_batch_config()
        entries = cfg.entries
        scripts = self._checked_scripts()

        if not entries:
            self._host.append_log("[批量] 暂无执行条目，请先通过 工具 → 批量配置 添加")
            return
        if not scripts:
            self._host.append_log("[批量] 请至少勾选一个脚本")
            return

        # 保存脚本勾选到 batch_config
        cfg.script_ids = self._checked_script_ids()
        save_batch_config(cfg)

        # 构建进度表
        self._build_progress_table(entries, scripts)
        self._set_config_enabled(False)

        ok = self._host.run_batch(entries, scripts)
        if not ok:
            self._set_config_enabled(True)

    def _build_progress_table(self, entries: list[BatchEntry],
                              scripts: list[BatchScript]):
        """初始化进度表：条目×脚本 全量行"""
        self._progress_table.setRowCount(0)
        for entry in entries:
            label = f"{entry.account}/{entry.role}"
            for script in scripts:
                row = self._progress_table.rowCount()
                self._progress_table.insertRow(row)
                self._progress_table.setItem(
                    row, 0, QTableWidgetItem(label))
                self._progress_table.setItem(
                    row, 1, QTableWidgetItem(script.name))
                status_item = QTableWidgetItem(ST_PENDING)
                status_item.setBackground(_STATUS_COLORS[ST_PENDING])
                self._progress_table.setItem(row, 2, status_item)

    def update_progress(self, entry_label: str, script_id: str, status: str):
        """更新进度表中匹配行的状态（由 host 调用）"""
        script_name = script_id
        for i in range(self._script_list.count()):
            cfg = self._script_list.item(i).data(Qt.ItemDataRole.UserRole)
            if cfg and cfg["id"] == script_id:
                script_name = cfg["name"]
                break

        for row in range(self._progress_table.rowCount()):
            u_item = self._progress_table.item(row, 0)
            s_item = self._progress_table.item(row, 1)
            if u_item and u_item.text() == entry_label and \
               s_item and s_item.text() == script_name:
                status_item = QTableWidgetItem(status)
                color = _STATUS_COLORS.get(status)
                if color:
                    status_item.setBackground(color)
                self._progress_table.setItem(row, 2, status_item)
                self._progress_table.scrollToItem(status_item)
                break

    def on_batch_finished(self, summary: dict):
        """批量全部结束（由 host 调用）"""
        self._running = False
        self._set_config_enabled(True)
        self._refresh_run_button("ready")

    def refresh_config(self):
        """外部配置变更后调用，刷新条目概览 + 脚本勾选"""
        self._refresh_entry_summary()
        self._refresh_script_list()

    # ─── 状态联动 ─────────────────────────────────────────

    def _on_automation_state(self, state: str):
        """宿主自动化状态变化 → 刷新按钮"""
        self._running = (state == "running")
        self._refresh_run_button(state)
        if state == "running":
            self._set_config_enabled(False)
        else:
            self._set_config_enabled(True)

    def _refresh_run_button(self, state: str):
        if state == "running":
            self._btn_run.setText("停止 (F10)")
            self._btn_run.setStyleSheet(_STYLE_BTN_STOP)
        elif state == "not_ready":
            self._btn_run.setText("未连接")
            self._btn_run.setStyleSheet(_STYLE_BTN_NOT_READY)
        else:
            self._btn_run.setText("开始批量执行 (F9)")
            self._btn_run.setStyleSheet(_STYLE_BTN_RUN)

    def _set_config_enabled(self, enabled: bool):
        """运行期间锁定配置区域"""
        self._script_list.setEnabled(enabled)
