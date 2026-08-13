"""批量执行 Tab — 进度 / 脚本 / 配置 三页子 Tab

挂载于主窗口左侧 Tab「批量」。
仿照调律 Tab 结构：顶部开始/停止按钮 + 三页子 Tab。
- 进度：执行进度表
- 脚本：勾选要执行的脚本
- 配置：选择配置 + 勾选要执行的行
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from ...core.batch_config import (
    BatchConfigItem,
    load_batch_config,
    save_batch_config,
)
from ...core.config.session import get_session_store
from ...i18n import tr
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


# ─── enabled 用户态（session.json）──────────────────────────


def _load_enabled_rows() -> dict[str, list[bool]]:
    """从 session.json 读取各配置的 enabled 状态"""
    batch = get_session_store().get_node("batch", {})
    if not isinstance(batch, dict):
        return {}
    return batch.get("enabled_rows", {})


def _save_enabled_rows(enabled_rows: dict[str, list[bool]]) -> None:
    """保存 enabled 状态到 session.json 的 batch.enabled_rows（不影响其他节点）"""
    get_session_store().mutate_node(
        "batch",
        lambda old: {**(old if isinstance(old, dict) else {}),
                     "enabled_rows": enabled_rows},
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
        self._refresh_config_combo()
        self._refresh_entry_list()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 执行按钮（第一行）──
        self._btn_run = QPushButton(tr("开始批量执行 (F9)"))
        self._btn_run.setStyleSheet(_STYLE_BTN_RUN)
        self._btn_run.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._btn_run)

        # ── 三页子 Tab ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_progress_page(), tr("进度"))
        self._sub_tabs.addTab(self._build_script_page(), tr("脚本"))
        self._sub_tabs.addTab(self._build_config_page(), tr("配置"))
        layout.addWidget(self._sub_tabs)

    def _build_progress_page(self) -> QWidget:
        """进度页：执行进度表"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        self._progress_table = QTableWidget(0, 3)
        self._progress_table.setHorizontalHeaderLabels([tr("条目"), tr("脚本"), tr("状态")])
        hheader = self._progress_table.horizontalHeader()
        assert hheader is not None
        hheader.setStretchLastSection(True)
        self._progress_table.setColumnWidth(0, 100)
        self._progress_table.setColumnWidth(1, 110)
        self._progress_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        vheader = self._progress_table.verticalHeader()
        assert vheader is not None
        vheader.setVisible(False)
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

        layout.addWidget(QLabel(tr("<b>勾选要执行的脚本：</b>")))
        self._script_list = QListWidget()
        layout.addWidget(self._script_list)
        layout.addStretch()

        scroll.setWidget(widget)
        return scroll

    def _build_config_page(self) -> QWidget:
        """配置页：选择配置 + 勾选要执行的行"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        # 配置选择行
        config_row = QHBoxLayout()
        config_row.addWidget(QLabel(tr("当前配置：")))
        self._config_combo = QComboBox()
        self._config_combo.setMinimumWidth(150)
        self._config_combo.currentIndexChanged.connect(self._on_config_changed)
        config_row.addWidget(self._config_combo, stretch=1)
        layout.addLayout(config_row)

        # 全选/全不选行
        select_row = QHBoxLayout()
        select_row.addWidget(QLabel(tr("<b>选择要执行的行：</b>")))
        select_row.addStretch()
        btn_all = QPushButton(tr("全选"))
        btn_all.setFixedWidth(60)
        btn_all.clicked.connect(lambda: self._set_all_entries_checked(True))
        select_row.addWidget(btn_all)
        btn_none = QPushButton(tr("全不选"))
        btn_none.setFixedWidth(60)
        btn_none.clicked.connect(lambda: self._set_all_entries_checked(False))
        select_row.addWidget(btn_none)
        layout.addLayout(select_row)

        # 行勾选列表（放在 scroll 中）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        self._entry_checkboxes: list[tuple[QCheckBox, int]] = []  # (checkbox, row_index)
        self._entry_container = QVBoxLayout()
        scroll_layout.addLayout(self._entry_container)
        scroll_layout.addStretch()

        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll, stretch=1)

        return widget

    # ─── 配置选择 ─────────────────────────────────────────

    def _refresh_config_combo(self):
        """刷新配置下拉框"""
        cfg = load_batch_config()
        self._config_combo.blockSignals(True)
        self._config_combo.clear()
        for name in cfg.configs:
            self._config_combo.addItem(name)
        # 选中 active_config
        if cfg.active_config and cfg.active_config in cfg.configs:
            idx = self._config_combo.findText(cfg.active_config)
            if idx >= 0:
                self._config_combo.setCurrentIndex(idx)
        self._config_combo.blockSignals(False)

    def _on_config_changed(self, index: int):
        """配置下拉框切换 → 刷新行列表 + 保存 active_config"""
        if index < 0:
            return
        name = self._config_combo.itemText(index)
        cfg = load_batch_config()
        cfg.active_config = name
        save_batch_config(cfg)
        self._refresh_entry_list()

    def _current_config_name(self) -> str:
        """获取当前选中的配置名"""
        idx = self._config_combo.currentIndex()
        if idx < 0:
            return ""
        return self._config_combo.itemText(idx)

    # ─── 行列表 ──────────────────────────────────────────

    def _refresh_entry_list(self):
        """刷新配置页的行勾选列表"""
        # 清空旧控件
        while self._entry_container.count():
            item = self._entry_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._entry_checkboxes.clear()

        cfg = load_batch_config()
        config = cfg.get_active()
        if not config or not config.rows:
            lbl = QLabel(tr("暂无数据，请通过 工具 → 批量配置 添加"))
            lbl.setStyleSheet("color: #999;")
            self._entry_container.addWidget(lbl)
            return

        # 加载 enabled 状态
        enabled_rows = _load_enabled_rows()
        config_enabled = enabled_rows.get(config.name, [True] * len(config.rows))

        for i, row_data in enumerate(config.rows):
            label = self._format_row_label(config, row_data)
            cb = QCheckBox(label)
            checked = config_enabled[i] if i < len(config_enabled) else True
            cb.setChecked(checked)
            cb.stateChanged.connect(self._on_entry_check_changed)
            self._entry_container.addWidget(cb)
            self._entry_checkboxes.append((cb, i))

    def _format_row_label(self, config: BatchConfigItem, row_data: dict) -> str:
        """格式化行显示标签"""
        parts = []
        for col in config.columns:
            val = row_data.get(col, "")
            if val:
                parts.append(str(val))
        return " / ".join(parts) if parts else tr("(空行)")

    def _on_entry_check_changed(self):
        """行勾选变更 → 保存到 session.json（用户态）"""
        config_name = self._current_config_name()
        if not config_name:
            return

        enabled_rows = _load_enabled_rows()
        config = load_batch_config().get_active()
        if not config:
            return

        enabled_list = [False] * len(config.rows)
        for cb, idx in self._entry_checkboxes:
            if 0 <= idx < len(enabled_list):
                enabled_list[idx] = cb.isChecked()
        enabled_rows[config_name] = enabled_list
        _save_enabled_rows(enabled_rows)

    def _set_all_entries_checked(self, checked: bool):
        """全选/全不选行"""
        for cb, _ in self._entry_checkboxes:
            cb.setChecked(checked)
        self._on_entry_check_changed()

    def _get_enabled_rows(self) -> list[tuple[int, dict]]:
        """获取已启用的行列表：[(index, row_data), ...]"""
        cfg = load_batch_config()
        config = cfg.get_active()
        if not config:
            return []

        enabled_rows = _load_enabled_rows()
        config_enabled = enabled_rows.get(config.name, [True] * len(config.rows))

        result = []
        for i, row_data in enumerate(config.rows):
            if i < len(config_enabled) and config_enabled[i]:
                result.append((i, row_data))
        return result

    # ─── 脚本列表 ─────────────────────────────────────────

    def _refresh_script_list(self, checked_ids: set[str] | None = None):
        """刷新脚本勾选列表（数据源与日常下拉一致）"""
        from ...workflows.discovery import list_exposed_scripts

        if checked_ids is None:
            cfg = load_batch_config()
            checked_ids = set(cfg.script_ids)

        self._script_list.blockSignals(True)
        self._script_list.clear()
        try:
            configs = list_exposed_scripts()
        except Exception:
            configs = []
        for script_cfg in configs:
            item = QListWidgetItem(script_cfg["name"])
            item.setData(Qt.ItemDataRole.UserRole, script_cfg)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = script_cfg["id"] in checked_ids
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
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                cfg = item.data(Qt.ItemDataRole.UserRole)
                if cfg:
                    ids.append(cfg["id"])
        return ids

    def _checked_scripts(self) -> list[BatchScript]:
        """获取勾选的脚本 BatchScript 列表

        ⚠️ 不读取参数：脚本参数由批量执行引擎在执行时从 wf_configs 加载。
        """
        scripts: list[BatchScript] = []
        for i in range(self._script_list.count()):
            item = self._script_list.item(i)
            if item is None or item.checkState() != Qt.CheckState.Checked:
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
        enabled_rows = self._get_enabled_rows()
        scripts = self._checked_scripts()

        if not enabled_rows:
            self._host.append_log(tr("[批量] 暂无启用的行，请到「配置」页勾选"))
            return
        if not scripts:
            self._host.append_log(tr("[批量] 请至少勾选一个脚本"))
            return

        # 保存脚本勾选到 batch_config
        cfg = load_batch_config()
        cfg.script_ids = self._checked_script_ids()
        save_batch_config(cfg)

        # 构建进度表
        config = cfg.get_active()
        self._build_progress_table(enabled_rows, config, scripts)
        self._set_config_enabled(False)

        ok = self._host.run_batch(enabled_rows, scripts)
        if not ok:
            self._set_config_enabled(True)

    def _build_progress_table(self, enabled_rows: list[tuple[int, dict]],
                              config: BatchConfigItem | None,
                              scripts: list[BatchScript]):
        """初始化进度表：行×脚本 全量行"""
        self._progress_table.setRowCount(0)
        for _idx, row_data in enabled_rows:
            label = self._format_row_label(config, row_data) if config else str(row_data)
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
            it = self._script_list.item(i)
            if it is None:
                continue
            cfg = it.data(Qt.ItemDataRole.UserRole)
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
        """外部配置变更后调用，刷新配置 + 脚本 + 行列表"""
        self._refresh_config_combo()
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
            self._btn_run.setText(tr("停止 (F10)"))
            self._btn_run.setStyleSheet(_STYLE_BTN_STOP)
        elif state == "not_ready":
            self._btn_run.setText(tr("未连接"))
            self._btn_run.setStyleSheet(_STYLE_BTN_NOT_READY)
        else:
            self._btn_run.setText(tr("开始批量执行 (F9)"))
            self._btn_run.setStyleSheet(_STYLE_BTN_RUN)

    def _set_config_enabled(self, enabled: bool):
        """运行期间锁定脚本页和配置页"""
        self._script_list.setEnabled(enabled)
        self._config_combo.setEnabled(enabled)
        for cb, _ in self._entry_checkboxes:
            cb.setEnabled(enabled)
