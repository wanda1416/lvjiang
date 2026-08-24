"""批量执行 Tab — 进度 / 脚本 / 配置 三页子 Tab

挂载于主窗口左侧 Tab「批量」。
仿照调律 Tab 结构：顶部开始/停止按钮 + 三页子 Tab。
- 进度：执行进度表
- 脚本：勾选要执行的脚本
- 配置：选择配置 + 勾选要执行的行
"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
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
from ..theme import get_theme_manager
from .batch_runner import (
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchScript,
)


def _status_color(status: str):
    """返回随主题变化的批量任务状态背景色。"""
    from PyQt6.QtGui import QColor
    tokens = get_theme_manager().tokens
    colours = {
        ST_PENDING: tokens.surface_alt,
        ST_RUNNING: tokens.info_surface,
        ST_SUCCESS: tokens.success_surface,
        ST_FAILED: tokens.danger_surface,
        ST_SKIPPED: tokens.warning_surface,
    }
    value = colours.get(status)
    return QColor(value) if value else None

_STYLE_BTN_RUN = (
    "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
)
_STYLE_BTN_STOP = (
    "background-color: #f44336; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
)
_STYLE_BTN_NOT_READY = (
    "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px; font-size: 13px;"
)
_STYLE_BTN_PAUSE = (
    "background-color: #FF9800; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
)
_STYLE_BTN_RESUME = (
    "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
)
_STYLE_BTN_DISABLED = (
    "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
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
        get_theme_manager().theme_changed.connect(self._refresh_status_colors)

        self._refresh_script_list()
        self._refresh_config_combo()
        self._refresh_entry_list()

    # ─── UI 构建 ─────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # ── 执行按钮 + 暂停/恢复按钮（第一行）──
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        self._btn_run = QPushButton(f"{tr('开始执行')} ({self._host._user_config.hotkeys.start})")
        self._btn_run.setStyleSheet(_STYLE_BTN_RUN)
        self._btn_run.clicked.connect(self._on_run_clicked)
        btn_layout.addWidget(self._btn_run)

        self._btn_pause_resume = QPushButton(tr("暂停"))
        self._btn_pause_resume.setEnabled(False)
        self._btn_pause_resume.setStyleSheet(_STYLE_BTN_DISABLED)
        self._btn_pause_resume.clicked.connect(self._on_pause_resume_clicked)
        btn_layout.addWidget(self._btn_pause_resume)
        layout.addLayout(btn_layout)

        # ── 三页子 Tab ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_script_page(), tr("脚本"))
        self._sub_tabs.addTab(self._build_config_page(), tr("配置"))
        self._sub_tabs.addTab(self._build_progress_page(), tr("进度"))
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
        # 前两列自适应内容，最后一列拉伸填充剩余空间
        hheader.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hheader.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hheader.setStretchLastSection(True)
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
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel(tr("<b>勾选要执行的脚本：</b>")))
        title_row.addStretch()
        self._btn_script_up = QPushButton(tr("↑ 上移"))
        self._btn_script_up.setFixedWidth(72)
        self._btn_script_up.clicked.connect(lambda: self._move_selected_script(-1))
        title_row.addWidget(self._btn_script_up)
        self._btn_script_down = QPushButton(tr("↓ 下移"))
        self._btn_script_down.setFixedWidth(72)
        self._btn_script_down.clicked.connect(lambda: self._move_selected_script(1))
        title_row.addWidget(self._btn_script_down)
        layout.addLayout(title_row)

        self._script_list = QTreeWidget()
        self._script_list.setColumnCount(2)
        self._script_list.setHeaderLabels([tr("脚本候选"), tr("执行顺序")])
        self._script_list.setRootIsDecorated(False)
        self._script_list.setUniformRowHeights(True)
        self._script_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._script_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self._script_list.header()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._script_list.setColumnWidth(1, 80)
        self._script_list.itemChanged.connect(self._on_script_item_changed)
        self._script_list.currentItemChanged.connect(
            lambda *_: self._update_script_move_buttons()
        )
        layout.addWidget(self._script_list, stretch=1)

        self._script_order: list[str] = []
        self._script_configs_by_id: dict[str, dict] = {}
        self._updating_script_list = False
        self._update_script_move_buttons()
        return widget

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
            lbl.setStyleSheet("color: palette(mid);")
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

    def _refresh_script_list(self, checked_ids: list[str] | None = None):
        """刷新脚本勾选列表（数据源与日常下拉一致）"""
        from ...workflows.discovery import list_exposed_scripts

        if checked_ids is None:
            cfg = load_batch_config()
            checked_ids = list(cfg.script_ids)

        self._updating_script_list = True
        self._script_list.clear()
        try:
            configs = [
                cfg for cfg in list_exposed_scripts()
                if cfg.get("batchable", True)
            ]
        except Exception:
            configs = []
        self._script_configs_by_id = {cfg["id"]: cfg for cfg in configs}
        self._script_order = []
        for script_id in checked_ids:
            if script_id in self._script_configs_by_id \
                    and script_id not in self._script_order:
                self._script_order.append(script_id)

        # 与“配置”页的 QCheckBox 行保持相同的控件高度和垂直间距。
        row_height = QCheckBox().sizeHint().height() + max(
            0, self._entry_container.spacing()
        )
        for script_cfg in configs:
            item = QTreeWidgetItem([script_cfg["name"], ""])
            item.setData(0, Qt.ItemDataRole.UserRole, script_cfg)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = script_cfg["id"] in checked_ids
            item.setCheckState(
                0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            item.setSizeHint(0, QSize(0, row_height))
            item.setSizeHint(1, QSize(0, row_height))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            self._script_list.addTopLevelItem(item)
        self._updating_script_list = False
        self._refresh_script_order_column()
        self._update_script_move_buttons()

    def _script_id(self, item: QTreeWidgetItem | None) -> str:
        """返回脚本行绑定的 ID。"""
        if item is None:
            return ""
        cfg = item.data(0, Qt.ItemDataRole.UserRole)
        return cfg.get("id", "") if isinstance(cfg, dict) else ""

    def _on_script_item_changed(self, item: QTreeWidgetItem, column: int):
        """勾选变化后追加/移除执行顺序，并立即连续编号。"""
        if self._updating_script_list or column != 0:
            return
        script_id = self._script_id(item)
        if not script_id:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            if script_id not in self._script_order:
                self._script_order.append(script_id)
        elif script_id in self._script_order:
            self._script_order.remove(script_id)
        self._refresh_script_order_column()
        self._persist_script_order()
        self._update_script_move_buttons()

    def _refresh_script_order_column(self):
        """第二列仅为已勾选脚本显示连续的 1..N。"""
        order_by_id = {
            script_id: str(index)
            for index, script_id in enumerate(self._script_order, start=1)
        }
        self._updating_script_list = True
        try:
            for index in range(self._script_list.topLevelItemCount()):
                item = self._script_list.topLevelItem(index)
                item.setText(1, order_by_id.get(self._script_id(item), ""))
        finally:
            self._updating_script_list = False

    def _persist_script_order(self):
        """立即保存勾选项及其执行顺序。"""
        cfg = load_batch_config()
        cfg.script_ids = list(self._script_order)
        save_batch_config(cfg)

    def _move_selected_script(self, delta: int):
        """调整当前已勾选脚本的执行顺序。"""
        script_id = self._script_id(self._script_list.currentItem())
        if script_id not in self._script_order:
            return
        old_index = self._script_order.index(script_id)
        new_index = old_index + delta
        if new_index < 0 or new_index >= len(self._script_order):
            return
        self._script_order[old_index], self._script_order[new_index] = (
            self._script_order[new_index], self._script_order[old_index]
        )
        self._refresh_script_order_column()
        self._persist_script_order()
        self._update_script_move_buttons()

    def _update_script_move_buttons(self):
        """仅在所选脚本可移动时启用顺序按钮。"""
        script_id = self._script_id(self._script_list.currentItem())
        try:
            index = self._script_order.index(script_id)
        except ValueError:
            index = -1
        editable = self._script_list.isEnabled()
        self._btn_script_up.setEnabled(editable and index > 0)
        self._btn_script_down.setEnabled(
            editable and 0 <= index < len(self._script_order) - 1
        )

    def _checked_script_ids(self) -> list[str]:
        """获取勾选的脚本 ID 列表"""
        return list(self._script_order)

    def _checked_scripts(self) -> list[BatchScript]:
        """获取勾选的脚本 BatchScript 列表

        ⚠️ 不读取参数：脚本参数由批量执行引擎在执行时从 wf_configs 加载。
        """
        scripts: list[BatchScript] = []
        for script_id in self._script_order:
            cfg = self._script_configs_by_id.get(script_id)
            if cfg is None:
                continue
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
            # 条目只显示 user_column 对应的值
            if config and config.user_column:
                label = row_data.get(config.user_column, "")
            else:
                label = self._format_row_label(config, row_data) if config else str(row_data)
            for script in scripts:
                row = self._progress_table.rowCount()
                self._progress_table.insertRow(row)
                self._progress_table.setItem(
                    row, 0, QTableWidgetItem(label))
                self._progress_table.setItem(
                    row, 1, QTableWidgetItem(script.name))
                status_item = QTableWidgetItem(ST_PENDING)
                status_item.setBackground(_status_color(ST_PENDING))
                self._progress_table.setItem(row, 2, status_item)

    def update_progress(self, entry_label: str, script_id: str, status: str):
        """更新进度表中匹配行的状态（由 host 调用）"""
        script_name = script_id
        cfg = self._script_configs_by_id.get(script_id)
        if cfg:
            script_name = cfg["name"]

        for row in range(self._progress_table.rowCount()):
            u_item = self._progress_table.item(row, 0)
            s_item = self._progress_table.item(row, 1)
            if u_item and u_item.text() == entry_label and \
               s_item and s_item.text() == script_name:
                status_item = QTableWidgetItem(status)
                color = _status_color(status)
                if color:
                    status_item.setBackground(color)
                self._progress_table.setItem(row, 2, status_item)
                self._progress_table.scrollToItem(status_item)
                break

    def _refresh_status_colors(self, _theme: str) -> None:
        """实时切换主题后重绘现有进度行。"""
        for row in range(self._progress_table.rowCount()):
            item = self._progress_table.item(row, 2)
            if item is None:
                continue
            color = _status_color(item.text())
            if color is not None:
                item.setBackground(color)

    def on_batch_finished(self, summary: dict):
        """批量全部结束（由 host 调用）"""
        self._running = False
        self._set_config_enabled(True)
        self._refresh_run_button("idle")

    def refresh_config(self):
        """外部配置变更后调用，刷新配置 + 脚本 + 行列表"""
        self._refresh_config_combo()
        self._refresh_script_list()
        self._refresh_entry_list()

    # ─── 状态联动 ─────────────────────────────────────────

    def _on_automation_state(self, state: str):
        """宿主自动化状态变化 → 刷新按钮"""
        self._running = state in ("running", "paused")
        self._refresh_run_button(state)
        if state in ("running", "paused"):
            self._set_config_enabled(False)
        else:
            self._set_config_enabled(True)

    def _on_pause_resume_clicked(self):
        """暂停/恢复按钮点击 → 转发给宿主"""
        self._host.request_pause_resume()

    def _refresh_run_button(self, state: str):
        hk = self._host._user_config.hotkeys
        if state in ("running", "paused"):
            self._btn_run.setText(f"{tr('结束')} ({hk.stop})")
            self._btn_run.setStyleSheet(_STYLE_BTN_STOP)
        elif state == "not_ready":
            self._btn_run.setText(tr("未连接"))
            self._btn_run.setStyleSheet(_STYLE_BTN_NOT_READY)
        else:
            self._btn_run.setText(f"{tr('开始执行')} ({hk.start})")
            self._btn_run.setStyleSheet(_STYLE_BTN_RUN)
        # 刷新暂停/恢复按钮
        if state == "running":
            self._btn_pause_resume.setText(f"{tr('暂停')} ({hk.pause})")
            self._btn_pause_resume.setEnabled(True)
            self._btn_pause_resume.setStyleSheet(_STYLE_BTN_PAUSE)
        elif state == "paused":
            self._btn_pause_resume.setText(f"{tr('恢复')} ({hk.pause})")
            self._btn_pause_resume.setEnabled(True)
            self._btn_pause_resume.setStyleSheet(_STYLE_BTN_RESUME)
        else:
            self._btn_pause_resume.setText(tr("暂停"))
            self._btn_pause_resume.setEnabled(False)
            self._btn_pause_resume.setStyleSheet(_STYLE_BTN_DISABLED)

    def _set_config_enabled(self, enabled: bool):
        """运行期间锁定脚本页和配置页"""
        self._script_list.setEnabled(enabled)
        self._update_script_move_buttons()
        self._config_combo.setEnabled(enabled)
        for cb, _ in self._entry_checkboxes:
            cb.setEnabled(enabled)
