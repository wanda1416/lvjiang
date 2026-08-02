"""批量执行 Tab — 进度 / 脚本 / 配置 三页子 Tab

挂载于主窗口左侧 Tab「批量」。
仿照调律 Tab 结构：顶部开始/停止按钮 + 三页子 Tab。
- 进度：执行进度表
- 脚本：勾选要执行的脚本
- 配置：勾选要执行的条目（用户/角色）
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.batch_config import (
    BatchEntry,
    load_batch_config,
    save_batch_config,
)
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
        self._refresh_entry_list()

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

        # ── 三页子 Tab ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_progress_page(), "进度")
        self._sub_tabs.addTab(self._build_script_page(), "脚本")
        self._sub_tabs.addTab(self._build_config_page(), "配置")
        layout.addWidget(self._sub_tabs)

    def _build_progress_page(self) -> QWidget:
        """进度页：执行进度表"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

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
        return widget

    def _build_script_page(self) -> QWidget:
        """脚本页：勾选要执行的脚本"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("<b>勾选要执行的脚本：</b>"))
        self._script_list = QListWidget()
        layout.addWidget(self._script_list)
        layout.addStretch()

        scroll.setWidget(widget)
        return scroll

    def _build_config_page(self) -> QWidget:
        """配置页：勾选要执行的条目（用户/角色）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        # 标题行
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>选择要执行的条目：</b>"))
        header.addStretch()
        btn_all = QPushButton("全选")
        btn_all.setFixedWidth(60)
        btn_all.clicked.connect(lambda: self._set_all_entries_checked(True))
        header.addWidget(btn_all)
        btn_none = QPushButton("全不选")
        btn_none.setFixedWidth(60)
        btn_none.clicked.connect(lambda: self._set_all_entries_checked(False))
        header.addWidget(btn_none)
        layout.addLayout(header)

        # 条目勾选列表
        self._entry_checkboxes: list[tuple[QCheckBox, int]] = []  # (checkbox, entry_index)
        self._entry_container = QVBoxLayout()
        layout.addLayout(self._entry_container)
        layout.addStretch()

        scroll.setWidget(widget)
        return scroll

    # ─── 条目列表 ─────────────────────────────────────────

    def _refresh_entry_list(self):
        """刷新配置页的条目勾选列表"""
        # 清空旧控件
        while self._entry_container.count():
            item = self._entry_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._entry_checkboxes.clear()

        cfg = load_batch_config()
        if not cfg.entries:
            lbl = QLabel("暂无条目，请通过 工具 → 批量配置 添加")
            lbl.setStyleSheet("color: #999;")
            self._entry_container.addWidget(lbl)
            return

        for i, entry in enumerate(cfg.entries):
            cb = QCheckBox(f"{entry.account} / {entry.role}（角色{entry.role_index}）")
            cb.setChecked(entry.enabled)
            cb.stateChanged.connect(self._on_entry_check_changed)
            self._entry_container.addWidget(cb)
            self._entry_checkboxes.append((cb, i))

    def _on_entry_check_changed(self):
        """条目勾选变更 → 保存到 batch_config"""
        cfg = load_batch_config()
        for cb, idx in self._entry_checkboxes:
            if 0 <= idx < len(cfg.entries):
                cfg.entries[idx].enabled = cb.isChecked()
        save_batch_config(cfg)

    def _set_all_entries_checked(self, checked: bool):
        """全选/全不选条目"""
        for cb, _ in self._entry_checkboxes:
            cb.setChecked(checked)
        self._on_entry_check_changed()

    def _get_enabled_entries(self) -> list[BatchEntry]:
        """获取已启用的条目列表"""
        cfg = load_batch_config()
        return [e for e in cfg.entries if e.enabled]

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
        entries = self._get_enabled_entries()
        scripts = self._checked_scripts()

        if not entries:
            self._host.append_log("[批量] 暂无启用的条目，请到「配置」页勾选")
            return
        if not scripts:
            self._host.append_log("[批量] 请至少勾选一个脚本")
            return

        # 保存脚本勾选到 batch_config
        cfg = load_batch_config()
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
        """外部配置变更后调用，刷新脚本 + 条目列表"""
        self._refresh_script_list()
        self._refresh_entry_list()

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
        """运行期间锁定脚本页和配置页"""
        self._script_list.setEnabled(enabled)
        for cb, _ in self._entry_checkboxes:
            cb.setEnabled(enabled)
