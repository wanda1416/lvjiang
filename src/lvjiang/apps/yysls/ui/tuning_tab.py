"""燕云「调律」Tab —— 通过 AppHooks 注入通用 MainWindow 的插件页面。

职责：
- 调律配置三页 Tab（规则 | 部位 | 更多）与 wf_configs 统一存储持久化：
  规则 = 调律规则与玩法；部位 = 调律部位选择；
  更多 = 跳过实际调律 mock + 全部注册开关
- 「开始调律」按钮三态（运行中 / 未就绪 / 就绪），订阅宿主 automation_state_changed
- ``f9_run()``：F9 快捷键与按钮共用的启停入口，
  收集配置并通过宿主 ``run_workflow_implementation`` 启动 auto_tuning
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
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

from ..tune_slots import DEFAULT_SLOTS, LOCKED_SLOTS, SLOT_GROUPS
from .tune_config_widget import TuningConfigWidget, TuningGlobalsWidget


def _tuning_switch_names(switches: dict[str, bool]) -> list[str]:
    """开启的开关 key → 注册表显示名（注册表不可用时退回 key）"""
    try:
        from ..evaluator.tuning_rules import get_tune_config
        names = get_tune_config().switches
    except Exception:
        names = {}
    return [names.get(k, k) for k, v in switches.items() if v]


class TuningTab(QWidget):
    """调律 Tab（host 为通用 MainWindow，提供宿主 API 与信号）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._tuning_progress_dialog = None  # 进度对话框（懒创建，复用）
        self._build_ui()
        self._load_tuning_config()
        host.automation_state_changed.connect(self._on_automation_state)
        # 基础配置变更时刷新「更多」页开关（新增/删除开关即时生效）
        from ....core.config.resolver import get_resolver
        get_resolver().add_change_listener(self._on_base_config_changed)

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        # 顶部固定「开始调律」按钮（第一行）+ 下方配置三页 Tab（规则 | 部位 | 更多）+ 底部状态栏
        tab_layout = QVBoxLayout(self)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(8)

        self.btn_run_tuning = QPushButton("开始调律 (F9)")
        self.btn_run_tuning.clicked.connect(self.f9_run)
        self.btn_run_tuning.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
        )
        tab_layout.addWidget(self.btn_run_tuning)

        # 进度对话框控制行：复选框 + 按钮
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        self._cb_auto_progress = QCheckBox("自动打开调律进度")
        self._cb_auto_progress.toggled.connect(lambda: self._save_tuning_config())
        progress_row.addWidget(self._cb_auto_progress)
        progress_row.addStretch()
        self._btn_toggle_progress = QPushButton("打开进度")
        self._btn_toggle_progress.setFixedWidth(80)
        self._btn_toggle_progress.clicked.connect(self._toggle_progress_dialog)
        progress_row.addWidget(self._btn_toggle_progress)
        tab_layout.addLayout(progress_row)

        config_tabs = QTabWidget()
        config_tabs.addTab(self._build_rules_page(), "规则")
        config_tabs.addTab(self._build_slots_page(), "部位")
        config_tabs.addTab(self._build_more_page(), "更多")
        tab_layout.addWidget(config_tabs)

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
        """「规则」页：基础规则单选 + 流派规则与玩法（变更即持久化）"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>基础规则（单选）：</b>"))
        self._base_group_key = ""
        self._button_group: QButtonGroup | None = None
        self._group_container = QWidget()
        self._group_layout = QVBoxLayout(self._group_container)
        self._group_layout.setContentsMargins(0, 0, 0, 0)
        self._group_layout.setSpacing(2)
        self._refresh_base_group_radios()
        layout.addWidget(self._group_container)
        layout.addWidget(QLabel("<b>流派规则（可多选）：</b>"))
        self._tuning_config = TuningConfigWidget(show_globals=False)
        self._tuning_config.config_changed.connect(self._save_tuning_config)
        layout.addWidget(self._tuning_config)
        layout.addStretch()
        return self._wrap_scroll(panel)

    def _refresh_base_group_radios(self):
        """重建基础规则单选组（遍历全部规则组，选中项保持不变）"""
        from ..evaluator.tuning_rules import get_tuning_group_manager
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
            rb.setToolTip("F6 调律配置可新增/编辑规则组")
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

    def _build_slots_page(self) -> QWidget:
        """「部位」页：调律部位选择（标题行内嵌全选/取消全选）"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        slots_header = QHBoxLayout()
        slots_header.addWidget(QLabel("<b>选择调律部位：</b>"))
        slots_header.addStretch()
        btn_select_all = QPushButton("全选")
        btn_select_all.clicked.connect(lambda: self._set_all_tuning_checks(True))
        btn_select_all.setFixedWidth(70)
        slots_header.addWidget(btn_select_all)
        btn_deselect_all = QPushButton("取消全选")
        btn_deselect_all.clicked.connect(lambda: self._set_all_tuning_checks(False))
        btn_deselect_all.setFixedWidth(70)
        slots_header.addWidget(btn_deselect_all)
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

        # ── 初始跳过 / 指定调律（互斥）────────────────────
        skip_group = QHBoxLayout()
        self._cb_skip = QCheckBox("初始跳过")
        self._sp_skip_row = QSpinBox()
        self._sp_skip_row.setRange(1, 99)
        self._sp_skip_row.setPrefix("行 ")
        self._sp_skip_col = QSpinBox()
        self._sp_skip_col.setRange(1, 6)
        self._sp_skip_col.setPrefix("列 ")
        skip_group.addWidget(self._cb_skip)
        skip_group.addWidget(self._sp_skip_row)
        skip_group.addWidget(self._sp_skip_col)
        skip_group.addStretch()
        layout.addLayout(skip_group)

        target_group = QHBoxLayout()
        self._cb_target = QCheckBox("指定调律")
        self._sp_target_row = QSpinBox()
        self._sp_target_row.setRange(1, 99)
        self._sp_target_row.setPrefix("行 ")
        self._sp_target_col = QSpinBox()
        self._sp_target_col.setRange(1, 6)
        self._sp_target_col.setPrefix("列 ")
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

    def _build_more_page(self) -> QWidget:
        """「更多」页：跳过实际调律 mock + 全部注册开关"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>全局开关与测试选项：</b>"))
        self._tuning_globals = TuningGlobalsWidget()
        self._tuning_globals.config_changed.connect(self._save_tuning_config)
        layout.addWidget(self._tuning_globals)
        layout.addStretch()
        return self._wrap_scroll(panel)

    # ─── 按钮状态（订阅宿主 automation_state_changed）──────────

    def _on_automation_state(self, state: str):
        if state == "running":
            self.btn_run_tuning.setText("停止 (F10)")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
            )
        elif state == "not_ready":
            self.btn_run_tuning.setText("未就绪")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
            )
        else:
            self.btn_run_tuning.setText("开始调律 (F9)")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
            )
            # 工作流结束：仅调律工作流（有进度对话框或 hub）时通知标记完成
            engine = getattr(self._host, '_current_engine', None)
            if engine is not None and hasattr(engine, '_progress_hub'):
                dlg = self._tuning_progress_dialog
                if dlg is not None:
                    dlg.mark_done()
                # 同步按钮文本（用户可能通过 X 按钮关闭了对话框）
                if dlg is not None and not dlg.is_visible():
                    self._btn_toggle_progress.setText("打开进度")

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

        from ..core.material_recognizer import get_missing_output_fields
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

        # 图库空间预检（UI 即时反馈，与工作流 run() 预检同契约）
        if self._missing_tuning_output_fields():
            msg = "当前图库空间缺少 levels/counts 输出字段，无法启动自动调律"
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        # ── 从统一存储读取配置 ──
        from ....core.config.wf_configs import get_wf_config
        tc = get_wf_config("auto_tuning")

        selected_slots = tc.get("selected_slots") or []
        if not selected_slots:
            msg = "请至少选择一个调律部位"
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        # 获取调律规则配置（按规则分组的层级 dict）并创建判定器
        from ..evaluator import (
            get_rule_names,
            get_tuning_judge,
            get_tuning_rules,
            is_rule_implemented,
        )
        rules_cfg = tc.get("rules", {})
        enabled = {k: cfg for k, cfg in rules_cfg.items() if cfg.get("enabled")}
        if not enabled:
            msg = "请至少选择一个调律规则"
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return
        rule_judges = []
        rule_map = get_tuning_rules()
        switches = {str(k): bool(v) for k, v in tc.get("switches", {}).items()}
        skip_tuning = bool(tc.get("skip_tuning", False))
        for rule_key, cfg in enabled.items():
            if not is_rule_implemented(rule_key):
                host.append_log(f"[警告] 规则「{get_rule_names().get(rule_key, rule_key)}」判定暂未实现，已跳过")
                continue
            rule = rule_map[rule_key]
            wr_cfg = cfg.get("playstyles")
            if rule.playstyles and wr_cfg is not None and not wr_cfg:
                msg = f"规则「{rule.name}」需至少勾选一个玩法"
                host.append_log(f"[错误] {msg}")
                self._show_status_error(msg)
                return
            rule_judges.append(
                get_tuning_judge(rule_key, {**cfg, "switches": switches}))
        if not rule_judges:
            msg = "选中的规则均未实现判定逻辑"
            host.append_log(f"[错误] {msg}")
            self._show_status_error(msg)
            return

        flow_name = "自动调律"

        # 基础规则组（启动时快照注入）
        from ..evaluator.tuning_rules import get_tuning_group
        group_key = tc.get("base_group", "")
        base_group = get_tuning_group(group_key) if group_key else None
        if base_group is None:
            msg = f"基础规则组 '{group_key}' 不存在，拒绝启动"
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

        def configure(wf_instance, engine):
            from ..workflows.tuning_context import TuningRunContext
            wf_instance.run_ctx = TuningRunContext(
                selected_slots=selected_slots,
                rule_judges=rule_judges,
                judge_configs={
                    k: {**cfg, "switches": switches} for k, cfg in enabled.items()},
                judge_rule_keys=list(enabled),
                base_group=base_group,
                skip_tuning=skip_tuning,
                skip_start=skip_start,
                target_cell=target_cell,
            )
            # 创建调律进度信号桥（对话框由 tuning_tab 管理，hub 归 auto_tuning 所有）
            if engine is not None:
                from .tuning_progress_hub import TuningProgressHub
                engine._progress_hub = TuningProgressHub()

            rule_names_text = "、".join(j.rule_name for j in rule_judges)
            on_names = _tuning_switch_names(switches)
            if on_names:
                rule_names_text += f"（开关：{'、'.join(on_names)}）"
            if skip_tuning:
                host.append_log("[提示] 已开启「跳过实际调律」：仅模拟进出调律页，不执行调律")
            host.append_log(
                f"[开始] {flow_name} 流程，基础规则: {base_group.name}，"
                f"规则: {rule_names_text}，部位: {selected_slots}")

        host.run_workflow_implementation(
            "auto_tuning", flow_name, configure)

        # 工作流已启动：若勾选“自动打开”则显示进度对话框
        if self._cb_auto_progress.isChecked():
            self._ensure_progress_dialog(auto_show=True)

    # ─── 进度对话框管理 ────────────────────────────────

    def _toggle_progress_dialog(self):
        """“打开进度”按钮：显示/隐藏进度对话框"""
        dlg = self._ensure_progress_dialog(auto_show=False)
        if dlg is None:
            return  # hub 尚未创建，无法操作
        if dlg.is_visible():
            dlg.hide()
            self._btn_toggle_progress.setText("打开进度")
        else:
            dlg.show()
            self._btn_toggle_progress.setText("隐藏进度")

    def _ensure_progress_dialog(self, *, auto_show: bool):
        """懒创建或复用进度对话框，连接到当前 engine 的 hub"""
        engine = getattr(self._host, '_current_engine', None)
        hub = getattr(engine, '_progress_hub', None) if engine else None
        if hub is None:
            # hub 尚未创建（不应发生，但兑底）
            return self._tuning_progress_dialog

        dlg = self._tuning_progress_dialog
        if dlg is None:
            from .tuning_progress_dialog import TuningProgressDialog
            dlg = TuningProgressDialog(hub)
            self._tuning_progress_dialog = dlg
        else:
            # 复用已有对话框，重新连接到新 hub
            dlg.reconnect(hub)
            dlg.reset_state()

        if auto_show:
            dlg.show()
            self._btn_toggle_progress.setText("隐藏进度")
        return dlg

    # ─── 调律配置持久化（wf_configs["auto_tuning"]）──────────

    def _load_tuning_config(self):
        from ....core.config.wf_configs import get_wf_config
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
        self._tuning_globals.set_skip_tuning(bool(tc.get("skip_tuning", False)))
        # 自动打开进度对话框
        self._cb_auto_progress.blockSignals(True)
        self._cb_auto_progress.setChecked(bool(tc.get("auto_open_progress", False)))
        self._cb_auto_progress.blockSignals(False)
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
        from ....core.config.wf_configs import update_wf_config
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
            "skip_start": skip_start,
            "target_cell": target_cell,
            "auto_open_progress": self._cb_auto_progress.isChecked(),
        })

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
        """全局开关状态（「更多」页动态复选框）"""
        return self._tuning_globals.get_switches()

    def _get_tuning_skip_tuning(self) -> bool:
        """全局「跳过实际调律」开关（临时测试用，「更多」页）"""
        return self._tuning_globals.get_skip_tuning()
