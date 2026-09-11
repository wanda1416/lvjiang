"""批量执行 Tab — 脚本 / 用户 / 进度 / 参数四页子 Tab

挂载于主窗口左侧 Tab「批量」。
仿照调律 Tab 结构：顶部开始/停止按钮 + 四页子 Tab。
- 进度：执行进度表
- 脚本：勾选要执行的脚本
- 用户：勾选当前配置组实际执行的用户
"""

from __future__ import annotations

import json
from typing import Any, cast

from loguru import logger
from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
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
    lifecycle_parameter_definitions,
    load_batch_config,
    save_batch_config,
)
from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width
from ..main.run_control import STATE_PLAN_UNSUPPORTED
from ..theme import get_theme_manager
from .batch_runner import (
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchScript,
    PlannedTask,
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
_STYLE_BTN_PLAN_UNSUPPORTED = (
    "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
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

_BATCH_LIST_VERTICAL_PADDING = 12
_TABLE_COLUMN_HORIZONTAL_PADDING = 20
_TABLE_VIEWPORT_SAFETY_MARGIN = 4


def _batch_list_row_height(widget: QWidget) -> int:
    """Use the same font-based row height in the script and config pages."""
    return widget.fontMetrics().height() + _BATCH_LIST_VERTICAL_PADDING


def _four_cjk_column_width(widget: QWidget) -> int:
    """Default compact width that still fits four CJK characters."""
    return (
        widget.fontMetrics().horizontalAdvance("汉字宽度")
        + _TABLE_COLUMN_HORIZONTAL_PADDING
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
        self._progress_column_resize_guard = False
        self._progress_row_index: dict[tuple[int, str], int] = {}
        self._progress_row_context: dict[int, tuple[int, str]] = {}
        self._progress_task_plan: dict[tuple[int, str], PlannedTask] = {}
        self._params_popup: QFrame | None = None
        self._setup_ui()

        # 宿主状态信号
        host.automation_state_changed.connect(self._on_automation_state)
        get_theme_manager().theme_changed.connect(self._refresh_status_colors)

        self._refresh_config_combo()
        self._refresh_group_contents()
        QTimer.singleShot(0, self._set_initial_column_widths)

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

        config_row = QHBoxLayout()
        config_row.addWidget(QLabel(tr("当前配置组：")))
        self._config_combo = QComboBox()
        self._config_combo.setMinimumWidth(150)
        self._config_combo.currentIndexChanged.connect(self._on_config_changed)
        config_row.addWidget(self._config_combo, stretch=1)
        layout.addLayout(config_row)

        # ── 四页子 Tab ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_script_page(), tr("脚本"))
        self._sub_tabs.addTab(self._build_config_page(), tr("用户"))
        self._sub_tabs.addTab(self._build_progress_page(), tr("进度"))
        self._sub_tabs.addTab(self._build_params_page(), tr("参数"))
        layout.addWidget(self._sub_tabs)

    def _build_params_page(self) -> QWidget:
        """参数页：保存当前配置组的执行参数。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        summary_group = QGroupBox(tr("批量设置"))
        summary_form = QFormLayout(summary_group)
        self._rounds_spin = QSpinBox()
        self._rounds_spin.setRange(1, 999)
        self._rounds_spin.setValue(1)
        self._rounds_spin.valueChanged.connect(self._persist_rounds)
        summary_form.addRow(tr("执行轮数："), self._rounds_spin)
        self._workflow_labels: dict[str, QLabel] = {}
        for key, label in (
            ("batch_setup", tr("批次准备") + "："),
            ("prepare_item", tr("条目准备") + "："),
            ("finish_item", tr("条目收尾") + "："),
            ("batch_teardown", tr("批次收尾") + "："),
        ):
            value = QLabel()
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self._workflow_labels[key] = value
            summary_form.addRow(label, value)
        layout.addWidget(summary_group)

        self._workflow_params_panel = QWidget()
        self._workflow_params_layout = QVBoxLayout(self._workflow_params_panel)
        self._workflow_params_layout.setContentsMargins(0, 0, 0, 0)
        self._workflow_param_groups: list[QGroupBox] = []
        self._workflow_param_widgets: dict[tuple[str, str], QWidget] = {}
        self._workflow_param_types: dict[tuple[str, str], str] = {}
        layout.addWidget(self._workflow_params_panel)
        layout.addStretch()
        return widget

    def _build_progress_page(self) -> QWidget:
        """进度页：执行进度表"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        self._progress_table = QTableWidget(0, 3)
        self._progress_table.setHorizontalHeaderLabels([tr("条目"), tr("脚本"), tr("状态")])
        self._progress_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._progress_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        cast(QWidget, self._progress_table.viewport()).installEventFilter(self)
        hheader = cast(QHeaderView, self._progress_table.horizontalHeader())
        # 默认保留四字宽；视口极窄时允许继续压缩，绝不产生横向滚动条。
        hheader.setMinimumSectionSize(1)
        hheader.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hheader.setStretchLastSection(False)
        hheader.sectionResized.connect(self._constrain_progress_column_widths)
        self._progress_table.setColumnWidth(
            2, _four_cjk_column_width(self._progress_table)
        )
        self._progress_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._progress_table.cellClicked.connect(self._on_progress_cell_clicked)
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

        actions = QHBoxLayout()
        script_label = QLabel(tr("<b>选择执行脚本：</b>"))
        script_label.setToolTip(tr("执行顺序由批量配置中的任务顺序决定"))
        actions.addWidget(script_label)
        actions.addStretch()
        self._btn_script_all = QPushButton(tr("全选"))
        self._btn_script_none = QPushButton(tr("全不选"))
        self._btn_script_all.clicked.connect(
            lambda: self._set_all_scripts_checked(True))
        self._btn_script_none.clicked.connect(
            lambda: self._set_all_scripts_checked(False))
        apply_button_style(
            self._btn_script_all, self._btn_script_none,
            variant="neutral",
        )
        for button in (self._btn_script_all, self._btn_script_none):
            actions.addWidget(button)
        layout.addLayout(actions)

        self._script_list = QTreeWidget()
        self._script_list.setColumnCount(2)
        self._script_list.setHeaderLabels([tr("脚本候选"), tr("执行顺序")])
        self._script_list.setRootIsDecorated(False)
        self._script_list.setUniformRowHeights(True)
        self._script_list.setIndentation(0)
        self._script_list.setStyleSheet(
            "QTreeWidget::item { padding-left: 6px; padding-right: 8px; }"
        )
        self._script_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._script_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._script_list.setToolTip(
            tr("执行顺序由批量配置中的任务顺序决定"))
        header = self._script_list.header()
        assert header is not None
        header.setMinimumHeight(32)
        header.setMinimumSectionSize(48)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._script_list.setColumnWidth(
            1, _four_cjk_column_width(self._script_list)
        )
        self._script_list.itemChanged.connect(self._on_script_item_changed)
        layout.addWidget(self._script_list, stretch=1)

        self._script_order: list[str] = []
        self._script_candidate_order: list[str] = []
        self._missing_script_ids: list[tuple[int, str]] = []
        self._script_configs_by_id: dict[str, dict] = {}
        self._updating_script_list = False
        return widget

    def _set_initial_column_widths(self) -> None:
        """Give content columns the free space while keeping metadata compact."""
        order_width = _four_cjk_column_width(self._script_list)
        script_viewport = cast(QWidget, self._script_list.viewport())
        script_header = cast(QHeaderView, self._script_list.header())
        script_available = max(
            script_viewport.width() - _TABLE_VIEWPORT_SAFETY_MARGIN,
            0,
        )
        script_name_width = max(
            script_header.minimumSectionSize(),
            script_available - order_width,
        )
        self._script_list.setColumnWidth(0, script_name_width)
        self._script_list.setColumnWidth(1, order_width)

        self._set_progress_column_widths()

    def _set_progress_column_widths(self) -> None:
        """Fit default widths inside the viewport: compact / flexible / compact."""
        progress_viewport = cast(QWidget, self._progress_table.viewport())
        progress_header = cast(
            QHeaderView, self._progress_table.horizontalHeader()
        )
        available = max(
            progress_viewport.width() - _TABLE_VIEWPORT_SAFETY_MARGIN,
            0,
        )
        minimum = progress_header.minimumSectionSize()
        compact_width = min(
            _four_cjk_column_width(self._progress_table),
            max(minimum, (available - minimum) // 2),
        )
        script_width = max(minimum, available - compact_width * 2)
        self._progress_column_resize_guard = True
        try:
            self._progress_table.setColumnWidth(0, compact_width)
            self._progress_table.setColumnWidth(1, script_width)
            self._progress_table.setColumnWidth(2, compact_width)
        finally:
            self._progress_column_resize_guard = False
        self._constrain_progress_column_widths()

    def _constrain_progress_column_widths(self, *_args) -> None:
        """Shrink overflowing columns so the progress table never scrolls sideways."""
        if self._progress_column_resize_guard:
            return
        progress_viewport = cast(QWidget, self._progress_table.viewport())
        progress_header = cast(
            QHeaderView, self._progress_table.horizontalHeader()
        )
        available = max(
            progress_viewport.width() - _TABLE_VIEWPORT_SAFETY_MARGIN,
            0,
        )
        widths = [self._progress_table.columnWidth(column) for column in range(3)]
        overflow = sum(widths) - available
        if overflow <= 0:
            return

        minimum = progress_header.minimumSectionSize()
        resized_column = (
            _args[0]
            if _args and isinstance(_args[0], int) and 0 <= _args[0] < 3
            else None
        )
        shrink_order = [
            column for column in (1, 0, 2) if column != resized_column
        ]
        if resized_column is not None:
            shrink_order.append(resized_column)
        self._progress_column_resize_guard = True
        try:
            # 用户拖动时优先压缩其他列；视口缩小时优先压缩脚本列。
            for column in shrink_order:
                reducible = max(0, widths[column] - minimum)
                reduction = min(overflow, reducible)
                if reduction:
                    widths[column] -= reduction
                    self._progress_table.setColumnWidth(column, widths[column])
                    overflow -= reduction
                if overflow <= 0:
                    break
        finally:
            self._progress_column_resize_guard = False

    def eventFilter(self, watched, event):
        if (
            hasattr(self, "_progress_table")
            and watched is self._progress_table.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            QTimer.singleShot(0, self._constrain_progress_column_widths)
        return super().eventFilter(watched, event)

    def _build_config_page(self) -> QWidget:
        """用户页：勾选当前配置组实际执行的用户。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)

        # 全选/全不选行
        select_row = QHBoxLayout()
        user_label = QLabel(tr("<b>选择执行用户：</b>"))
        user_label.setToolTip(tr("执行顺序由批量配置中的用户顺序决定"))
        select_row.addWidget(user_label)
        select_row.addStretch()
        self._btn_user_all = QPushButton(tr("全选"))
        self._btn_user_all.setFixedWidth(60)
        self._btn_user_all.clicked.connect(
            lambda: self._set_all_entries_checked(True))
        select_row.addWidget(self._btn_user_all)
        self._btn_user_none = QPushButton(tr("全不选"))
        self._btn_user_none.setFixedWidth(60)
        self._btn_user_none.clicked.connect(
            lambda: self._set_all_entries_checked(False))
        select_row.addWidget(self._btn_user_none)
        apply_button_style(
            self._btn_user_all, self._btn_user_none, variant="neutral")
        fit_button_width(
            self._btn_user_all, self._btn_user_none, minimum=60)
        layout.addLayout(select_row)

        # 行勾选列表（放在 scroll 中）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)

        self._entry_checkboxes: list[tuple[QCheckBox, str]] = []
        self._entry_container = QVBoxLayout()
        self._entry_container.setSpacing(0)
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
        self._refresh_group_contents()

    def _refresh_group_contents(self) -> None:
        self._refresh_script_list()
        self._refresh_entry_list()
        self._refresh_params()

    def _refresh_params(self) -> None:
        cfg = load_batch_config()
        item = cfg.configs.get(self._current_config_name()) or cfg.get_active()
        self._rounds_spin.blockSignals(True)
        self._rounds_spin.setValue(item.rounds if item is not None else 1)
        self._rounds_spin.blockSignals(False)
        for key, label in self._workflow_labels.items():
            path = getattr(item.workflows, key) if item is not None else ""
            label.setText(path or tr("未配置"))
        self._rebuild_workflow_params(item)

    def _rebuild_workflow_params(self, item: BatchConfigItem | None) -> None:
        while self._workflow_params_layout.count():
            layout_item = self._workflow_params_layout.takeAt(0)
            old_widget = layout_item.widget() if layout_item is not None else None
            if old_widget is not None:
                old_widget.deleteLater()
        self._workflow_param_groups.clear()
        self._workflow_param_widgets.clear()
        self._workflow_param_types.clear()
        if item is None:
            self._workflow_params_panel.setVisible(False)
            return

        phase_labels = {
            "batch_setup": tr("批次准备"),
            "prepare_item": tr("条目准备"),
            "finish_item": tr("条目收尾"),
            "batch_teardown": tr("批次收尾"),
        }
        definitions = lifecycle_parameter_definitions(item.workflows)
        for phase, params in definitions.items():
            if not params:
                continue
            group = QGroupBox(phase_labels[phase])
            form = QFormLayout(group)
            self._workflow_param_groups.append(group)
            saved = item.workflow_params.get(phase, {})
            for definition in params:
                name = str(definition["name"])
                label = str(definition.get("label") or name)
                value = saved.get(name, definition.get("default"))
                param_type = definition.get("type", "select")
                widget: QWidget
                if param_type == "bool":
                    checkbox = QCheckBox()
                    checkbox.setChecked(
                        value.lower() in ("true", "1", "yes", "on")
                        if isinstance(value, str) else bool(value))
                    checkbox.toggled.connect(self._persist_workflow_params)
                    widget = checkbox
                elif param_type == "number":
                    spin = QSpinBox()
                    spin.setRange(
                        int(definition.get("min", 0)),
                        int(definition.get("max", 999999)),
                    )
                    spin.setValue(int(value) if value is not None else 0)
                    spin.valueChanged.connect(self._persist_workflow_params)
                    widget = spin
                elif param_type == "select":
                    combo = QComboBox()
                    for option in definition.get("options", []):
                        if isinstance(option, dict):
                            combo.addItem(str(option.get("label", option["value"])),
                                          option["value"])
                        else:
                            combo.addItem(str(option), str(option))
                    selected = combo.findData(value)
                    if selected >= 0:
                        combo.setCurrentIndex(selected)
                    combo.currentIndexChanged.connect(self._persist_workflow_params)
                    widget = combo
                elif param_type == "checkgroup":
                    container = QWidget()
                    options_layout = QHBoxLayout(container)
                    options_layout.setContentsMargins(0, 0, 0, 0)
                    selected_values = value if isinstance(value, dict) else {}
                    for option in definition.get("options", []):
                        if isinstance(option, dict):
                            option_name = str(option["value"])
                            option_label = str(option.get("label", option_name))
                        else:
                            option_name = option_label = str(option)
                        checkbox = QCheckBox(option_label)
                        checkbox.setObjectName(option_name)
                        checkbox.setChecked(bool(selected_values.get(option_name, True)))
                        checkbox.toggled.connect(self._persist_workflow_params)
                        options_layout.addWidget(checkbox)
                    options_layout.addStretch()
                    widget = container
                else:
                    edit = QPlainTextEdit() if definition.get("multiline") else QLineEdit()
                    if isinstance(edit, QPlainTextEdit):
                        edit.setMaximumHeight(100)
                        edit.setPlainText(str(value or ""))
                        edit.textChanged.connect(self._persist_workflow_params)
                    else:
                        edit.setText(str(value or ""))
                        edit.textChanged.connect(self._persist_workflow_params)
                    widget = edit
                widget.setObjectName(name)
                self._workflow_param_widgets[(phase, name)] = widget
                self._workflow_param_types[(phase, name)] = str(param_type)
                form.addRow(f"{label}：", widget)
            self._workflow_params_layout.addWidget(group)
        self._workflow_params_panel.setVisible(bool(self._workflow_param_groups))

    def _persist_workflow_params(self, *_args) -> None:
        values: dict[str, dict] = {}
        for key, widget in self._workflow_param_widgets.items():
            phase, name = key
            param_type = self._workflow_param_types[key]
            value: Any
            if isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                value = widget.value()
            elif isinstance(widget, QComboBox):
                value = widget.currentData()
            elif isinstance(widget, QPlainTextEdit):
                value = widget.toPlainText()
            elif param_type == "checkgroup":
                value = {
                    checkbox.objectName(): checkbox.isChecked()
                    for checkbox in widget.findChildren(QCheckBox)
                }
            else:
                value = cast(QLineEdit, widget).text()
            values.setdefault(phase, {})[name] = value
        cfg = load_batch_config()
        item = cfg.configs.get(self._current_config_name())
        if item is None:
            return
        item.workflow_params = values
        save_batch_config(cfg)

    def _persist_rounds(self, rounds: int) -> None:
        cfg = load_batch_config()
        item = cfg.configs.get(self._current_config_name())
        if item is None:
            return
        item.rounds = rounds
        save_batch_config(cfg)

    def _current_config_name(self) -> str:
        """获取当前选中的配置名"""
        idx = self._config_combo.currentIndex()
        if idx < 0:
            return ""
        return self._config_combo.itemText(idx)

    # ─── 行列表 ──────────────────────────────────────────

    def _refresh_entry_list(self):
        """刷新用户页的勾选列表。"""
        # 清空旧控件
        while self._entry_container.count():
            item = self._entry_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._entry_checkboxes.clear()

        cfg = load_batch_config()
        config = cfg.get_active()
        if not config or not config.usernames:
            lbl = QLabel(tr("暂无数据，请通过 工具 → 批量配置 添加"))
            lbl.setStyleSheet("color: palette(mid);")
            self._entry_container.addWidget(lbl)
            return

        selected = set(config.selected_usernames)
        for username in config.usernames:
            cb = QCheckBox(username)
            cb.setFixedHeight(_batch_list_row_height(cb))
            cb.setToolTip(tr("执行顺序由批量配置中的用户顺序决定"))
            cb.setChecked(username in selected)
            cb.toggled.connect(self._persist_user_selection)
            self._entry_container.addWidget(cb)
            self._entry_checkboxes.append((cb, username))

    def _set_all_entries_checked(self, checked: bool):
        """全选/全不选行"""
        for cb, _username in self._entry_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._persist_user_selection()

    def _persist_user_selection(self, *_args) -> None:
        name = self._current_config_name()
        selected = [
            username for checkbox, username in self._entry_checkboxes
            if checkbox.isChecked()
        ]
        cfg = load_batch_config()
        item = cfg.configs.get(name)
        if item is None:
            return
        item.selected_usernames = selected
        save_batch_config(cfg)

    def _get_enabled_usernames(self) -> list[str]:
        """按配置顺序返回本次勾选的用户名。"""
        cfg = load_batch_config()
        config = cfg.configs.get(self._current_config_name())
        if not config:
            return []
        return [username for checkbox, username in self._entry_checkboxes
                if checkbox.isChecked()]

    # ─── 脚本列表 ─────────────────────────────────────────

    def _refresh_script_list(self, checked_ids: list[str] | None = None):
        """刷新脚本勾选列表（数据源与日常下拉一致）"""
        from ...workflows.discovery import list_exposed_scripts

        if checked_ids is None:
            cfg = load_batch_config()
            group = cfg.configs.get(self._current_config_name()) or cfg.get_active()
            checked_ids = list(group.selected_task_ids) if group is not None else []

        self._updating_script_list = True
        self._script_list.clear()
        try:
            discovered = [
                cfg for cfg in list_exposed_scripts()
                if cfg.get("batchable", True)
            ]
        except Exception:
            discovered = []
        discovered_by_id = {cfg["id"]: cfg for cfg in discovered}
        batch_cfg = load_batch_config()
        group = batch_cfg.configs.get(self._current_config_name()) or batch_cfg.get_active()
        visible_ids = list(group.task_ids) if group is not None else []
        visible = set(visible_ids)
        display_ids = list(visible_ids)
        configs = [
            discovered_by_id[task_id] for task_id in display_ids
            if task_id in discovered_by_id
        ]
        self._script_configs_by_id = {cfg["id"]: cfg for cfg in configs}
        self._script_candidate_order = [
            task_id for task_id in display_ids if task_id in self._script_configs_by_id
        ]
        checked = set(checked_ids)
        self._script_order = [
            task_id for task_id in self._script_candidate_order if task_id in checked
        ]
        # 勾选过、但此刻发现不到的脚本（被删、取消暴露、改成 dedicated、
        # 挪进 standalone/…）。它们不参与本次执行，但必须原位留在
        # selected_task_ids 里：顺手抹掉的话，脚本一恢复暴露，用户的
        # 勾选就再也回不来了，而且全程没有任何提示。
        self._missing_script_ids = []
        for index, script_id in enumerate(checked_ids):
            if script_id in visible and script_id not in self._script_configs_by_id:
                self._missing_script_ids.append((index, script_id))
        self._warn_missing_scripts()

        # 脚本与配置两页使用同一行高，避免树控件按字体最小高度挤成一团。
        row_height = _batch_list_row_height(self._script_list)
        for script_cfg in configs:
            item = QTreeWidgetItem([script_cfg["name"], ""])
            item.setData(0, Qt.ItemDataRole.UserRole, script_cfg)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            is_checked = script_cfg["id"] in checked_ids
            item.setCheckState(
                0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked
            )
            item.setSizeHint(0, QSize(0, row_height))
            item.setSizeHint(1, QSize(0, row_height))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            self._script_list.addTopLevelItem(item)
        self._updating_script_list = False
        self._refresh_script_order_column()

    def _script_id(self, item: QTreeWidgetItem | None) -> str:
        """返回脚本行绑定的 ID。"""
        if item is None:
            return ""
        cfg = item.data(0, Qt.ItemDataRole.UserRole)
        return cfg.get("id", "") if isinstance(cfg, dict) else ""

    def _on_script_item_changed(self, item: QTreeWidgetItem, column: int):
        """勾选变化后按配置组顺序更新实际执行项。"""
        if self._updating_script_list or column != 0:
            return
        script_id = self._script_id(item)
        if not script_id:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            selected = {*self._script_order, script_id}
        else:
            selected = set(self._script_order) - {script_id}
        self._script_order = [
            candidate for candidate in self._script_candidate_order
            if candidate in selected
        ]
        self._refresh_script_order_column()
        self._persist_script_order()

    def _set_all_scripts_checked(self, checked: bool) -> None:
        self._updating_script_list = True
        try:
            for index in range(self._script_list.topLevelItemCount()):
                item = self._script_list.topLevelItem(index)
                if item is None:
                    continue
                item.setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self._updating_script_list = False
        self._script_order = list(self._script_candidate_order) if checked else []
        self._refresh_script_order_column()
        self._persist_script_order()

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

    def _warn_missing_scripts(self):
        """勾选过但当前发现不到的脚本，必须明确告诉用户它不会执行。"""
        if not self._missing_script_ids:
            return
        names = "、".join(script_id for _index, script_id in
                          self._missing_script_ids)
        message = tr("[批量] 以下已勾选脚本当前不可用，本次不会执行：") + names
        logger.warning(message)
        append_log = getattr(self._host, "append_log", None)
        if callable(append_log):
            append_log(message)

    def _merged_script_ids(self) -> list[str]:
        """当前执行顺序 + 暂时不可用的 id（按原位插回）。"""
        merged = list(self._script_order)
        for index, script_id in self._missing_script_ids:
            merged.insert(min(index, len(merged)), script_id)
        return merged

    def _persist_script_order(self):
        """立即保存当前配置组的实际任务勾选。"""
        cfg = load_batch_config()
        item = cfg.configs.get(self._current_config_name())
        if item is None:
            return
        item.selected_task_ids = self._merged_script_ids()
        save_batch_config(cfg)

    def _checked_script_ids(self) -> list[str]:
        """获取勾选的脚本 ID 列表"""
        return list(self._script_order)

    def _checked_scripts(self) -> list[BatchScript]:
        """获取勾选的脚本 BatchScript 列表

        只携带参数定义；配置组与用户参数值由批量启动计划统一解析。
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
                scope=cfg.get("scope", "daily"),
                parameters=list(cfg.get("parameters") or []),
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
        usernames = self._get_enabled_usernames()
        scripts = self._checked_scripts()

        if not usernames:
            self._host.append_log(tr("[批量] 暂无启用的用户，请到「用户」页勾选"))
            return
        if not scripts:
            self._host.append_log(tr("[批量] 请至少勾选一个脚本"))
            return

        cfg = load_batch_config()

        # 构建进度表
        config = cfg.configs.get(self._current_config_name())
        self._build_progress_table(usernames, config, scripts)
        self._set_config_enabled(False)

        ok = self._host.run_batch(usernames, scripts)
        if not ok:
            self._set_config_enabled(True)

    def _build_progress_table(self, usernames: list[str],
                              config: BatchConfigItem | None,
                              scripts: list[BatchScript]):
        """初始化进度表：行×脚本 全量行"""
        self._progress_table.setRowCount(0)
        # (run_idx, script_id) → 表行号。执行侧按同样的 行×脚本 顺序推进，
        # 所以这个映射是精确的；靠标签文本反查则会在标签重名、或两边标签
        # 算法不一致时把状态刷到别人的行上（甚至一行都刷不到）。
        self._progress_row_index = {}
        self._progress_row_context = {}
        self._progress_task_plan = {}
        for run_idx, username in enumerate(usernames):
            label = username
            for script in scripts:
                row = self._progress_table.rowCount()
                self._progress_row_index[(run_idx, script.id)] = row
                self._progress_row_context[row] = (run_idx, script.id)
                self._progress_table.insertRow(row)
                label_item = QTableWidgetItem(label)
                label_item.setToolTip(label)
                self._progress_table.setItem(row, 0, label_item)
                script_name = str(script.name)
                script_item = QTableWidgetItem(script_name)
                script_item.setToolTip(script_name)
                self._progress_table.setItem(row, 1, script_item)
                status_item = QTableWidgetItem(ST_PENDING)
                status_item.setToolTip(ST_PENDING)
                status_item.setBackground(_status_color(ST_PENDING))
                self._progress_table.setItem(row, 2, status_item)
        # Rows may make the vertical scrollbar appear, changing viewport width.
        QTimer.singleShot(0, self._set_progress_column_widths)

    def apply_task_plan(
        self, plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        """绑定本轮执行快照，并标记使用用户独立参数的组合。"""
        self._progress_task_plan = plan
        for key, planned in plan.items():
            row = self._progress_row_index.get(key)
            if row is None:
                continue
            item = self._progress_table.item(row, 1)
            if item is None:
                continue
            prefix = "* " if planned.parameter_source == "user" else ""
            item.setText(prefix + planned.script.name)
            item.setToolTip(
                (tr("* 表示当前用户使用独立参数\n")
                 if planned.parameter_source == "user" else "")
                + tr("点击查看本轮任务参数"))

    @staticmethod
    def _format_param_value(value) -> str:
        if isinstance(value, bool):
            return tr("是") if value else tr("否")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    def _on_progress_cell_clicked(self, row: int, column: int) -> None:
        """点击脚本名称时显示当前用户与任务的参数快照。"""
        if column != 1:
            return
        key = self._progress_row_context.get(row)
        planned = self._progress_task_plan.get(key) if key is not None else None
        if planned is None:
            return
        if self._params_popup is not None:
            self._params_popup.close()

        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.setMinimumWidth(300)
        popup.setMaximumWidth(480)
        outer = QVBoxLayout(popup)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)
        outer.addWidget(QLabel(f"<b>{planned.script.name}</b>"))
        source = (tr("用户独立参数") if planned.parameter_source == "user"
                  else tr("全局任务参数"))
        detail = QLabel(
            tr("执行用户：{username}\n参数来源：{source}").format(
                username=planned.username, source=source))
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(detail)

        body = QWidget()
        form = QFormLayout(body)
        form.setContentsMargins(0, 0, 0, 0)
        definitions = {
            str(item.get("name")): str(item.get("label") or item.get("name"))
            for item in (planned.script.parameters or [])
            if isinstance(item, dict) and item.get("name")
        }
        if planned.params:
            for name, value in planned.params.items():
                value_label = QLabel(self._format_param_value(value))
                value_label.setWordWrap(True)
                value_label.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(definitions.get(name, name) + "：", value_label)
        else:
            form.addRow(QLabel(tr("该任务没有可配置参数")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(260)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        item = self._progress_table.item(row, column)
        if item is None:
            return
        rect = self._progress_table.visualItemRect(item)
        viewport = cast(QWidget, self._progress_table.viewport())
        position = viewport.mapToGlobal(rect.bottomLeft())
        popup.adjustSize()
        popup.move(position)
        popup.show()
        self._params_popup = popup

    def update_progress(self, run_idx: int, entry_label: str,
                        script_id: str, status: str):
        """更新进度表中该 (条目, 脚本) 行的状态（由 host 调用）"""
        row = self._progress_row_index.get((run_idx, script_id))
        if row is None or row >= self._progress_table.rowCount():
            logger.warning(
                f"批量进度无处安放，已忽略: run_idx={run_idx} "
                f"script_id={script_id} label={entry_label!r}"
            )
            return
        status_item = QTableWidgetItem(status)
        status_item.setToolTip(status)
        color = _status_color(status)
        if color:
            status_item.setBackground(color)
        self._progress_table.setItem(row, 2, status_item)
        self._progress_table.scrollToItem(status_item)

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
        self._refresh_group_contents()

    def refresh_scripts(self):
        """脚本发现/暴露层变更后调用，只刷新脚本候选列表。

        候选列表、显示名和 wf 路径都来自 ``list_exposed_scripts()``。不跟着
        「脚本配置」重新拉一遍的话，批量页会一直拿着上次启动时的快照：改过
        的显示名不更新，取消暴露的脚本仍留在列表里并且照跑不误。
        """
        self._refresh_script_list()

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
        elif state == STATE_PLAN_UNSUPPORTED:
            self._btn_run.setText(tr("方案不支持"))
            self._btn_run.setStyleSheet(_STYLE_BTN_PLAN_UNSUPPORTED)
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
        """运行期间锁定配置组、脚本、用户和参数。"""
        self._script_list.setEnabled(enabled)
        self._btn_script_all.setEnabled(enabled)
        self._btn_script_none.setEnabled(enabled)
        self._config_combo.setEnabled(enabled)
        self._btn_user_all.setEnabled(enabled)
        self._btn_user_none.setEnabled(enabled)
        self._rounds_spin.setEnabled(enabled)
        self._workflow_params_panel.setEnabled(enabled)
        for cb, _username in self._entry_checkboxes:
            cb.setEnabled(enabled)
