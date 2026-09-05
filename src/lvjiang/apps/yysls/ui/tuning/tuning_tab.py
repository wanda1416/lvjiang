"""燕云「调律」Tab —— 通过 AppHooks 注入通用 MainWindow 的插件页面。

职责：
- 调律配置两页 Tab（规则 | 参数）与 wf_configs 统一存储持久化：
  规则 = 调律规则与玩法；参数 = 部位、全局开关、调律设置与调试参数
- 「开始调律」按钮三态（运行中 / 未就绪 / 就绪），订阅宿主 automation_state_changed
- ``f9_run()``：F9 快捷键与按钮共用的启停入口，
  收集配置并通过宿主 ``run_workflow_implementation`` 启动 auto_tuning
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .....i18n import tr
from .....ui.button_styles import apply_button_style, fit_button_width
from .....ui.execution_user_selector import ExecutionUserSelector
from .....ui.main.run_control import STATE_PLAN_UNSUPPORTED
from ...config.tune_slots import DEFAULT_SLOTS, LOCKED_SLOTS, SLOT_GROUPS
from .config_widget import TuningConfigWidget, TuningGlobalsWidget


def _tuning_switch_names(switches: dict[str, bool]) -> list[str]:
    """开启的开关 key → 注册表显示名（注册表不可用时退回 key）"""
    try:
        from ...core.tuning_rules import get_tune_config
        names = get_tune_config().switches
    except Exception:
        names = {}
    return [names.get(k, k) for k, v in switches.items() if v]


class _OptionalPositiveSpinBox(QSpinBox):
    """以 0 表示未设置，但界面保持空白而不是显示占位文字。"""

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt override
        return "" if value == 0 else super().textFromValue(value)

    def valueFromText(self, text: str | None) -> int:  # noqa: N802 - Qt override
        return 0 if not text or not text.strip() else super().valueFromText(text)


class TuningTab(QWidget):
    """调律 Tab（host 为通用 MainWindow，提供宿主 API 与信号）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._build_ui()
        self._load_tuning_config()
        host.automation_state_changed.connect(self._on_automation_state)
        # 基础配置变更时刷新「参数」页开关（新增/删除开关即时生效）
        from .....core.config.resolver import get_resolver
        get_resolver().add_change_listener(self._on_base_config_changed)

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        # 顶部固定按钮 + 下方配置两页 Tab + 底部状态栏
        tab_layout = QVBoxLayout(self)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(8)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(8)
        self.btn_run_tuning = QPushButton(f"{tr('开始调律')} ({self._host._user_config.hotkeys.start})")
        self.btn_run_tuning.clicked.connect(self.f9_run)
        self.btn_run_tuning.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
        )
        btn_layout.addWidget(self.btn_run_tuning)

        self.btn_pause_resume = QPushButton(tr("暂停"))
        self.btn_pause_resume.setEnabled(False)
        self.btn_pause_resume.setStyleSheet(
            "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
        )
        self.btn_pause_resume.clicked.connect(self._on_pause_resume_clicked)
        btn_layout.addWidget(self.btn_pause_resume)
        tab_layout.addLayout(btn_layout)

        self._execution_user_selector = ExecutionUserSelector(
            self._host.user_manager)
        tab_layout.addWidget(self._execution_user_selector)

        self._config_tabs = QTabWidget()
        self._config_tabs.addTab(self._build_rules_page(), tr("规则"))
        self._config_tabs.addTab(self._build_parameters_page(), tr("参数"))
        tab_layout.addWidget(self._config_tabs)

        # 底部状态栏（显示启动失败原因）
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #d32f2f; padding: 4px; font-size: 12px;")
        self._status_label.setWordWrap(True)
        self._status_label.hide()
        tab_layout.addWidget(self._status_label)

    def _wrap_scroll(self, panel: QWidget) -> QScrollArea:
        """页面统一包可滚动容器（禁水平滚动，最小宽避免内容被裁切）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        vscrollbar = scroll.verticalScrollBar()
        assert vscrollbar is not None
        scrollbar_w = vscrollbar.sizeHint().width()
        scroll.setMinimumWidth(
            panel.minimumSizeHint().width() + scrollbar_w + 8)
        return scroll

    def _build_rules_page(self) -> QWidget:
        """「规则」页：最低等级覆盖 + 基础规则单选 + 流派规则与玩法（变更即持久化）"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        # ── 最低等级（运行时覆盖基础规则的等级门槛）────────
        min_level_row = QHBoxLayout()
        min_level_row.setContentsMargins(0, 0, 0, 0)
        min_level_row.setSpacing(6)
        min_level_row.addWidget(QLabel("<b>" + tr("最低等级：") + "</b>"))
        self._min_level_combo = QComboBox()
        self._min_level_combo.addItem(tr("默认（跟随基础规则）"), None)
        self._refresh_min_level_combo()
        self._min_level_combo.currentIndexChanged.connect(
            lambda _i: self._save_tuning_config())
        self._min_level_combo.setToolTip(tr(
            "运行时覆盖基础规则中的等级门槛；\n"
            "「默认」使用基础规则定义的 min_level，\n"
            "选择具体等级后以该值为准。"))
        min_level_row.addWidget(self._min_level_combo)
        min_level_row.addStretch()
        layout.addLayout(min_level_row)

        layout.addWidget(QLabel("<b>" + tr("基础规则（单选）：") + "</b>"))
        self._base_group_key = ""
        self._button_group: QButtonGroup | None = None
        self._group_container = QWidget()
        self._group_layout = QVBoxLayout(self._group_container)
        self._group_layout.setContentsMargins(0, 0, 0, 0)
        self._group_layout.setSpacing(2)
        self._refresh_base_group_radios()
        layout.addWidget(self._group_container)
        layout.addWidget(QLabel("<b>" + tr("流派规则（可多选）：") + "</b>"))
        self._tuning_config = TuningConfigWidget(show_globals=False)
        self._tuning_config.config_changed.connect(self._save_tuning_config)
        layout.addWidget(self._tuning_config)
        layout.addStretch()
        return self._wrap_scroll(panel)

    def _refresh_min_level_combo(self):
        """刷新最低等级下拉列表（等级配置变更后调用）"""
        current = self._min_level_combo.currentData()
        self._min_level_combo.blockSignals(True)
        self._min_level_combo.clear()
        self._min_level_combo.addItem(tr("默认（跟随基础规则）"), None)
        from ...config import get_game_config
        configs = get_game_config().get_level_configs()
        levels = sorted([c.level for c in configs], reverse=True)
        for lv in levels:
            self._min_level_combo.addItem(str(lv), lv)
        # 恢复之前的选中值
        if current is not None:
            idx = self._min_level_combo.findData(current)
            if idx >= 0:
                self._min_level_combo.setCurrentIndex(idx)
        self._min_level_combo.blockSignals(False)

    def _refresh_base_group_radios(self):
        """重建基础规则单选组（遍历全部规则组，选中项保持不变）"""
        from ...core.tuning_rules import get_tuning_group_manager
        groups = get_tuning_group_manager().get_groups()
        # 当前 key 不在时取第一个可用组
        if self._base_group_key not in groups:
            self._base_group_key = next(iter(groups), "")
        while self._group_layout.count():
            item = self._group_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._group_radios: dict[str, QRadioButton] = {}
        self._button_group = QButtonGroup(self)
        for key, group in groups.items():
            rb = QRadioButton(group.name)
            rb.setToolTip(tr("F6 调律配置可新增/编辑规则组"))
            rb.setChecked(key == self._base_group_key)
            rb.toggled.connect(
                lambda checked, k=key: self._on_base_group_toggled(k, checked))
            self._group_layout.addWidget(rb)
            self._group_radios[key] = rb
            self._button_group.addButton(rb)

    def _on_base_group_toggled(self, key: str, checked: bool):
        """基础规则单选变更即落盘（session，启动时注入工作流）"""
        if not checked:
            return
        self._base_group_key = key
        self._save_tuning_config()

    def _select_base_group_radio(self, key: str):
        """按 key 选中单选按钮（不存在时取第一个）"""
        rb = self._group_radios.get(key)
        if rb is None:
            rb = next(iter(self._group_radios.values()), None)
        if rb is None:
            return
        actual_key = next(
            (k for k, v in self._group_radios.items() if v is rb), "")
        self._base_group_key = actual_key
        rb.blockSignals(True)
        rb.setChecked(True)
        rb.blockSignals(False)

    def _build_parameters_page(self) -> QWidget:
        """「参数」页：部位、全局开关、调律设置与调试参数。"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        slots_header = QHBoxLayout()
        slots_header.addWidget(QLabel("<b>" + tr("调律部位：") + "</b>"))
        slots_header.addStretch()
        btn_select_all = QPushButton(tr("全选"))
        btn_select_all.clicked.connect(lambda: self._set_all_tuning_checks(True))
        slots_header.addWidget(btn_select_all)
        btn_deselect_all = QPushButton(tr("取消全选"))
        btn_deselect_all.clicked.connect(lambda: self._set_all_tuning_checks(False))
        slots_header.addWidget(btn_deselect_all)
        apply_button_style(btn_select_all, btn_deselect_all, variant="neutral")
        fit_button_width(btn_select_all, btn_deselect_all, minimum=70)
        layout.addLayout(slots_header)
        self._tuning_checkboxes: list[QCheckBox] = []

        slots_row = QHBoxLayout()
        for group_name, slots in SLOT_GROUPS:
            grp = QGroupBox(group_name)
            grp_layout = QVBoxLayout(grp)
            for slot_key, slot_label in slots:
                cb = QCheckBox(slot_label)
                cb.setObjectName(slot_key)
                cb.setChecked(True)
                if slot_key in LOCKED_SLOTS:
                    # 主武器槽已展示全部武器，副武器无需遍历，强制禁用
                    cb.setChecked(False)
                    cb.setEnabled(False)
                # 初始状态设置完成后再连接，避免构造期半成品状态误落盘
                cb.stateChanged.connect(self._save_tuning_config)
                grp_layout.addWidget(cb)
                self._tuning_checkboxes.append(cb)
            slots_row.addWidget(grp)
        layout.addLayout(slots_row)

        layout.addWidget(QLabel("<b>" + tr("全局开关：") + "</b>"))
        self._tuning_globals = TuningGlobalsWidget(show_skip_tuning=False)
        self._tuning_globals.config_changed.connect(self._save_tuning_config)
        layout.addWidget(self._tuning_globals)

        layout.addWidget(QLabel("<b>" + tr("调律设置：") + "</b>"))
        self._pc_background_scroll_cb = QCheckBox(
            tr("PC 端兼容后台模式"))
        self._pc_background_scroll_cb.setToolTip(tr(
            "仅在 PC 端生效；关闭时继续使用更精准的拖拽滚动。"))
        self._pc_background_scroll_cb.stateChanged.connect(
            lambda _state: self._save_tuning_config())
        layout.addWidget(self._pc_background_scroll_cb)
        self._use_stone_cache_cb = QCheckBox(tr("使用律准石缓存"))
        self._use_stone_cache_cb.setToolTip(tr(
            "首次识别后按调律、重置和回收结果记账；"
            "关闭后每个检查点重新识别。"))
        self._use_stone_cache_cb.stateChanged.connect(
            lambda _state: self._save_tuning_config())
        layout.addWidget(self._use_stone_cache_cb)
        initial_stone_row = QHBoxLayout()
        self._initial_stone_check_cb = QCheckBox(
            tr("初始检查大律准石数量大于"))
        self._initial_stone_check_cb.setToolTip(tr(
            "启用后，首次识别数量低于设定值时要求人工确认。"))
        initial_stone_row.addWidget(self._initial_stone_check_cb)
        self._initial_stone_min = _OptionalPositiveSpinBox()
        self._initial_stone_min.setRange(0, 99999)
        self._initial_stone_min.setEnabled(False)
        self._initial_stone_min.setToolTip(tr(
            "首次识别时使用的额外大律准石数量门槛。"))
        self._initial_stone_check_cb.toggled.connect(
            self._on_initial_stone_check_toggled)
        self._initial_stone_min.valueChanged.connect(
            lambda _value: self._save_tuning_config())
        initial_stone_row.addWidget(self._initial_stone_min)
        initial_stone_row.addStretch()
        layout.addLayout(initial_stone_row)
        self._positional_traversal_cb = QCheckBox(
            tr("启用位置校验遍历策略"))
        self._positional_traversal_cb.setToolTip(tr(
            "默认使用去重遍历（dedup）；启用后改用位置校验遍历（positional）。"))
        self._positional_traversal_cb.stateChanged.connect(
            lambda _state: self._save_tuning_config())
        layout.addWidget(self._positional_traversal_cb)

        layout.addWidget(QLabel("<b>" + tr("调试参数：") + "</b>"))
        self._skip_tuning_cb = QCheckBox(
            tr("跳过实际调律（仅进出调律页，测试滚动用）"))
        self._skip_tuning_cb.stateChanged.connect(
            lambda _state: self._save_tuning_config())
        layout.addWidget(self._skip_tuning_cb)
        self._validate_stone_cache_cb = QCheckBox(tr("运行时校验缓存"))
        self._validate_stone_cache_cb.setToolTip(tr(
            "启用后，每实际进入 5 件装备调律时重新读取律准石；"
            "有效读数与缓存相差超过 1 个大律准石时记录错误日志。"))
        self._validate_stone_cache_cb.stateChanged.connect(
            lambda _state: self._save_tuning_config())
        layout.addWidget(self._validate_stone_cache_cb)

        # ── 初始跳过 / 指定调律（互斥）────────────────────
        skip_group = QHBoxLayout()
        self._cb_skip = QCheckBox(tr("初始跳过"))
        self._sp_skip_row = QSpinBox()
        self._sp_skip_row.setRange(1, 99)
        self._sp_skip_row.setPrefix(tr("行 "))
        self._sp_skip_col = QSpinBox()
        self._sp_skip_col.setRange(1, 6)
        self._sp_skip_col.setPrefix(tr("列 "))
        skip_group.addWidget(self._cb_skip)
        skip_group.addWidget(self._sp_skip_row)
        skip_group.addWidget(self._sp_skip_col)
        skip_group.addStretch()
        layout.addLayout(skip_group)

        target_group = QHBoxLayout()
        self._cb_target = QCheckBox(tr("指定调律"))
        self._sp_target_row = QSpinBox()
        self._sp_target_row.setRange(1, 99)
        self._sp_target_row.setPrefix(tr("行 "))
        self._sp_target_col = QSpinBox()
        self._sp_target_col.setRange(1, 6)
        self._sp_target_col.setPrefix(tr("列 "))
        target_group.addWidget(self._cb_target)
        target_group.addWidget(self._sp_target_row)
        target_group.addWidget(self._sp_target_col)
        target_group.addStretch()
        layout.addLayout(target_group)

        # 互斥联动
        self._cb_skip.toggled.connect(self._on_skip_target_toggled)
        self._cb_target.toggled.connect(self._on_skip_target_toggled)
        # 变更即持久化
        self._cb_skip.toggled.connect(lambda: self._save_tuning_config())
        self._cb_target.toggled.connect(lambda: self._save_tuning_config())
        for sp in (self._sp_skip_row, self._sp_skip_col,
                   self._sp_target_row, self._sp_target_col):
            sp.valueChanged.connect(lambda: self._save_tuning_config())
        # 初始状态：SpinBox 随 checkbox 勾选才可用
        self._sp_skip_row.setEnabled(False)
        self._sp_skip_col.setEnabled(False)
        self._sp_target_row.setEnabled(False)
        self._sp_target_col.setEnabled(False)

        layout.addStretch()
        return self._wrap_scroll(panel)

    # ─── 按钮状态（订阅宿主 automation_state_changed）──────────

    def _on_automation_state(self, state: str):
        hk = self._host._user_config.hotkeys
        if state in ("running", "paused"):
            self.btn_run_tuning.setText(f"{tr('结束')} ({hk.stop})")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        elif state == "not_ready":
            self.btn_run_tuning.setText(tr("未就绪"))
            self.btn_run_tuning.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        elif state == STATE_PLAN_UNSUPPORTED:
            # 不能落进下面的 else：那里除了变绿还会 mark_done()，会误报完成。
            self.btn_run_tuning.setText(tr("方案不支持"))
            self.btn_run_tuning.setStyleSheet(
                "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        else:
            self.btn_run_tuning.setText(f"{tr('开始调律')} ({hk.start})")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
            # 工作流结束：通知调律进度 Tab 标记完成
            engine = getattr(self._host, '_current_engine', None)
            if engine is not None and hasattr(engine, '_progress_hub'):
                widget = self._find_progress_widget()
                if widget is not None:
                    widget.mark_done()
        # 刷新暂停/恢复按钮
        if state == "running":
            self.btn_pause_resume.setText(f"{tr('暂停')} ({hk.pause})")
            self.btn_pause_resume.setEnabled(True)
            self.btn_pause_resume.setStyleSheet(
                "background-color: #FF9800; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        elif state == "paused":
            self.btn_pause_resume.setText(f"{tr('恢复')} ({hk.pause})")
            self.btn_pause_resume.setEnabled(True)
            self.btn_pause_resume.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        else:
            self.btn_pause_resume.setText(tr("暂停"))
            self.btn_pause_resume.setEnabled(False)
            self.btn_pause_resume.setStyleSheet(
                "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        # 进度面板的暂停提示：独立于按钮，避免只看右侧面板时误以为卡死
        widget = self._find_progress_widget()
        if widget is not None:
            widget.set_paused(state == "paused")

    def _on_pause_resume_clicked(self):
        """暂停/恢复按钮点击 → 转发给宿主"""
        self._host.request_pause_resume()

    # ─── 配置变更监听 ────────────────────────────────────────

    def _on_base_config_changed(self, rel_path: str):
        """配置变更监听回调：tune_config/规则组变化时刷新对应控件"""
        if rel_path == "yysls/tune_config.yaml":
            self._tuning_globals.refresh_switches()
        elif rel_path.startswith("yysls/base_groups/"):
            self._refresh_base_group_radios()

    # ─── 启停入口（F9 快捷键 / 按钮点击共用）───────────────────

    @staticmethod
    def _missing_tuning_output_fields() -> list[str]:
        """当前图库空间缺失的必需输出字段 key（调律启动预检）"""
        from lvjiang.core.reference_db import ReferenceDatabase

        from ...core.recognizer.reference_adapter import get_missing_output_fields
        db = ReferenceDatabase()
        db.load()
        return get_missing_output_fields(db)

    def f9_run(self):
        """运行中 → 停止；否则收集配置启动自动调律"""
        if self._host.is_running:
            self._host.request_stop()
            return
        self._start_tuning()

    def _show_status_error(self, message: str):
        """在底部状态栏显示错误信息"""
        self._status_label.setText(message)
        self._status_label.show()

    def _clear_status(self):
        """清除底部状态栏"""
        self._status_label.hide()
        self._status_label.setText("")

    def _start_tuning(self):
        """从统一存储读取调律配置，校验后启动 auto_tuning 工作流"""
        host = self._host
        self._clear_status()

        execution_username = self._execution_user_selector.resolve_username()
        if not execution_username:
            msg = tr("请选择有效的执行用户")
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        # 图库空间预检（UI 即时反馈，与工作流 run() 预检同契约）
        if self._missing_tuning_output_fields():
            msg = tr("当前图库空间缺少 levels/counts 输出字段，无法启动自动调律")
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        # ── 从统一存储读取配置 ──
        from .....core.config.wf_configs import get_wf_config
        tc = get_wf_config("auto_tuning")

        selected_slots = tc.get("selected_slots") or []
        if not selected_slots:
            msg = tr("请至少选择一个调律部位")
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        # 获取调律规则配置（按规则分组的层级 dict）并创建判定器
        from ...core.evaluator import (
            get_rule_names,
            get_tuning_judge,
            get_tuning_rules,
            is_rule_implemented,
        )
        rules_cfg = tc.get("rules", {})
        enabled = {k: cfg for k, cfg in rules_cfg.items() if cfg.get("enabled")}
        if not enabled:
            msg = tr("请至少选择一个调律规则")
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return
        rule_judges = []
        rule_map = get_tuning_rules()
        switches = {str(k): bool(v) for k, v in tc.get("switches", {}).items()}
        skip_tuning = bool(tc.get("skip_tuning", False))
        pc_background_scroll = bool(tc.get("pc_background_scroll", False))
        use_stone_cache = bool(tc.get("use_stone_cache", True))
        initial_stone_min_count = tc.get("initial_stone_min_count")
        initial_stone_check_enabled = bool(tc.get(
            "initial_stone_check_enabled",
            initial_stone_min_count is not None,
        ))
        if initial_stone_min_count is not None:
            initial_stone_min_count = int(initial_stone_min_count)
        elif initial_stone_check_enabled:
            initial_stone_min_count = 80
        if not initial_stone_check_enabled:
            initial_stone_min_count = None
        validate_stone_cache = bool(tc.get("validate_stone_cache", False))
        scroll_strategy = (
            "positional"
            if tc.get("scroll_strategy") == "positional"
            else ""
        )
        for rule_key, cfg in enabled.items():
            if not is_rule_implemented(rule_key):
                host.append_log(f"[警告] 规则「{get_rule_names().get(rule_key, rule_key)}」判定暂未实现，已跳过")
                continue
            rule = rule_map[rule_key]
            wr_cfg = cfg.get("playstyles")
            if rule.playstyles and wr_cfg is not None and not wr_cfg:
                msg = f"{tr('规则「{name}」需至少勾选一个玩法')}".format(name=rule.name)
                host.append_log(f"[错误] {msg}")
                self._show_status_error(msg)
                return
            rule_judges.append(
                get_tuning_judge(rule_key, {**cfg, "switches": switches}))
        if not rule_judges:
            msg = tr("选中的规则均未实现判定逻辑")
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        flow_name = tr("自动调律")

        # 基础规则组（启动时快照注入）
        from ...core.tuning_rules import get_tuning_group
        group_key = tc.get("base_group", "")
        base_group = get_tuning_group(group_key) if group_key else None
        if base_group is None:
            msg = tr("基础规则组 '{key}' 不存在，拒绝启动").format(key=group_key)
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        # 运行时瞬态字段（启动时从 UI 即时读取，保存时也会持久化）
        skip_start = None
        if self._cb_skip.isChecked() and self._cb_skip.isEnabled():
            skip_start = (self._sp_skip_row.value(), self._sp_skip_col.value())
        target_cell = None
        if self._cb_target.isChecked() and self._cb_target.isEnabled():
            target_cell = (self._sp_target_row.value(), self._sp_target_col.value())
        # 最低等级覆盖（None=跟随基础规则）
        min_level_override = self._min_level_combo.currentData()
        if min_level_override is not None:
            min_level_override = int(min_level_override)

        def configure(wf_instance, engine):
            from ...workflows.tuning_context import TuningRunContext
            wf_instance.run_ctx = TuningRunContext(
                selected_slots=selected_slots,
                rule_judges=rule_judges,
                judge_configs={
                    k: {**cfg, "switches": switches} for k, cfg in enabled.items()},
                judge_rule_keys=list(enabled),
                base_group=base_group,
                skip_tuning=skip_tuning,
                pc_background_scroll=pc_background_scroll,
                use_stone_cache=use_stone_cache,
                initial_stone_check_enabled=initial_stone_check_enabled,
                initial_stone_min_count=initial_stone_min_count,
                validate_stone_cache=validate_stone_cache,
                scroll_strategy=scroll_strategy,
                skip_start=skip_start,
                target_cell=target_cell,
                min_level=min_level_override,
            )
            # 创建调律进度信号桥（右侧进度 Tab 由 _find_progress_widget 查找并连接）
            if engine is not None:
                from .progress_hub import TuningProgressHub
                engine._progress_hub = TuningProgressHub()
                # 连接右侧调律管理中的进度页
                widget = self._find_progress_widget()
                if widget is not None:
                    widget.reconnect(engine._progress_hub)
                    widget.reset_state()
                else:
                    logger.warning("未找到调律进度控件，进度信号不会显示")

            rule_names_text = "、".join(j.rule_name for j in rule_judges)
            on_names = _tuning_switch_names(switches)
            if on_names:
                rule_names_text += f"（开关：{'、'.join(on_names)}）"
            if skip_tuning:
                host.append_log(tr("[提示] 已开启「跳过实际调律」：仅模拟进出调律页，不执行调律"))
            effective_min_level = (
                min_level_override if min_level_override is not None
                else base_group.scan.min_level)
            host.append_log(
                f"[开始] {flow_name} 流程，基础规则: {base_group.name}，"
                f"规则: {rule_names_text}，部位: {selected_slots}，"
                f"最低等级: {effective_min_level}，"
                f"执行用户: {execution_username}"
                + ("" if min_level_override is None else "（UI 覆盖）"))

        host.run_workflow_implementation(
            "auto_tuning", flow_name, configure,
            execution_username=execution_username)

    # ─── 进度控件查找 ────────────────────────────────

    def _find_progress_widget(self):
        """查找可连接进度桥的调律管理容器（兼容旧进度控件）。"""
        from .management_widget import TuningManagementWidget
        from .progress_widget import TuningProgressWidget
        tabs = getattr(self._host, 'tabs', None)
        if tabs is None:
            return None
        for i in range(tabs.count()):
            w = tabs.widget(i)
            if isinstance(w, TuningManagementWidget):
                return w
            if isinstance(w, TuningProgressWidget):
                return w
        return None

    # ─── 调律配置持久化（wf_configs["auto_tuning"]）──────────

    def _load_tuning_config(self):
        from .....core.config.wf_configs import get_wf_config
        tc = get_wf_config("auto_tuning")
        selected = tc.get("selected_slots") or list(DEFAULT_SLOTS)
        rules_cfg = tc.get("rules") or {"huiyi_general": {"enabled": True}}
        for cb in self._tuning_checkboxes:
            cb.blockSignals(True)
            # 禁用项（副武器）不随会话配置回选
            cb.setChecked(cb.isEnabled() and cb.objectName() in selected)
            cb.blockSignals(False)
        self._tuning_config.set_config(rules_cfg)
        # 基础规则单选（无持久值时选第一个可用组）
        self._base_group_key = tc.get("base_group", "")
        self._select_base_group_radio(self._base_group_key)
        self._tuning_globals.set_switches(tc.get("switches", {}))
        self._skip_tuning_cb.blockSignals(True)
        self._skip_tuning_cb.setChecked(bool(tc.get("skip_tuning", False)))
        self._skip_tuning_cb.blockSignals(False)
        self._pc_background_scroll_cb.blockSignals(True)
        self._pc_background_scroll_cb.setChecked(
            bool(tc.get("pc_background_scroll", False)))
        self._pc_background_scroll_cb.blockSignals(False)
        self._use_stone_cache_cb.blockSignals(True)
        self._use_stone_cache_cb.setChecked(
            bool(tc.get("use_stone_cache", True)))
        self._use_stone_cache_cb.blockSignals(False)
        initial_stone_min = tc.get("initial_stone_min_count")
        initial_stone_enabled = bool(tc.get(
            "initial_stone_check_enabled", initial_stone_min is not None))
        self._initial_stone_check_cb.blockSignals(True)
        self._initial_stone_check_cb.setChecked(initial_stone_enabled)
        self._initial_stone_check_cb.blockSignals(False)
        self._initial_stone_min.blockSignals(True)
        self._initial_stone_min.setValue(
            int(initial_stone_min) if initial_stone_min is not None
            else (80 if initial_stone_enabled else 0))
        self._initial_stone_min.blockSignals(False)
        self._initial_stone_min.setEnabled(initial_stone_enabled)
        self._validate_stone_cache_cb.blockSignals(True)
        self._validate_stone_cache_cb.setChecked(
            bool(tc.get("validate_stone_cache", False)))
        self._validate_stone_cache_cb.blockSignals(False)
        self._positional_traversal_cb.blockSignals(True)
        self._positional_traversal_cb.setChecked(
            tc.get("scroll_strategy") == "positional")
        self._positional_traversal_cb.blockSignals(False)
        # 最低等级（运行时覆盖基础规则的等级门槛）
        saved_min_level = tc.get("min_level")
        self._min_level_combo.blockSignals(True)
        if saved_min_level is not None:
            idx = self._min_level_combo.findData(int(saved_min_level))
            if idx >= 0:
                self._min_level_combo.setCurrentIndex(idx)
            else:
                self._min_level_combo.setCurrentIndex(0)  # 回退到「默认」
        else:
            self._min_level_combo.setCurrentIndex(0)  # 「默认」
        self._min_level_combo.blockSignals(False)
        # 初始跳过 / 指定调律
        for key, cb, sp_row, sp_col in (
            ("skip_start", self._cb_skip, self._sp_skip_row, self._sp_skip_col),
            ("target_cell", self._cb_target, self._sp_target_row, self._sp_target_col),
        ):
            val = tc.get(key)
            cb.blockSignals(True)
            sp_row.blockSignals(True)
            sp_col.blockSignals(True)
            if isinstance(val, (list, tuple)) and len(val) == 2:
                cb.setChecked(True)
                sp_row.setValue(int(val[0]))
                sp_col.setValue(int(val[1]))
            else:
                cb.setChecked(False)
            cb.blockSignals(False)
            sp_row.blockSignals(False)
            sp_col.blockSignals(False)
        self._on_skip_target_toggled()

    def _save_tuning_config(self):
        from .....core.config.wf_configs import update_wf_config
        skip_start = None
        if self._cb_skip.isChecked():
            skip_start = [self._sp_skip_row.value(), self._sp_skip_col.value()]
        target_cell = None
        if self._cb_target.isChecked():
            target_cell = [self._sp_target_row.value(), self._sp_target_col.value()]
        update_wf_config("auto_tuning", {
            "selected_slots": self._get_tuning_selected_slots(),
            "rules": self._get_tuning_rule_config(),
            "switches": self._get_tuning_switches(),
            "base_group": self._base_group_key,
            "skip_tuning": self._get_tuning_skip_tuning(),
            "pc_background_scroll": self._pc_background_scroll_cb.isChecked(),
            "use_stone_cache": self._use_stone_cache_cb.isChecked(),
            "initial_stone_check_enabled": (
                self._initial_stone_check_cb.isChecked()),
            "initial_stone_min_count": (
                self._initial_stone_min.value()
                if (self._initial_stone_check_cb.isChecked()
                    and self._initial_stone_min.value() > 0) else None),
            "validate_stone_cache": self._validate_stone_cache_cb.isChecked(),
            "scroll_strategy": (
                "positional" if self._positional_traversal_cb.isChecked() else ""),
            "skip_start": skip_start,
            "target_cell": target_cell,
            "min_level": self._min_level_combo.currentData(),
        })

    def _on_initial_stone_check_toggled(self, checked: bool) -> None:
        self._initial_stone_min.setEnabled(checked)
        if checked and self._initial_stone_min.value() == 0:
            self._initial_stone_min.blockSignals(True)
            self._initial_stone_min.setValue(80)
            self._initial_stone_min.blockSignals(False)
        self._save_tuning_config()

    def _set_all_tuning_checks(self, checked: bool):
        for cb in self._tuning_checkboxes:
            if cb.isEnabled():
                cb.setChecked(checked)
        self._save_tuning_config()

    def _on_skip_target_toggled(self):
        """初始跳过 / 指定调律互斥联动：勾一个另一个置灰"""
        skip_on = self._cb_skip.isChecked()
        target_on = self._cb_target.isChecked()
        self._cb_target.setEnabled(not skip_on)
        self._sp_target_row.setEnabled(not skip_on and target_on)
        self._sp_target_col.setEnabled(not skip_on and target_on)
        self._cb_skip.setEnabled(not target_on)
        self._sp_skip_row.setEnabled(skip_on and not target_on)
        self._sp_skip_col.setEnabled(skip_on and not target_on)

    def _get_tuning_selected_slots(self) -> list[str]:
        return [cb.objectName() for cb in self._tuning_checkboxes if cb.isChecked()]

    def _get_tuning_rule_config(self) -> dict[str, dict]:
        """流派规则配置委托公共控件收集"""
        return self._tuning_config.get_config()

    def _get_tuning_switches(self) -> dict[str, bool]:
        """全局开关状态（「参数」页动态复选框）"""
        return self._tuning_globals.get_switches()

    def _get_tuning_skip_tuning(self) -> bool:
        """「跳过实际调律」调试开关。"""
        return self._skip_tuning_cb.isChecked()
