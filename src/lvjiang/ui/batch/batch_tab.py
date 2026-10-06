"""批量执行 Tab — 脚本 / 单元 / 进度 / 参数四页子 Tab

挂载于主窗口左侧 Tab「批量」。
仿照调律 Tab 结构：顶部开始/停止按钮 + 四页子 Tab。
- 进度：执行进度表
- 脚本：勾选要执行的脚本
- 用户：勾选当前配置组实际执行的用户
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from loguru import logger
from PyQt6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
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

from lvjiang.ui.combo_box import AutoWidthComboBox

from ...core.batch_config import (
    BatchConfigItem,
    declares_unit_prepare,
    load_batch_config,
)
from ...core.batch_run import (
    BatchRunDraft,
    BatchSelection,
    active_group_id,
    forget_drafts,
    load_draft,
    save_draft,
    set_active_group_id,
)
from ...core.profile.models import MODEL_QUOTA
from ...core.profile.schema import get_profile_config
from ...core.profile.service import profile_read
from ...i18n import tr
from ..button_styles import (
    apply_button_style,
    apply_execution_button_style,
    fit_button_width,
)
from ..main.run_control import (
    STATE_PAUSING,
    STATE_PLAN_UNSUPPORTED,
    STATE_START_DENIED,
    STATE_STOPPING,
)
from ..theme import get_theme_manager
from ..widgets import add_top_aligned_row
from .batch_runner import (
    ST_FAILED,
    ST_PENDING,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchRunSpec,
    BatchScript,
    PlannedTask,
)


@dataclass
class _BatchProgressState:
    rows: list[tuple[int, str, str, str, str]]
    plans: dict[tuple[int, str], PlannedTask]
    terminal: bool = False


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


@dataclass(frozen=True)
class ProfileOrderStatus:
    """右键「指定顺序排序」当前的可用性。

    属性单元或配置组没有指定排序时，这个动作不适用，隐藏；配置了但
    Profile 定义已被删除属于「功能存在、此刻不可用」，保留并置灰，把原因
    写在菜单项上，不让用户点了才发现没反应。
    """

    visible: bool = False
    enabled: bool = False
    reason: str = ""


class _ReorderTreeWidget(QTreeWidget):
    """Flat tree that reports a completed internal drag/drop reorder."""

    order_changed = pyqtSignal()
    restore_order_requested = pyqtSignal()
    shuffle_order_requested = pyqtSignal()
    profile_order_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None, *, profile_order: bool = False) -> None:
        super().__init__(parent)
        self._profile_order = profile_order
        # 菜单弹出时现算：Profile 定义可能在别处被删，缓存值会过期。
        self.profile_order_status: Callable[[], ProfileOrderStatus] | None = None
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_order_menu)

    def dropEvent(self, event: QDropEvent | None) -> None:
        super().dropEvent(event)
        if event is not None and event.isAccepted():
            self.order_changed.emit()

    def _show_order_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        restore_action = QAction(tr("恢复默认顺序"), menu)
        shuffle_action = QAction(tr("随机打乱顺序"), menu)
        restore_action.triggered.connect(
            lambda _checked=False: self.restore_order_requested.emit())
        shuffle_action.triggered.connect(
            lambda _checked=False: self.shuffle_order_requested.emit())
        has_rows = self.topLevelItemCount() > 0
        restore_action.setEnabled(has_rows)
        shuffle_action.setEnabled(self.topLevelItemCount() > 1)
        menu.addAction(restore_action)
        menu.addAction(shuffle_action)
        if self._profile_order:
            status = (self.profile_order_status() if self.profile_order_status
                      else ProfileOrderStatus())
            if status.visible:
                text = tr("指定顺序排序")
                if status.reason:
                    text += f"（{status.reason}）"
                profile_action = QAction(text, menu)
                profile_action.setEnabled(has_rows and status.enabled)
                profile_action.setToolTip(status.reason)
                profile_action.triggered.connect(
                    lambda _checked=False: self.profile_order_requested.emit())
                menu.addAction(profile_action)
                menu.setToolTipsVisible(True)
        viewport = cast(QWidget, self.viewport())
        menu.exec(viewport.mapToGlobal(position))


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
        self._run_progress: dict[str, _BatchProgressState] = {}
        self._visible_run_id = ""
        # 三层状态在本页的落点：_item 是配置组定义（只读），_draft 是本次运行
        # 草稿（本页唯一可写的东西），_group_id 是两者的关联键。
        self._group_id: str = ""
        self._item: BatchConfigItem | None = None
        self._draft: BatchRunDraft = BatchRunDraft()
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
        from ..hotkeys import hotkey_label
        self._btn_run = QPushButton(hotkey_label(
            tr("开始执行"), self._host._user_config.hotkeys.start))
        apply_execution_button_style(self._btn_run, "run")
        self._btn_run.clicked.connect(self._on_run_clicked)
        btn_layout.addWidget(self._btn_run, 1)

        self._btn_pause_resume = QPushButton(tr("暂停"))
        self._btn_pause_resume.setEnabled(False)
        apply_execution_button_style(self._btn_pause_resume, "disabled")
        self._btn_pause_resume.clicked.connect(self._on_pause_resume_clicked)
        btn_layout.addWidget(self._btn_pause_resume, 1)
        layout.addLayout(btn_layout)

        config_row = QHBoxLayout()
        config_row.setContentsMargins(0, 0, 0, 0)
        config_row.setSpacing(8)
        config_row.addWidget(QLabel(tr("当前配置：")))
        self._config_combo = AutoWidthComboBox()
        self._config_combo.setMinimumWidth(150)
        self._config_combo.setMinimumHeight(32)
        self._config_combo.currentIndexChanged.connect(self._on_config_changed)
        config_row.addWidget(self._config_combo, stretch=1)
        layout.addLayout(config_row)

        # ── 四页子 Tab ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_script_page(), tr("脚本"))
        self._sub_tabs.addTab(self._build_config_page(), tr("单元"))
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
        # 标签一律左对齐：这一页混着输入项与只读展示，右对齐时两种行的文字
        # 起点会错开，读起来像两张表。
        summary_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._rounds_spin = QSpinBox()
        self._rounds_spin.setRange(1, 999)
        self._rounds_spin.setValue(1)
        self._rounds_spin.valueChanged.connect(self._on_rounds_changed)
        summary_form.addRow(tr("执行轮数："), self._rounds_spin)
        # 调度单元与指定排序属于配置组定义（它们决定候选列表的内容和生命周期
        # 契约），在「批量配置」里改；这里只如实显示本次用的是什么。
        self._unit_value = QLabel()
        self._unit_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_form.addRow(tr("调度单元："), self._unit_value)
        self._profile_sort_value = QLabel()
        self._profile_sort_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._profile_sort_label = QLabel(tr("指定排序："))
        self._summary_form = summary_form
        summary_form.addRow(self._profile_sort_label, self._profile_sort_value)
        self._unattended_check = QCheckBox()
        self._unattended_check.toggled.connect(self._on_unattended_toggled)
        summary_form.addRow(tr("无人值守："), self._unattended_check)
        self._workflow_labels: dict[str, QLabel] = {}
        for key, label in (
            ("batch_setup", tr("批次准备") + "："),
            ("prepare_item", tr("条目准备") + "："),
            ("finish_item", tr("条目收尾") + "："),
            ("batch_teardown", tr("批次收尾") + "："),
            ("recover_unattended", tr("异常恢复") + "："),
        ):
            value = QLabel()
            value.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self._workflow_labels[key] = value
            add_top_aligned_row(summary_form, label, value)
        layout.addWidget(summary_group)

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
        script_label = QLabel(tr("<b>选择执行脚本</b>"))
        script_label.setToolTip(tr("拖动可临时调整实际执行顺序"))
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

        self._script_list = _ReorderTreeWidget()
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
        self._script_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._script_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._script_list.setToolTip(
            tr("拖动可调整实际执行顺序；右键可恢复默认或随机打乱顺序；"
               "批量配置中的顺序仅作为初始顺序"))
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
        self._script_list.order_changed.connect(self._on_script_rows_moved)
        self._script_list.restore_order_requested.connect(
            self._restore_script_order)
        self._script_list.shuffle_order_requested.connect(
            self._shuffle_script_order)
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

        user_viewport = cast(QWidget, self._user_list.viewport())
        user_header = cast(QHeaderView, self._user_list.header())
        user_available = max(
            user_viewport.width() - _TABLE_VIEWPORT_SAFETY_MARGIN,
            0,
        )
        user_name_width = max(
            user_header.minimumSectionSize(),
            user_available - order_width,
        )
        self._user_list.setColumnWidth(0, user_name_width)
        self._user_list.setColumnWidth(1, order_width)

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
        self._unit_label = QLabel(tr("<b>选择执行单元</b>"))
        self._unit_label.setToolTip(tr("拖动可临时调整实际执行顺序"))
        select_row.addWidget(self._unit_label)
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

        self._user_list = _ReorderTreeWidget(profile_order=True)
        self._user_list.setColumnCount(2)
        self._user_list.setHeaderLabels([tr("单元候选"), tr("执行顺序")])
        self._user_list.setRootIsDecorated(False)
        self._user_list.setUniformRowHeights(True)
        self._user_list.setIndentation(0)
        self._user_list.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._user_list.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._user_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self._user_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._user_list.setToolTip(
            tr("拖动可调整实际执行顺序；右键可恢复默认或随机打乱；"
               "批量配置中的顺序仅作为初始顺序"))
        header = self._user_list.header()
        assert header is not None
        header.setMinimumHeight(32)
        header.setMinimumSectionSize(48)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._user_list.setColumnWidth(
            1, _four_cjk_column_width(self._user_list))
        self._user_list.itemChanged.connect(self._on_user_item_changed)
        self._user_list.order_changed.connect(self._on_user_rows_moved)
        self._user_list.restore_order_requested.connect(
            self._restore_user_order)
        self._user_list.shuffle_order_requested.connect(
            self._shuffle_user_order)
        self._user_list.profile_order_requested.connect(
            self._sort_user_order_by_profile)
        self._user_list.profile_order_status = self._profile_order_status
        layout.addWidget(self._user_list, stretch=1)

        self._user_order: list[str] = []
        self._user_candidate_order: list[str] = []
        self._updating_user_list = False
        return widget

    # ─── 配置选择 ─────────────────────────────────────────

    def _load_group_state(self, group_id: str = "") -> None:
        """按 ID 载入定义与草稿，并按定义协调草稿。

        协调规则见 `BatchSelection.reconcile`：配置组改了可见范围，用户刚调好的
        本次顺序不该被清掉；新加进来的条目按定义层的默认勾选加入。
        """
        cfg = load_batch_config()
        self._group_id = cfg.resolve_id(
            group_id or self._group_id or active_group_id())
        self._item = cfg.get(self._group_id)
        self._draft = load_draft(self._group_id)
        item = self._item
        if item is None:
            return
        self._draft.reconcile(
            task_candidates=list(item.task_ids),
            task_defaults=list(item.default_task_ids),
            entry_candidates=self._unit_candidates(item),
            entry_defaults=self._entry_defaults(item),
            unit_key=item.execution_unit_key,
        )

    def _entry_defaults(self, item: BatchConfigItem) -> list[str]:
        """当前调度单元的默认勾选。属性单元没配过默认值时按全选处理。"""
        if item.execution_unit_key == "user":
            return list(item.default_usernames)
        configured = item.default_units.get(item.execution_unit_key)
        if configured is None:
            return self._unit_candidates(item)
        return list(configured)

    def _entry_selection(self) -> BatchSelection:
        key = self._item.execution_unit_key if self._item is not None else "user"
        return self._draft.entry_selection(key)

    def _save_draft(self) -> None:
        """草稿立即落 session。它从不写 batch.json——定义层只由配置窗口改。"""
        if self._group_id:
            save_draft(self._group_id, self._draft)

    def _refresh_config_combo(self):
        """刷新配置下拉框。条目带稳定 ID，重命名不影响选中与草稿关联。"""
        cfg = load_batch_config()
        self._config_combo.blockSignals(True)
        self._config_combo.clear()
        for group_id, item in cfg.configs.items():
            self._config_combo.addItem(item.name, group_id)
        current = cfg.resolve_id(self._group_id or active_group_id())
        index = self._config_combo.findData(current)
        if index >= 0:
            self._config_combo.setCurrentIndex(index)
        self._config_combo.blockSignals(False)
        forget_drafts(list(cfg.configs))

    def _on_config_changed(self, index: int):
        """切换配置组 → 换到它自己的运行草稿，并记下主页面活动组。"""
        if index < 0:
            return
        group_id = str(self._config_combo.itemData(index) or "")
        set_active_group_id(group_id)
        self._refresh_group_contents(group_id)

    def _refresh_group_contents(self, group_id: str = "") -> None:
        self._load_group_state(group_id)
        self._refresh_script_list()
        self._refresh_entry_list()
        self._refresh_params()

    def _refresh_params(self) -> None:
        item = self._item
        self._rounds_spin.blockSignals(True)
        self._rounds_spin.setValue(self._draft.rounds)
        self._rounds_spin.blockSignals(False)
        unit_key = item.execution_unit_key if item is not None else "user"
        self._unit_value.setText(
            tr("用户名") if unit_key == "user" else unit_key)
        self._unit_value.setToolTip(
            tr("调度单元属于配置组定义，在「工具 → 批量配置」中修改"))
        user_unit = unit_key == "user"
        sort_key = item.profile_sort_key if item is not None else ""
        if sort_key:
            direction = tr("升序") if (
                item is not None and item.profile_sort_direction == "asc"
            ) else tr("降序")
            self._profile_sort_value.setText(f"{sort_key}（{direction}）")
        else:
            self._profile_sort_value.setText(tr("不指定"))
        self._profile_sort_value.setToolTip(
            tr("排序键属于配置组定义；在用户列表右键「按 Profile 排序」执行一次，"
               "排完的顺序只属于本次运行"))
        self._summary_form.setRowVisible(self._profile_sort_label, user_unit)
        recover_wf = (item.workflows.recover_unattended
                      if item is not None else "")
        self._unattended_check.blockSignals(True)
        self._unattended_check.setChecked(
            self._draft.unattended and bool(recover_wf))
        self._unattended_check.blockSignals(False)
        self._unattended_check.setEnabled(bool(recover_wf))
        self._unattended_check.setToolTip(tr(
            "本次长时间无人看守时勾选：任务遇到异常暂停时不再等人，该任务按"
            "失败记录并跳过，随后由配置组的「异常恢复 wf」把游戏收回登录主页，"
            "再继续下一个任务。确认、输入和选择仍等待用户交互"
        ) if recover_wf else tr(
            "需要先在「工具 → 批量配置」为本配置组配置「异常恢复 wf」：无人值守"
            "撞上异常暂停后要靠它把游戏收回登录主页"))
        for key, label in self._workflow_labels.items():
            path = getattr(item.workflows, key) if item is not None else ""
            label.setText(path or tr("未配置"))

    def _on_rounds_changed(self, rounds: int) -> None:
        self._draft.rounds = int(rounds)
        self._save_draft()

    def _on_unattended_toggled(self, checked: bool) -> None:
        self._draft.unattended = bool(checked)
        self._save_draft()

    def _unit_candidates(self, config: BatchConfigItem) -> list[str]:
        if config.execution_unit_key == "user":
            return list(config.usernames)
        manager = getattr(self._host, "_user_manager", None)
        if manager is None:
            return []
        values: dict[str, None] = {}
        for username in config.usernames:
            user = manager.get_user(username)
            value = (str(user.attributes.get(config.execution_unit_key, "")).strip()
                     if user is not None else "")
            if value:
                values[value] = None
        return list(values)

    # ─── 行列表 ──────────────────────────────────────────

    @staticmethod
    def _apply_tree_order(
        tree: QTreeWidget,
        ordered_ids: list[str],
        item_id: Callable[[QTreeWidgetItem | None], str],
    ) -> None:
        """按 ID 重排现有行，未出现在目标顺序中的行保持原相对顺序。"""
        current_items: list[QTreeWidgetItem] = []
        while tree.topLevelItemCount():
            item = tree.takeTopLevelItem(0)
            if item is not None:
                current_items.append(item)

        items_by_id = {
            current_id: item
            for item in current_items
            if (current_id := item_id(item))
        }
        reordered = [
            items_by_id[current_id]
            for current_id in ordered_ids
            if current_id in items_by_id
        ]
        reordered_ids = {item_id(item) for item in reordered}
        reordered.extend(
            item for item in current_items
            if item_id(item) not in reordered_ids
        )
        tree.addTopLevelItems(reordered)

    @staticmethod
    def _tree_order(
        tree: QTreeWidget,
        item_id: Callable[[QTreeWidgetItem | None], str],
    ) -> list[str]:
        return [
            current_id
            for index in range(tree.topLevelItemCount())
            if (current_id := item_id(tree.topLevelItem(index)))
        ]

    def _refresh_entry_list(self):
        """刷新用户页的勾选列表。"""
        config = self._item
        self._updating_user_list = True
        self._user_list.clear()
        if not config:
            self._user_order = []
            self._user_candidate_order = []
            self._updating_user_list = False
            return

        key = config.execution_unit_key
        self._user_list.setToolTip(
            tr("拖动可调整实际执行顺序；右键可恢复默认、随机打乱或按 Profile 排序；"
               "批量配置中的顺序仅作为初始顺序")
            if key == "user" else
            tr("拖动可调整实际执行顺序；右键可恢复默认或随机打乱；"
               "批量配置中的顺序仅作为初始顺序"))
        candidate_label = tr("单元候选（用户名）") if key == "user" else (
            tr("单元候选（{key}）").format(key=key))
        self._user_list.headerItem().setText(0, candidate_label)
        header = self._user_list.header()
        assert header is not None
        self._user_list.setColumnWidth(
            0, max(self._user_list.columnWidth(0),
                   header.fontMetrics().horizontalAdvance(candidate_label) + 24))
        # 顺序与勾选都取本次草稿：草稿已按定义层协调过（见 _load_group_state）
        selection = self._entry_selection()
        display_order = list(selection.order)
        selected = set(selection.checked)
        self._user_candidate_order = list(display_order)
        self._user_order = selection.execution_order()
        row_height = _batch_list_row_height(self._user_list)
        for username in display_order:
            item = QTreeWidgetItem([username, ""])
            item.setData(0, Qt.ItemDataRole.UserRole, username)
            item.setFlags(
                (item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsDropEnabled
            )
            item.setCheckState(
                0, Qt.CheckState.Checked
                if username in selected else Qt.CheckState.Unchecked)
            item.setSizeHint(0, QSize(0, row_height))
            item.setSizeHint(1, QSize(0, row_height))
            item.setTextAlignment(1, Qt.AlignmentFlag.AlignCenter)
            self._user_list.addTopLevelItem(item)
        self._updating_user_list = False
        self._refresh_user_order_column()

    def _set_all_entries_checked(self, checked: bool):
        """全选/全不选行"""
        self._updating_user_list = True
        try:
            for index in range(self._user_list.topLevelItemCount()):
                item = self._user_list.topLevelItem(index)
                if item is None:
                    continue
                item.setCheckState(
                    0, Qt.CheckState.Checked
                    if checked else Qt.CheckState.Unchecked)
        finally:
            self._updating_user_list = False
        self._user_order = list(self._user_candidate_order) if checked else []
        self._refresh_user_order_column()
        self._store_entry_selection()

    @staticmethod
    def _user_name(item: QTreeWidgetItem | None) -> str:
        if item is None:
            return ""
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else ""

    def _on_user_item_changed(self, _item: QTreeWidgetItem, column: int) -> None:
        if self._updating_user_list or column != 0:
            return
        self._sync_user_order_from_rows()

    def _on_user_rows_moved(self, *_args) -> None:
        if not self._updating_user_list:
            self._sync_user_order_from_rows()

    def _restore_user_order(self) -> None:
        """恢复默认：顺序回到定义层初始顺序，勾选回到默认勾选。"""
        item = self._item
        if item is None:
            return
        defaults = BatchSelection.from_defaults(
            self._unit_candidates(item), self._entry_defaults(item))
        key = item.execution_unit_key
        if key == "user":
            self._draft.users = defaults
        else:
            self._draft.units[key] = defaults
        self._save_draft()
        self._refresh_entry_list()

    def _shuffle_user_order(self) -> None:
        order = self._tree_order(self._user_list, self._user_name)
        random.shuffle(order)
        self._updating_user_list = True
        try:
            self._apply_tree_order(self._user_list, order, self._user_name)
        finally:
            self._updating_user_list = False
        self._sync_user_order_from_rows()

    def _profile_order_status(self) -> ProfileOrderStatus:
        """右键菜单弹出时判定「指定顺序排序」是否可用。

        不缓存：Profile 定义可能在定义编辑器里被删掉，而批量页收不到通知。
        """
        group = self._item
        if group is None or group.execution_unit_key != "user":
            return ProfileOrderStatus()
        key = group.profile_sort_key
        if not key:
            # 当前配置组没有指定排序，这个动作无从谈起。
            return ProfileOrderStatus()
        try:
            exists = get_profile_config().get_key(key) is not None
        except (OSError, ValueError) as exc:
            logger.warning(f"读取 Profile 定义失败，指定排序不可用: {exc}")
            exists = False
        if not exists:
            return ProfileOrderStatus(
                visible=True, enabled=False,
                reason=tr("Profile 定义已不存在"))
        return ProfileOrderStatus(visible=True, enabled=True)

    def _sort_user_order_by_profile(self) -> None:
        """按当前配置的 Profile 数值对用户行执行一次稳定排序。"""
        group = self._item
        if group is None or not group.profile_sort_key:
            return
        if group.execution_unit_key != "user":
            return
        schema = get_profile_config()
        key = group.profile_sort_key
        if schema.get_key(key) is None:
            logger.warning(f"指定顺序排序已取消：Profile 定义不存在: {key}")
            return
        order = self._tree_order(self._user_list, self._user_name)
        values: dict[str, float | None] = {}
        quota = schema.get_model_type(key) == MODEL_QUOTA
        for username in order:
            try:
                value = profile_read(username, key)
            except Exception as exc:
                logger.warning(f"读取用户 Profile 排序值失败: {username}/{key}: {exc}")
                values[username] = None
                continue
            if value is None and quota:
                # 配额尚无记录等于本周期尚未完成，按 0 排序。
                values[username] = 0.0
                continue
            try:
                number = float(value) if value is not None else None
            except (TypeError, ValueError):
                number = None
            values[username] = number if number is not None and math.isfinite(number) else None
        descending = group.profile_sort_direction == "desc"
        # 缺失/非数字一律排末尾；Python 的稳定排序保留同值用户的原相对顺序。
        sorted_order = sorted(
            order,
            key=lambda username: (
                values[username] is None,
                -(values[username] or 0) if descending else (values[username] or 0),
            ),
        )
        self._updating_user_list = True
        try:
            self._apply_tree_order(self._user_list, sorted_order, self._user_name)
        finally:
            self._updating_user_list = False
        self._sync_user_order_from_rows()
        missing = sum(value is None for value in values.values())
        message = tr("已按 Profile 排序用户") + f": {key} ({len(order)} " + tr("人") + ")"
        if missing:
            message += f"；{missing} " + tr("人无数值，置于末尾")
        logger.info(message)
        append_log = getattr(self._host, "append_log", None)
        if callable(append_log):
            append_log(message)

    def _sync_user_order_from_rows(self) -> None:
        self._user_candidate_order = []
        self._user_order = []
        for index in range(self._user_list.topLevelItemCount()):
            item = self._user_list.topLevelItem(index)
            if item is None:
                continue
            username = self._user_name(item)
            if not username:
                continue
            self._user_candidate_order.append(username)
            if item.checkState(0) == Qt.CheckState.Checked:
                self._user_order.append(username)
        self._refresh_user_order_column()
        self._store_entry_selection()

    def _refresh_user_order_column(self) -> None:
        order_by_name = {
            username: str(index)
            for index, username in enumerate(self._user_order, start=1)
        }
        self._updating_user_list = True
        try:
            for index in range(self._user_list.topLevelItemCount()):
                item = self._user_list.topLevelItem(index)
                if item is None:
                    continue
                item.setText(1, order_by_name.get(self._user_name(item), ""))
        finally:
            self._updating_user_list = False

    def _store_entry_selection(self) -> None:
        """把列表当前的顺序与勾选写回本次草稿。**不碰配置组定义。**"""
        selection = self._entry_selection()
        selection.order = list(self._user_candidate_order)
        selection.checked = list(self._user_order)
        self._save_draft()

    def _get_enabled_usernames(self) -> list[str]:
        """本次实际执行顺序（用户名或属性值）。"""
        return list(self._user_order)

    # ─── 脚本列表 ─────────────────────────────────────────

    def _refresh_script_list(self, checked_ids: list[str] | None = None):
        """刷新脚本勾选列表（数据源与日常下拉一致）"""
        from ...workflows.discovery import list_exposed_scripts, script_display_name

        if checked_ids is None:
            checked_ids = list(self._draft.tasks.checked)

        self._updating_script_list = True
        self._script_list.clear()
        try:
            run_env_getter = getattr(self._host, "_selected_run_env", None)
            run_env = run_env_getter() if callable(run_env_getter) else None
            discovered = [
                cfg for cfg in list_exposed_scripts(run_env)
                if cfg.get("batchable", False)
            ]
        except Exception:
            discovered = []
        discovered_by_id = {cfg["id"]: cfg for cfg in discovered}
        group = self._item
        visible = set(group.task_ids) if group is not None else set()
        # 显示顺序就是草稿里的本次顺序；取消勾选不该让条目跳到末尾，所以顺序
        # 由 order 决定，与勾选集合无关。
        display_ids = [
            task_id for task_id in self._draft.tasks.order if task_id in visible
        ]
        configs = [
            discovered_by_id[task_id] for task_id in display_ids
            if task_id in discovered_by_id
        ]
        self._script_configs_by_id = {cfg["id"]: cfg for cfg in configs}
        self._script_candidate_order = [
            task_id for task_id in display_ids if task_id in self._script_configs_by_id
        ]
        self._script_order = [
            task_id for task_id in checked_ids
            if task_id in self._script_configs_by_id
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
            item = QTreeWidgetItem([script_display_name(script_cfg), ""])
            item.setData(0, Qt.ItemDataRole.UserRole, script_cfg)
            item.setFlags(
                (item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                & ~Qt.ItemFlag.ItemIsDropEnabled
            )
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

    def _on_script_rows_moved(self, *_args) -> None:
        if self._updating_script_list:
            return
        self._script_candidate_order = []
        self._script_order = []
        for index in range(self._script_list.topLevelItemCount()):
            item = self._script_list.topLevelItem(index)
            if item is None:
                continue
            script_id = self._script_id(item)
            if not script_id:
                continue
            self._script_candidate_order.append(script_id)
            if item.checkState(0) == Qt.CheckState.Checked:
                self._script_order.append(script_id)
        self._refresh_script_order_column()
        self._persist_script_order()

    def _restore_script_order(self) -> None:
        """恢复默认：顺序回到定义层初始顺序，勾选回到默认勾选。"""
        group = self._item
        if group is None:
            return
        self._draft.tasks = BatchSelection.from_defaults(
            list(group.task_ids), list(group.default_task_ids))
        self._save_draft()
        self._refresh_script_list()

    def _shuffle_script_order(self) -> None:
        order = self._tree_order(self._script_list, self._script_id)
        random.shuffle(order)
        self._updating_script_list = True
        try:
            self._apply_tree_order(self._script_list, order, self._script_id)
        finally:
            self._updating_script_list = False
        self._on_script_rows_moved()

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
        """把列表当前顺序与勾选写回本次草稿。**不碰配置组定义。**"""
        self._draft.tasks.order = list(self._script_candidate_order) + [
            task_id for _index, task_id in self._missing_script_ids
        ]
        self._draft.tasks.checked = self._merged_script_ids()
        self._save_draft()

    def _checked_script_ids(self) -> list[str]:
        """获取勾选的脚本 ID 列表"""
        return list(self._script_order)

    def _checked_scripts(self) -> list[BatchScript]:
        """获取勾选的脚本 BatchScript 列表

        只携带参数定义；配置组与用户参数值由批量启动计划统一解析。
        """
        scripts: list[BatchScript] = []
        from ...workflows.discovery import script_display_name
        for script_id in self._script_order:
            cfg = self._script_configs_by_id.get(script_id)
            if cfg is None:
                continue
            scripts.append(BatchScript(
                id=cfg["id"],
                name=script_display_name(cfg),
                wf_file=cfg.get("wf_file", ""),
                class_name=cfg.get("class", ""),
                scope=cfg.get("scope", "daily"),
                parameters=list(cfg.get("parameters") or []),
                env=list(cfg.get("env") or []),
                batch_check=str(cfg.get("batch_check") or ""),
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
            self._host.append_log(tr("[批量] 暂无启用的单元，请到「单元」页勾选"))
            return
        if not scripts:
            self._host.append_log(tr("[批量] 请至少勾选一个脚本"))
            return

        config = self._item
        if config is not None and config.execution_unit_key != "user":
            # 属性单元的一个单元值可能对应多名用户，必须由条目准备 wf 选定并回传
            # 用户名。不声明这条协议的 wf 跑起来只会让每个单元都以同一个协议错误
            # 被丢弃，整批空跑，所以在动客户端之前就拦住。
            from PyQt6.QtWidgets import QMessageBox
            if not config.workflows.prepare_item:
                QMessageBox.warning(
                    self, tr("无法开始批量任务"),
                    tr("当前按属性调度，请先在「工具 → 批量配置」中配置条目准备 wf。"),
                )
                return
            if not declares_unit_prepare(config.workflows.prepare_item):
                QMessageBox.warning(
                    self, tr("无法开始批量任务"),
                    tr("当前按「{key}」调度，但条目准备工作流「{wf}」不会选定并返回"
                       "本单元的用户名。请改用 batch/prepare_item_by_attr.wf，"
                       "或在自写的准备工作流里声明 #% batch_unit_prepare: true "
                       "并返回 username。").format(
                        key=config.execution_unit_key,
                        wf=config.workflows.prepare_item),
                )
                return
        if config is None:
            self._host.append_log(tr("[批量] 暂无配置组，请先通过 工具 → 批量配置 添加"))
            return
        self._build_progress_table(usernames, config, scripts)
        self._set_config_enabled(False)

        # 定义 + 草稿 → 不可变快照。之后再改配置组、改用户资料或在本页重新勾选，
        # 都不会影响这一批：调度器只认这份快照。
        spec = BatchRunSpec.build(
            config, self._draft, entries=usernames, scripts=scripts,
            candidate_usernames=(
                list(config.usernames)
                if config.execution_unit_key != "user" else None),
        )
        ok = self._host.run_batch(spec)
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

    def bind_run(self, task_run_id: str) -> None:
        """将当前刚初始化的进度表绑定到运行实例。"""
        rows: list[tuple[int, str, str, str, str]] = []
        for row, context in self._progress_row_context.items():
            run_idx, script_id = context
            label_item = self._progress_table.item(row, 0)
            script_item = self._progress_table.item(row, 1)
            status_item = self._progress_table.item(row, 2)
            rows.append((
                run_idx, script_id,
                label_item.text() if label_item else "",
                script_item.text() if script_item else "",
                status_item.text() if status_item else ST_PENDING,
            ))
        self._run_progress[task_run_id] = _BatchProgressState(
            rows=rows, plans=dict(self._progress_task_plan))
        self._visible_run_id = task_run_id

    def show_run(self, task_run_id: str) -> None:
        self._progress_table.setUpdatesEnabled(False)
        try:
            self._show_run_contents(task_run_id)
        finally:
            self._progress_table.setUpdatesEnabled(True)

    def _show_run_contents(self, task_run_id: str) -> None:
        """恢复任务进度内容；调用方负责暂停表格重绘。"""
        state = self._run_progress.get(task_run_id)
        if state is None:
            self._visible_run_id = ""
            self._progress_table.setRowCount(0)
            self._progress_row_index = {}
            self._progress_row_context = {}
            self._progress_task_plan = {}
            self._running = False
            self._refresh_run_button("idle")
            self._set_config_enabled(True)
            return
        self._visible_run_id = task_run_id
        self._progress_table.setRowCount(0)
        self._progress_row_index = {}
        self._progress_row_context = {}
        self._progress_task_plan = dict(state.plans)
        for run_idx, script_id, label, script_name, status in state.rows:
            row = self._progress_table.rowCount()
            self._progress_table.insertRow(row)
            self._progress_row_index[(run_idx, script_id)] = row
            self._progress_row_context[row] = (run_idx, script_id)
            self._progress_table.setItem(row, 0, QTableWidgetItem(label))
            self._progress_table.setItem(row, 1, QTableWidgetItem(script_name))
            status_item = QTableWidgetItem(status)
            color = _status_color(status)
            if color is not None:
                status_item.setBackground(color)
            self._progress_table.setItem(row, 2, status_item)
        self.apply_task_plan(state.plans)
        self._running = not state.terminal
        self._refresh_run_button("running" if self._running else "idle")
        self._set_config_enabled(not self._running)

    def apply_run_task_plan(
        self, task_run_id: str, plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            state.plans = dict(plan)
        if self._visible_run_id == task_run_id:
            self.apply_task_plan(plan)

    def apply_run_selected_unit_plan(
        self, task_run_id: str, run_idx: int,
        plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            state.plans.update(plan)
        if self._visible_run_id == task_run_id:
            self.apply_selected_unit_plan(run_idx, plan)

    def apply_selected_unit_plan(
        self, _run_idx: int, plan: dict[tuple[int, str], PlannedTask],
    ) -> None:
        """属性单元确定角色后，显示该角色冻结的任务参数。"""
        self._progress_task_plan.update(plan)
        for key, planned in plan.items():
            row = self._progress_row_index.get(key)
            item = self._progress_table.item(row, 1) if row is not None else None
            if item is not None:
                item.setText(
                    ("* " if planned.parameter_source == "user" else "")
                    + planned.script.name)
                item.setToolTip(
                    tr("执行用户：{username}\n点击查看本轮任务参数").format(
                        username=planned.username))

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
                add_top_aligned_row(
                    form, definitions.get(name, name) + "：", value_label)
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

    def update_run_progress(
        self, task_run_id: str, run_idx: int, entry_label: str,
        script_id: str, status: str,
    ) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            for index, row in enumerate(state.rows):
                if row[0] == run_idx and row[1] == script_id:
                    state.rows[index] = (
                        row[0], row[1], entry_label or row[2], row[3], status)
                    break
        if self._visible_run_id == task_run_id:
            self.update_progress(run_idx, entry_label, script_id, status)

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

    def finish_run(self, task_run_id: str) -> None:
        state = self._run_progress.get(task_run_id)
        if state is not None:
            state.terminal = True
        if self._visible_run_id == task_run_id:
            self.on_batch_finished({})

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
        active_states = ("running", STATE_PAUSING, "paused", STATE_STOPPING)
        self._running = state in active_states
        self._refresh_run_button(state)
        if state in active_states:
            self._set_config_enabled(False)
        else:
            self._set_config_enabled(True)

    def _on_pause_resume_clicked(self):
        """暂停/恢复按钮点击 → 转发给宿主"""
        self._host.request_pause_resume()

    def _refresh_run_button(self, state: str):
        from ..hotkeys import hotkey_label
        from ..main.run_control import other_task_running_label
        hk = self._host._user_config.hotkeys
        if label := other_task_running_label(self._host, "batch"):
            self._btn_run.setText(label)
            self._btn_run.setEnabled(False)
            apply_execution_button_style(self._btn_run, "disabled")
            self._btn_pause_resume.setText(tr("暂停"))
            self._btn_pause_resume.setEnabled(False)
            apply_execution_button_style(self._btn_pause_resume, "disabled")
            return
        if state == STATE_STOPPING:
            self._btn_run.setText(tr("停止中"))
            self._btn_run.setEnabled(False)
            apply_execution_button_style(self._btn_run, "stopping")
        elif state in ("running", STATE_PAUSING, "paused"):
            self._btn_run.setText(hotkey_label(tr("停止"), hk.stop))
            self._btn_run.setEnabled(True)
            apply_execution_button_style(self._btn_run, "stop")
        elif state == "not_ready":
            self._btn_run.setText(tr("未连接"))
            self._btn_run.setEnabled(True)
            apply_execution_button_style(self._btn_run, "not_ready")
        elif state in (STATE_PLAN_UNSUPPORTED, STATE_START_DENIED):
            # 不能落进下面的 else：并发门禁拒绝时这里也必须是灰的，否则绿色
            # 按钮点下去才报「需要激活 Lv1」。
            self._btn_run.setText(
                tr("方案不支持") if state == STATE_PLAN_UNSUPPORTED
                else self._host.start_denied_label() or tr("不能启动"))
            self._btn_run.setEnabled(True)
            apply_execution_button_style(self._btn_run, "disabled")
        else:
            self._btn_run.setText(hotkey_label(tr("开始执行"), hk.start))
            self._btn_run.setEnabled(True)
            apply_execution_button_style(self._btn_run, "run")
        # 刷新暂停/恢复按钮
        if state == "running":
            self._btn_pause_resume.setText(hotkey_label(tr("暂停"), hk.pause))
            self._btn_pause_resume.setEnabled(True)
            apply_execution_button_style(self._btn_pause_resume, "pause")
        elif state == STATE_PAUSING:
            self._btn_pause_resume.setText(tr("暂停中"))
            self._btn_pause_resume.setEnabled(False)
            apply_execution_button_style(self._btn_pause_resume, "pausing")
        elif state == "paused":
            self._btn_pause_resume.setText(hotkey_label(tr("恢复"), hk.pause))
            self._btn_pause_resume.setEnabled(True)
            apply_execution_button_style(self._btn_pause_resume, "run")
        else:
            self._btn_pause_resume.setText(tr("暂停"))
            self._btn_pause_resume.setEnabled(False)
            apply_execution_button_style(self._btn_pause_resume, "disabled")

    def _set_config_enabled(self, enabled: bool):
        """运行期间锁定配置组、脚本、用户和参数。"""
        self._script_list.setEnabled(enabled)
        self._btn_script_all.setEnabled(enabled)
        self._btn_script_none.setEnabled(enabled)
        self._config_combo.setEnabled(enabled)
        self._btn_user_all.setEnabled(enabled)
        self._btn_user_none.setEnabled(enabled)
        self._user_list.setEnabled(enabled)
        self._rounds_spin.setEnabled(enabled)
        self._unattended_check.setEnabled(
            enabled and bool(
                self._item is not None
                and self._item.workflows.recover_unattended))
