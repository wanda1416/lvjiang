"""批量执行页 - 配置、角色顺序、脚本队列与执行控制。"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import cast

from loguru import logger
from PyQt6.QtCore import QEvent, QSize, Qt, QTimer
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.execution.feedback import (
    ExecutionFeedback,
)
from lvjiang.ui.widgets.combo_box import AutoWidthComboBox

from ...core.batch.config import (
    BatchConfigItem,
    load_batch_config,
)
from ...core.batch.run import (
    BatchRunDraft,
    BatchSelection,
    active_group_id,
    forget_drafts,
    load_draft,
    save_draft,
    set_active_group_id,
)
from ...core.profile.schema import get_profile_config
from ...i18n import tr, tr_label
from ..widgets.button_styles import (
    apply_button_style,
    apply_execution_button_style,
    fit_button_width,
)
from ..widgets.misc import add_top_aligned_row
from ..widgets.theme import get_theme_manager
from .batch_execution import _BatchExecutionMixin
from .batch_runner import (
    ST_FAILED as ST_FAILED,
)
from .batch_runner import (
    ST_NOT_RUN as ST_NOT_RUN,
)
from .batch_runner import (
    ST_PENDING as ST_PENDING,
)
from .batch_runner import (
    ST_RUNNING as ST_RUNNING,
)
from .batch_runner import (
    ST_SKIPPED as ST_SKIPPED,
)
from .batch_runner import (
    ST_SUCCESS as ST_SUCCESS,
)
from .batch_runner import (
    BatchScript,
    PlannedTask,
)
from .batch_widgets import (
    _TABLE_VIEWPORT_SAFETY_MARGIN,
    ProfileOrderStatus,
    _batch_list_row_height,
    _BatchProgressState,
    _four_cjk_column_width,
    _ReorderTreeWidget,
)
from .batch_widgets import (
    _status_color as _status_color,
)
from .lifecycle_params import LifecycleParamsPanel
from .task_params_page import TaskParamsPage


class BatchTab(_BatchExecutionMixin, QWidget):
    """批量执行页面

    Args:
        host: MainWindow 实例（提供 run_batch / request_stop / 信号等）
    """

    execution_scope = "batch"

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
        #: (条目列表, [(脚本 ID, 名称)], 总轮数, 是否属性调度)：建表时冻结，
        #: bind_run 据此构造运行状态，不再从表格文本里反推。
        self._progress_build: tuple[
            list[str], list[tuple[str, str]], int, bool] = ([], [], 1, False)
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
        from ..execution.hotkeys import hotkey_label
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

        self._start_feedback = ExecutionFeedback()
        self.execution_feedback = self._start_feedback
        layout.addWidget(self._start_feedback)

        config_row = QHBoxLayout()
        config_row.setContentsMargins(0, 0, 0, 0)
        config_row.setSpacing(8)
        from ..execution.user_selector import execution_context_label
        config_row.addWidget(execution_context_label(tr("当前配置")))
        self._config_combo = AutoWidthComboBox()
        self._config_combo.setMinimumWidth(150)
        self._config_combo.setMinimumHeight(32)
        self._config_combo.currentIndexChanged.connect(self._on_config_changed)
        config_row.addWidget(self._config_combo, stretch=1)
        layout.addLayout(config_row)

        # ── 五页子 Tab ──
        self._sub_tabs = QTabWidget()
        self._sub_tabs.addTab(self._build_script_page(), tr("脚本"))
        self._sub_tabs.addTab(self._build_config_page(), tr("单元"))
        self._sub_tabs.addTab(self._build_progress_page(), tr("进度"))
        self._sub_tabs.addTab(self._build_params_page(), tr("参数"))
        self._sub_tabs.addTab(self._build_task_page(), tr("任务"))
        layout.addWidget(self._sub_tabs)

    def _build_task_page(self) -> QWidget:
        """任务页：按全局/用户快速编辑勾选脚本的任务参数。"""
        self._task_params_page = TaskParamsPage()
        return self._task_params_page

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
        summary_form.addRow(tr_label('执行轮数'), self._rounds_spin)
        # 调度单元与指定排序属于配置组定义（它们决定候选列表的内容和生命周期
        # 契约），在「批量配置」里改；这里只如实显示本次用的是什么。
        self._unit_value = QLabel()
        self._unit_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_form.addRow(tr_label('调度单元'), self._unit_value)
        self._profile_sort_value = QLabel()
        self._profile_sort_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._profile_sort_label = QLabel(tr_label('指定排序'))
        self._summary_form = summary_form
        summary_form.addRow(self._profile_sort_label, self._profile_sort_value)
        self._unattended_check = QCheckBox()
        self._unattended_check.toggled.connect(self._on_unattended_toggled)
        summary_form.addRow(tr_label('无人值守'), self._unattended_check)
        self._allow_portrait_check = QCheckBox()
        self._allow_portrait_check.toggled.connect(
            self._on_allow_portrait_toggled)
        summary_form.addRow(tr_label('允许竖屏'), self._allow_portrait_check)
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
        # 生命周期参数按 wf 文件头实时生成，值写运行草稿；配置窗口只选文件。
        self._lifecycle_panel = LifecycleParamsPanel()
        self._lifecycle_panel.changed.connect(
            self._on_lifecycle_params_changed)
        self._params_save_timer = QTimer(self)
        self._params_save_timer.setSingleShot(True)
        self._params_save_timer.setInterval(400)
        self._params_save_timer.timeout.connect(self._save_draft)
        self._lifecycle_scroll = QScrollArea()
        self._lifecycle_scroll.setWidgetResizable(True)
        self._lifecycle_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._lifecycle_scroll.setWidget(self._lifecycle_panel)
        self._lifecycle_scroll.setVisible(False)
        layout.addWidget(self._lifecycle_scroll, 1)
        return widget

    def _build_progress_page(self) -> QWidget:
        """进度页：执行进度表 + 底部轮次导航"""
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

        # 轮次导航：< 上一轮 | 当前第N轮/共N轮 | 下一轮 > | >> 跳到最新并跟随
        round_row = QHBoxLayout()
        round_row.setContentsMargins(0, 4, 0, 0)
        round_row.setSpacing(6)
        round_row.addStretch(1)
        self._round_prev_btn = self._make_round_button(
            QStyle.StandardPixmap.SP_ArrowLeft, tr("上一轮"))
        self._round_prev_btn.clicked.connect(self._on_prev_round)
        round_row.addWidget(self._round_prev_btn)
        self._round_label = QLabel()
        self._round_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        round_row.addWidget(self._round_label)
        self._round_next_btn = self._make_round_button(
            QStyle.StandardPixmap.SP_ArrowRight, tr("下一轮"))
        self._round_next_btn.clicked.connect(self._on_next_round)
        round_row.addWidget(self._round_next_btn)
        self._round_latest_btn = self._make_round_button(
            QStyle.StandardPixmap.SP_MediaSkipForward,
            tr("跳到最新一轮并持续跟随；按下表示正在跟随"))
        # 「跟随最新」是持续状态，用按下态表达，避免在轮次文本里塞「当前」。
        self._round_latest_btn.setCheckable(True)
        self._round_latest_btn.clicked.connect(self._on_latest_round)
        round_row.addWidget(self._round_latest_btn)
        round_row.addStretch(1)
        layout.addLayout(round_row)
        self._set_round_controls_visible(False)
        return widget

    def _make_round_button(
        self, pixmap: QStyle.StandardPixmap, tooltip: str,
    ) -> QPushButton:
        """轮次导航按钮统一用 Qt 标准图标，不拿文本符号充数。"""
        button = QPushButton()
        style = self.style()
        if style is not None:
            button.setIcon(style.standardIcon(pixmap))
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(32, 28)
        button.setToolTip(tooltip)
        return button

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
        self._btn_script_invert = QPushButton(tr("反选"))
        self._btn_script_all.clicked.connect(
            lambda: self._set_all_scripts_checked(True))
        self._btn_script_invert.clicked.connect(self._invert_script_checks)
        apply_button_style(
            self._btn_script_all, self._btn_script_invert,
            variant="neutral",
        )
        for button in (self._btn_script_all, self._btn_script_invert):
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
        # 只有两列：候选列吃掉面板剩余宽度，顺序列固定四字宽，
        # 合计永远等于视口宽度，因此不该出现横向滚动。
        self._script_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self._script_list.header()
        assert header is not None
        header.setMinimumHeight(32)
        header.setMinimumSectionSize(48)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        self._script_list.setColumnWidth(
            1, _four_cjk_column_width(self._script_list)
        )
        self._script_list.itemChanged.connect(self._on_script_item_changed)
        self._script_list.range_check_requested.connect(
            self._apply_script_range_check)
        self._script_list.order_changed.connect(self._on_script_rows_moved)
        self._script_list.restore_order_requested.connect(
            self._restore_script_order)
        self._script_list.shuffle_order_requested.connect(
            self._shuffle_script_order)
        layout.addWidget(self._script_list, stretch=1)

        self._script_order: list[str] = []
        self._script_candidate_order: list[str] = []
        self._missing_script_ids: list[tuple[int, str]] = []
        # 当前环境不支持、但脚本本身健在的已勾选条目：原位保留勾选，
        # 不按“脚本不可用”报警。
        self._env_filtered_script_ids: list[tuple[int, str]] = []
        self._script_configs_by_id: dict[str, dict] = {}
        self._updating_script_list = False
        return widget

    def _set_initial_column_widths(self) -> None:
        """列表列宽由 Stretch/Fixed 管住，这里只初始化进度表。"""
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

        # 全选/反选行
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
        self._btn_user_invert = QPushButton(tr("反选"))
        self._btn_user_invert.setFixedWidth(60)
        self._btn_user_invert.clicked.connect(self._invert_entry_checks)
        select_row.addWidget(self._btn_user_invert)
        apply_button_style(
            self._btn_user_all, self._btn_user_invert, variant="neutral")
        fit_button_width(
            self._btn_user_all, self._btn_user_invert, minimum=60)
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
        # 与脚本页一致：候选列拉伸占满剩余宽度，顺序列固定四字宽，
        # 两列合计始终等于面板宽度，不产生横向滚动。
        self._user_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self._user_list.header()
        assert header is not None
        header.setMinimumHeight(32)
        header.setMinimumSectionSize(48)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        self._user_list.setColumnWidth(
            1, _four_cjk_column_width(self._user_list))
        self._user_list.itemChanged.connect(self._on_user_item_changed)
        self._user_list.range_check_requested.connect(
            self._apply_user_range_check)
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
        self._refresh_start_feedback()

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
        self._flush_lifecycle_params()
        group_id = str(self._config_combo.itemData(index) or "")
        set_active_group_id(group_id)
        self._refresh_group_contents(group_id)

    def _refresh_group_contents(self, group_id: str = "") -> None:
        self._load_group_state(group_id)
        self._refresh_script_list()
        self._refresh_entry_list()
        self._refresh_params()
        self._refresh_task_params_page()
        self._refresh_start_feedback()

    def _refresh_task_params_page(self) -> None:
        """把当前勾选脚本与用户投影到「任务」页。

        任务参数与日常共用 wf_configs / 用户元数据，这里只决定画哪些脚本、
        可以切到哪些用户，不持有任何独立副本。
        """
        page = getattr(self, "_task_params_page", None)
        if page is None:
            return
        manager = getattr(self._host, "_user_manager", None)
        users_dir = getattr(manager, "_users_dir", None)
        run_env_getter = getattr(self._host, "_selected_run_env", None)
        run_env = run_env_getter() if callable(run_env_getter) else ""
        scripts = [
            self._script_configs_by_id[script_id]
            for script_id in self._script_order
            if script_id in self._script_configs_by_id
        ]
        page.set_context(
            scripts, self._task_param_usernames(),
            users_dir=users_dir, run_env=str(run_env or ""),
        )

    def _task_param_usernames(self) -> list[str]:
        """任务参数可切换的用户：用户模式取勾选用户，属性模式取所选单元的成员。"""
        config = self._item
        if config is None:
            return []
        if config.execution_unit_key == "user":
            return [name for name in self._user_order if name]
        selected = set(self._user_order)
        manager = getattr(self._host, "_user_manager", None)
        if manager is None:
            return list(config.usernames)
        key = config.execution_unit_key
        names: list[str] = []
        for username in config.usernames:
            user = manager.get_user(username)
            value = (str(user.attributes.get(key, "")).strip()
                     if user is not None else "")
            if value in selected:
                names.append(username)
        return names

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
        prepare_wf = (item.workflows.prepare_item if item is not None else "")
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
        # 允许竖屏必然先把客户端收回登录页，所以还要有条目准备把它送进游戏。
        portrait_ready = bool(recover_wf and prepare_wf)
        self._allow_portrait_check.blockSignals(True)
        self._allow_portrait_check.setChecked(
            self._draft.allow_portrait and portrait_ready)
        self._allow_portrait_check.blockSignals(False)
        self._allow_portrait_check.setEnabled(portrait_ready)
        self._allow_portrait_check.setToolTip(tr(
            "安卓目标批量开始时若处于竖屏，只要交换宽高后与布局画布比例一致就"
            "允许启动；启动后先由配置组的「异常恢复 wf」回到登录主页，再由"
            "「条目准备工作流」把人送回游戏。两者的工作流都要在「工具 → 批量配置」里配置"
        ) if portrait_ready else tr(
            "需要先在「工具 → 批量配置」为本配置组配置「异常恢复 wf」与"
            "「条目准备工作流」：允许竖屏启动会先把游戏收回登录主页，"
            "没有条目准备就无法再把人送回游戏"))
        for key, label in self._workflow_labels.items():
            path = getattr(item.workflows, key) if item is not None else ""
            label.setText(path or tr("未配置"))
        if item is None:
            self._lifecycle_scroll.setVisible(False)
        else:
            self._lifecycle_panel.set_workflows(
                item.workflows, self._draft.workflow_params)
            self._lifecycle_scroll.setVisible(
                self._lifecycle_panel.has_content())

    def _on_lifecycle_params_changed(self) -> None:
        """参数属于本次执行：立即进草稿，落盘做短防抖。"""
        if self._item is None:
            return
        self._draft.workflow_params = self._lifecycle_panel.values()
        self._params_save_timer.start()

    def _flush_lifecycle_params(self) -> None:
        """切换配置组或开始前把防抖中的参数落盘。"""
        if self._params_save_timer.isActive():
            self._params_save_timer.stop()
            self._save_draft()

    def _on_rounds_changed(self, rounds: int) -> None:
        self._draft.rounds = int(rounds)
        self._save_draft()

    def _on_unattended_toggled(self, checked: bool) -> None:
        self._draft.unattended = bool(checked)
        self._save_draft()

    def _on_allow_portrait_toggled(self, checked: bool) -> None:
        self._draft.allow_portrait = bool(checked)
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
        """刷新用户页的勾选列表。

        与脚本页同理：重建本次执行顺序后自己重算开始状态，调用方先落盘再刷新
        才不会残留上一次的「至少勾选一个执行单元」提示。
        """
        config = self._item
        self._updating_user_list = True
        self._user_list.clear()
        self._user_list.reset_check_anchor()
        if not config:
            self._user_order = []
            self._user_candidate_order = []
            self._updating_user_list = False
            self._refresh_start_feedback()
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
        self._refresh_start_feedback()

    def _set_all_entries_checked(self, checked: bool):
        """全选行"""
        self._user_list.reset_check_anchor()
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

    def _invert_entry_checks(self) -> None:
        """反选：逐行翻转勾选状态，执行顺序按当前行序重建。"""
        self._user_list.reset_check_anchor()
        self._updating_user_list = True
        try:
            for index in range(self._user_list.topLevelItemCount()):
                item = self._user_list.topLevelItem(index)
                if item is None:
                    continue
                checked = item.checkState(0) == Qt.CheckState.Checked
                item.setCheckState(
                    0, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)
        finally:
            self._updating_user_list = False
        self._sync_user_order_from_rows()

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

    def _apply_user_range_check(self, start: int, end: int, target: int) -> None:
        """Shift 区间勾选：把 [start, end] 闭区间统一设成目标状态，只写一次草稿。"""
        state = Qt.CheckState(target)
        self._updating_user_list = True
        try:
            for index in range(min(start, end), max(start, end) + 1):
                item = self._user_list.topLevelItem(index)
                if item is not None:
                    item.setCheckState(0, state)
        finally:
            self._updating_user_list = False
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
        self._user_list.reset_check_anchor()
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
        from ...core.batch.run import profile_user_order
        sorted_order, missing = profile_user_order(order, key, group.profile_sort_direction)
        self._updating_user_list = True
        try:
            self._apply_tree_order(self._user_list, sorted_order, self._user_name)
        finally:
            self._updating_user_list = False
        self._user_list.reset_check_anchor()
        self._sync_user_order_from_rows()
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
        self._refresh_task_params_page()

    def _get_enabled_usernames(self) -> list[str]:
        """本次实际执行顺序（用户名或属性值）。"""
        return list(self._user_order)

    # ─── 脚本列表 ─────────────────────────────────────────

    def _refresh_script_list(self, checked_ids: list[str] | None = None):
        """刷新脚本勾选列表（数据源与日常下拉一致）

        重建本次执行顺序后必须自己重算开始状态：调用方可能先落盘再刷新
        （「恢复默认顺序」就是），而 _save_draft 里的刷新读到的是重建前的
        顺序，于是「至少勾选一个脚本」的提示和灰掉的开按钮会留在界面上。
        """
        from ...workflows.discovery import (
            list_exposed_scripts,
            script_display_name,
            script_supports_env,
        )

        if checked_ids is None:
            checked_ids = list(self._draft.tasks.checked)

        self._updating_script_list = True
        self._script_list.clear()
        self._script_list.reset_check_anchor()
        try:
            run_env_getter = getattr(self._host, "_selected_run_env", None)
            run_env = run_env_getter() if callable(run_env_getter) else None
            # 环境过滤在本地做，仍然只扫一次发现层。这样能区分两种“看不见”：
            # 脚本被删/取消暴露（真缺失，必须报警），和脚本不支持当前环境
            # （切换视角时正常隐藏，勾选必须原地保留）。
            exposed = [
                cfg for cfg in list_exposed_scripts(None)
                if cfg.get("batchable", False)
            ]
            discovered = [cfg for cfg in exposed
                          if script_supports_env(cfg, run_env)]
        except Exception:
            exposed, discovered = [], []
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
        # 勾选过、但此刻发现不到的脚本分两种。两类都必须原位留在
        # selected_task_ids 里：顺手抹掉的话，脚本一恢复可执行，用户的勾选就
        # 再也回不来了。
        #   a) 真缺失——被删、取消暴露、改成 dedicated、挪进 standalone/…：
        #      本轮不执行，必须明确告诉用户。
        #   b) 只是不支持当前环境——切换执行视角（手机 ↔ 端游）时正常隐藏，
        #      不打扰用户；切回该环境勾选照旧生效。
        self._missing_script_ids = []
        self._env_filtered_script_ids = []
        exposed_ids = {cfg["id"] for cfg in exposed}
        for index, script_id in enumerate(checked_ids):
            if script_id not in visible or script_id in self._script_configs_by_id:
                continue
            if script_id in exposed_ids:
                self._env_filtered_script_ids.append((index, script_id))
            else:
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
        self._refresh_start_feedback()

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
        self._sync_script_order_from_rows()

    def _sync_script_order_from_rows(self) -> None:
        """按列表当前行序与勾选重建候选顺序和实际执行顺序，并落盘草稿。"""
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

    def _apply_script_range_check(self, start: int, end: int, target: int) -> None:
        """Shift 区间勾选：把 [start, end] 闭区间统一设成目标状态，只写一次草稿。"""
        state = Qt.CheckState(target)
        self._updating_script_list = True
        try:
            for index in range(min(start, end), max(start, end) + 1):
                item = self._script_list.topLevelItem(index)
                if item is not None:
                    item.setCheckState(0, state)
        finally:
            self._updating_script_list = False
        self._sync_script_order_from_rows()

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
        self._script_list.reset_check_anchor()
        self._on_script_rows_moved()

    def _set_all_scripts_checked(self, checked: bool) -> None:
        self._script_list.reset_check_anchor()
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

    def _invert_script_checks(self) -> None:
        """反选：逐行翻转勾选状态，执行顺序按当前行序重建。"""
        self._script_list.reset_check_anchor()
        self._updating_script_list = True
        try:
            for index in range(self._script_list.topLevelItemCount()):
                item = self._script_list.topLevelItem(index)
                if item is None:
                    continue
                checked = item.checkState(0) == Qt.CheckState.Checked
                item.setCheckState(
                    0, Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked)
        finally:
            self._updating_script_list = False
        self._sync_script_order_from_rows()

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

    def _unavailable_script_ids(self) -> list[tuple[int, str]]:
        """本轮不参与执行的已勾选脚本：真缺失 + 当前环境不支持，按原位序。"""
        return sorted([
            *getattr(self, "_missing_script_ids", []),
            *getattr(self, "_env_filtered_script_ids", []),
        ])

    def _merged_script_ids(self) -> list[str]:
        """当前执行顺序 + 暂时不参与的 id（按原位插回）。"""
        merged = list(self._script_order)
        for index, script_id in self._unavailable_script_ids():
            merged.insert(min(index, len(merged)), script_id)
        return merged

    def _persist_script_order(self):
        """把列表当前顺序与勾选写回本次草稿。**不碰配置组定义。**"""
        self._draft.tasks.order = list(self._script_candidate_order) + [
            script_id for _index, script_id in self._unavailable_script_ids()
        ]
        self._draft.tasks.checked = self._merged_script_ids()
        self._save_draft()
        self._refresh_task_params_page()

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

