"""燕云「调律」Tab —— 通过 AppHooks 注入通用 MainWindow 的插件页面。

职责：
- 调律配置三页 Tab（规则 | 部位 | 更多）与插件会话持久化：
  规则 = 调律规则与玩法；部位 = 调律部位选择；
  更多 = 跳过实际调律 mock + 全部注册开关
- 「开始调律」按钮三态（运行中 / 未就绪 / 就绪），订阅宿主 automation_state_changed
- ``f9_run()``：F9 快捷键与按钮共用的启停入口，
  收集配置并通过宿主 ``run_workflow_implementation`` 启动 auto_tuning
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..slots import DEFAULT_SLOTS, LOCKED_SLOTS, SLOT_GROUPS
from .tuning_config_widget import TuningConfigWidget, TuningGlobalsWidget


def _tuning_switch_names(switches: dict[str, bool]) -> list[str]:
    """开启的开关 key → 注册表显示名（注册表不可用时退回 key）"""
    try:
        from ..evaluator.tuning_rules import get_tuning_base
        names = get_tuning_base().switches
    except Exception:
        names = {}
    return [names.get(k, k) for k, v in switches.items() if v]


class TuningTab(QWidget):
    """调律 Tab（host 为通用 MainWindow，提供宿主 API 与信号）"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._build_ui()
        self._load_tuning_config()
        host.automation_state_changed.connect(self._on_automation_state)

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        # 顶部固定「开始调律」按钮 + 下方配置三页 Tab（规则 | 部位 | 更多）
        tab_layout = QVBoxLayout(self)
        tab_layout.setContentsMargins(4, 4, 4, 4)

        self.btn_run_tuning = QPushButton("开始调律 (F9)")
        self.btn_run_tuning.clicked.connect(self.f9_run)
        self.btn_run_tuning.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; font-size: 14px;"
        )
        tab_layout.addWidget(self.btn_run_tuning)

        config_tabs = QTabWidget()
        config_tabs.addTab(self._build_rules_page(), "规则")
        config_tabs.addTab(self._build_slots_page(), "部位")
        config_tabs.addTab(self._build_more_page(), "更多")
        tab_layout.addWidget(config_tabs)

    def _wrap_scroll(self, panel: QWidget) -> QScrollArea:
        """页面统一包可滚动容器（禁水平滚动，最小宽避免内容被裁切）"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel)
        scrollbar_w = scroll.verticalScrollBar().sizeHint().width()
        scroll.setMinimumWidth(
            panel.minimumSizeHint().width() + scrollbar_w + 8)
        return scroll

    def _build_rules_page(self) -> QWidget:
        """「规则」页：调律规则与玩法（公共控件，变更即持久化）"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(QLabel("<b>流派配置（可多选）：</b>"))
        self._tuning_config = TuningConfigWidget(show_globals=False)
        self._tuning_config.config_changed.connect(self._save_tuning_config)
        layout.addWidget(self._tuning_config)
        layout.addStretch()
        return self._wrap_scroll(panel)

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
                "background-color: #f44336; color: white; font-weight: bold; padding: 10px; font-size: 14px;"
            )
        elif state == "not_ready":
            self.btn_run_tuning.setText("未就绪")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 10px; font-size: 14px;"
            )
        else:
            self.btn_run_tuning.setText("开始调律 (F9)")
            self.btn_run_tuning.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; font-size: 14px;"
            )

    # ─── 启停入口（F9 快捷键 / 按钮点击共用）───────────────────

    def f9_run(self):
        """运行中 → 停止；否则收集配置启动自动调律"""
        if self._host.is_running:
            self._host.request_stop()
            return
        self._start_tuning()

    def _start_tuning(self):
        """收集并校验调律配置，通过宿主启动 auto_tuning 工作流"""
        host = self._host

        selected_slots = self._get_tuning_selected_slots()
        if not selected_slots:
            host.append_log("[错误] 请至少选择一个调律部位")
            return

        # 获取调律规则配置（按规则分组的层级 dict）并创建判定器
        from ..evaluator import (
            get_rule_names,
            get_tuning_judge,
            get_tuning_rules,
            is_rule_implemented,
        )
        rules_cfg = self._get_tuning_rule_config()
        enabled = {k: cfg for k, cfg in rules_cfg.items() if cfg.get("enabled")}
        if not enabled:
            host.append_log("[错误] 请至少选择一个调律规则")
            return
        rule_judges = []
        rule_map = get_tuning_rules()
        switches = {str(k): bool(v) for k, v in self._get_tuning_switches().items()}
        skip_tuning = self._get_tuning_skip_tuning()
        for rule_key, cfg in enabled.items():
            if not is_rule_implemented(rule_key):
                host.append_log(f"[警告] 规则「{get_rule_names().get(rule_key, rule_key)}」判定暂未实现，已跳过")
                continue
            rule = rule_map[rule_key]
            wr_cfg = cfg.get("playstyles")
            if rule.playstyles and wr_cfg is not None and not wr_cfg:
                host.append_log(f"[错误] 规则「{rule.name}」需至少勾选一个玩法")
                return
            rule_judges.append(
                get_tuning_judge(rule_key, {**cfg, "switches": switches}))
        if not rule_judges:
            host.append_log("[错误] 选中的规则均未实现判定逻辑")
            return

        flow_name = "自动调律"

        def configure(wf_instance, engine):
            from ..workflows.run_context import TuningRunContext
            # 运行上下文一次性收口注入（字段契约见 TuningRunContext）：
            # judge_configs 对齐 UI 实时勾选，供 judge_equipment_potential 使用；
            # skip_tuning 为临时测试开关（仅模拟进出调律页，便于测试滚动）
            skip_start = None
            if self._cb_skip.isChecked() and self._cb_skip.isEnabled():
                skip_start = (self._sp_skip_row.value(), self._sp_skip_col.value())
            target_cell = None
            if self._cb_target.isChecked() and self._cb_target.isEnabled():
                target_cell = (self._sp_target_row.value(), self._sp_target_col.value())
            wf_instance.run_ctx = TuningRunContext(
                selected_slots=selected_slots,
                rule_judges=rule_judges,
                judge_configs={
                    k: {**cfg, "switches": switches} for k, cfg in enabled.items()},
                judge_rule_keys=list(enabled),
                skip_tuning=skip_tuning,
                doc_username=host.active_user_name(),
                skip_start=skip_start,
                target_cell=target_cell,
            )

            rule_names_text = "、".join(j.rule_name for j in rule_judges)
            on_names = _tuning_switch_names(switches)
            if on_names:
                rule_names_text += f"（开关：{'、'.join(on_names)}）"
            if skip_tuning:
                host.append_log("[提示] 已开启「跳过实际调律」：仅模拟进出调律页，不执行调律")
            host.append_log(
                f"[开始] {flow_name} 流程，规则: {rule_names_text}，部位: {selected_slots}")

        host.run_workflow_implementation(
            "auto_tuning", flow_name, configure)

    # ─── 调律配置持久化（插件会话 config/session/yysls/session.json）──

    def _load_tuning_config(self):
        from ..plugin_session import get_plugin_session
        tuning = get_plugin_session().get_section("tuning")
        selected = tuning.get("selected_slots") or list(DEFAULT_SLOTS)
        raw = tuning.get("rules")
        if isinstance(raw, dict):
            rules_cfg = raw
        else:
            rules_cfg = {"huiyi_general": {"enabled": True}}
        for cb in self._tuning_checkboxes:
            cb.blockSignals(True)
            # 禁用项（副武器）不随会话配置回选
            cb.setChecked(cb.isEnabled() and cb.objectName() in selected)
            cb.blockSignals(False)
        self._tuning_config.set_config(rules_cfg)
        self._tuning_globals.set_switches(tuning.get("switches") or {})
        self._tuning_globals.set_skip_tuning(bool(tuning.get("skip_tuning", False)))
        # 初始跳过 / 指定调律
        for key, cb, sp_row, sp_col in (
            ("skip_start", self._cb_skip, self._sp_skip_row, self._sp_skip_col),
            ("target_cell", self._cb_target, self._sp_target_row, self._sp_target_col),
        ):
            val = tuning.get(key)
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
        from ..plugin_session import get_plugin_session
        skip_start = None
        if self._cb_skip.isChecked():
            skip_start = [self._sp_skip_row.value(), self._sp_skip_col.value()]
        target_cell = None
        if self._cb_target.isChecked():
            target_cell = [self._sp_target_row.value(), self._sp_target_col.value()]
        get_plugin_session().set_section("tuning", {
            "selected_slots": self._get_tuning_selected_slots(),
            "rules": self._get_tuning_rule_config(),
            "switches": self._get_tuning_switches(),
            "skip_tuning": self._get_tuning_skip_tuning(),
            "skip_start": skip_start,
            "target_cell": target_cell,
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
        """流派配置委托公共控件收集"""
        return self._tuning_config.get_config()

    def _get_tuning_switches(self) -> dict[str, bool]:
        """全局开关状态（「更多」页动态复选框）"""
        return self._tuning_globals.get_switches()

    def _get_tuning_skip_tuning(self) -> bool:
        """全局「跳过实际调律」开关（临时测试用，「更多」页）"""
        return self._tuning_globals.get_skip_tuning()
