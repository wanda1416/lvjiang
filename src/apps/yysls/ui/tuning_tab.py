"""燕云「调律」Tab —— 通过 AppHooks 注入通用 MainWindow 的插件页面。

职责：
- 流派配置 / 部位选择 UI 与插件会话持久化
- 「开始调律」按钮三态（运行中 / 未就绪 / 就绪），订阅宿主 automation_state_changed
- ``f9_run()``：F9 快捷键与按钮共用的启停入口，
  收集配置并通过宿主 ``run_workflow_implementation`` 启动 auto_tuning
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QScrollArea,
    QCheckBox,
)

from .tuning_config_widget import TuningConfigWidget


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
        # 顶部固定「开始调律」按钮 + 下方可滚动配置区（滚动时按钮始终可见）
        tab_layout = QVBoxLayout(self)
        tab_layout.setContentsMargins(4, 4, 4, 4)

        self.btn_run_tuning = QPushButton("开始调律 (F9)")
        self.btn_run_tuning.clicked.connect(self.f9_run)
        self.btn_run_tuning.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; font-size: 14px;"
        )
        tab_layout.addWidget(self.btn_run_tuning)

        tuning_scroll = QScrollArea()
        tuning_scroll.setWidgetResizable(True)
        tuning_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tuning_panel = QWidget()
        tuning_layout = QVBoxLayout(tuning_panel)
        tuning_layout.setContentsMargins(4, 4, 4, 4)

        # ── 流派配置（公共控件，变更即持久化）──
        tuning_layout.addWidget(QLabel("<b>流派配置（可多选）：</b>"))
        self._tuning_config = TuningConfigWidget()
        self._tuning_config.config_changed.connect(self._save_tuning_config)
        tuning_layout.addWidget(self._tuning_config)

        # ── 部位选择（标题行内嵌全选/取消全选，仅作用于部位复选框）──
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
        tuning_layout.addLayout(slots_header)
        self._tuning_checkboxes: list[QCheckBox] = []

        slots_row = QHBoxLayout()
        for group_name, slots in [
            ("武器类", [("main_weapon", "主武器"), ("sub_weapon", "副武器"),
                       ("ring", "环"), ("pendant", "佩")]),
            ("防具类", [("head", "冠胄"), ("chest", "胸甲"),
                       ("leg", "胫甲"), ("wrist", "腕甲")]),
        ]:
            grp = QGroupBox(group_name)
            grp_layout = QVBoxLayout(grp)
            for slot_key, slot_label in slots:
                cb = QCheckBox(slot_label)
                cb.setObjectName(slot_key)
                cb.setChecked(True)
                if slot_key == "sub_weapon":
                    # 主武器槽已展示全部武器，副武器无需遍历，强制禁用
                    cb.setChecked(False)
                    cb.setEnabled(False)
                # 初始状态设置完成后再连接，避免构造期半成品状态误落盘
                cb.stateChanged.connect(self._save_tuning_config)
                grp_layout.addWidget(cb)
                self._tuning_checkboxes.append(cb)
            slots_row.addWidget(grp)
        tuning_layout.addLayout(slots_row)

        tuning_layout.addStretch()
        tuning_scroll.setWidget(tuning_panel)
        # 宽度自适应：滚动区最小宽 = 内容最小宽 + 垂直滚动条，
        # 避免左侧分栏默认宽度下内容被裁切（水平滚动条已禁用）
        scrollbar_w = tuning_scroll.verticalScrollBar().sizeHint().width()
        tuning_scroll.setMinimumWidth(
            tuning_panel.minimumSizeHint().width() + scrollbar_w + 8)
        tab_layout.addWidget(tuning_scroll)

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
            get_tuning_judge, get_tuning_rules, get_rule_names,
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
        required_scenes = [
            "game_main_page", "game_menu_page", "bag_equip_detail",
            "equip_weapon_detail", "equip_armor_detail",
        ]

        def configure(wf_instance, engine):
            wf_instance._selected_slots = selected_slots
            wf_instance._rule_judges = rule_judges
            # 潜力判定配置（形状与 single_tuning._load_rule_config 一致，
            # 对齐 UI 实时勾选）：供 auto_tuning 的 judge_tuning_worthiness/
            # judge_equipment_potential 使用
            wf_instance._judge_configs = {
                k: {**cfg, "switches": switches} for k, cfg in enabled.items()}
            wf_instance._judge_rule_keys = list(enabled)
            # 临时测试开关：跳过实际调律（仅模拟进出调律页，便于测试滚动）
            wf_instance._skip_tuning = skip_tuning
            # 调律说明文档（logs/tuning/）的操作用户名
            wf_instance._doc_username = host.active_user_name()

            rule_names_text = "、".join(j.rule_name for j in rule_judges)
            on_names = _tuning_switch_names(switches)
            if on_names:
                rule_names_text += f"（开关：{'、'.join(on_names)}）"
            if skip_tuning:
                host.append_log("[提示] 已开启「跳过实际调律」：仅模拟进出调律页，不执行调律")
            host.append_log(
                f"[开始] {flow_name} 流程，规则: {rule_names_text}，部位: {selected_slots}")

        host.run_workflow_implementation(
            "auto_tuning", flow_name, required_scenes, configure)

    # ─── 调律配置持久化（插件会话 config/local/yysls/session.json）──

    def _load_tuning_config(self):
        from ..plugin_session import get_plugin_session
        default_slots = ["main_weapon", "ring", "pendant",
                         "head", "chest", "leg", "wrist"]
        tuning = get_plugin_session().get_section("tuning")
        selected = tuning.get("selected_slots") or default_slots
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
        self._tuning_config.set_switches(tuning.get("switches") or {})
        self._tuning_config.set_skip_tuning(bool(tuning.get("skip_tuning", False)))

    def _save_tuning_config(self):
        from ..plugin_session import get_plugin_session
        get_plugin_session().set_section("tuning", {
            "selected_slots": self._get_tuning_selected_slots(),
            "rules": self._get_tuning_rule_config(),
            "switches": self._get_tuning_switches(),
            "skip_tuning": self._get_tuning_skip_tuning(),
        })

    def _set_all_tuning_checks(self, checked: bool):
        for cb in self._tuning_checkboxes:
            if cb.isEnabled():
                cb.setChecked(checked)
        self._save_tuning_config()

    def _get_tuning_selected_slots(self) -> list[str]:
        return [cb.objectName() for cb in self._tuning_checkboxes if cb.isChecked()]

    def _get_tuning_rule_config(self) -> dict[str, dict]:
        """流派配置委托公共控件收集"""
        return self._tuning_config.get_config()

    def _get_tuning_switches(self) -> dict[str, bool]:
        """全局开关状态（公共控件顶部动态复选框）"""
        return self._tuning_config.get_switches()

    def _get_tuning_skip_tuning(self) -> bool:
        """全局「跳过实际调律」开关（临时测试用，与 switches 同级）"""
        return self._tuning_config.get_skip_tuning()
