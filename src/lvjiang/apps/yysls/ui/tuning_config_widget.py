"""调律规则配置公共控件

调律 Tab 与「装备调律验证」面板共用的调律规则配置 UI：
顶部按开关注册表（tuning_base.yaml switches 段）动态渲染的全局
开关复选框 + 每规则一个可勾选分组框（勾选标题 = 启用规则），
组内按规则声明（playstyle_options）生成玩法复选框（名字 + 主副武器摘要）。

配置结构与插件会话（config/local/yysls/session.json）tuning 节点一致：
- rules: {规则 key: {"enabled": bool, "playstyles": [名字]}}
  （playstyles 缺省 = 该规则声明的全部玩法）
- switches: {开关 key: bool}，由 get_switches/set_switches 单独读写
- skip_tuning: 全局布尔（临时测试开关），由 get_skip_tuning/set_skip_tuning 读写
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QCheckBox,
)


class TuningConfigWidget(QWidget):
    """调律规则配置控件（可多选）

    - 未勾选规则时玩法复选框整体隐藏（折叠为仅标题行）；
    - 任意控件变更发出 config_changed 信号。
    """

    config_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from lvjiang.apps.yysls.evaluator import get_tuning_rules
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 全局开关（按 tuning_base.yaml 开关注册表动态渲染）──
        from lvjiang.apps.yysls.evaluator.tuning_rules import get_tuning_base
        self._switch_cbs: dict[str, QCheckBox] = {}
        for switch_key, switch_name in get_tuning_base().switches.items():
            cb = QCheckBox(f"{switch_name}（全局）")
            cb.stateChanged.connect(
                lambda _state: self.config_changed.emit())
            layout.addWidget(cb)
            self._switch_cbs[switch_key] = cb

        # ── 全局：跳过实际调律（临时测试开关，仅模拟进出调律页）──
        self._skip_tuning_cb = QCheckBox("跳过实际调律（仅进出调律页，测试滚动用）")
        self._skip_tuning_cb.stateChanged.connect(
            lambda _state: self.config_changed.emit())
        layout.addWidget(self._skip_tuning_cb)

        # 规则 key → 控件集：{"group": QGroupBox, "content": QWidget|None,
        #   "playstyles": {名字: QCheckBox}}
        self._rule_widgets: dict[str, dict] = {}
        for key, rule in get_tuning_rules().items():
            title = rule.name if rule.implemented else f"{rule.name}（未实现）"
            grp = QGroupBox(title)
            grp.setCheckable(True)
            grp.setChecked(False)
            grp.toggled.connect(
                lambda checked, k=key: self._on_rule_toggled(k))
            grp_layout = QVBoxLayout(grp)
            grp_layout.setContentsMargins(8, 4, 8, 4)
            widgets: dict = {"group": grp, "content": None, "playstyles": {}}
            # 玩法复选框：未勾选规则时整体隐藏
            options = rule.playstyle_options
            if options:
                content = QWidget()
                content_layout = QVBoxLayout(content)
                content_layout.setContentsMargins(0, 0, 0, 0)
                for name, summary in options.items():
                    cb = QCheckBox(f"{name}（{summary}）")
                    cb.setChecked(True)  # 缺省 = 全部玩法
                    cb.stateChanged.connect(
                        lambda _state: self.config_changed.emit())
                    content_layout.addWidget(cb)
                    widgets["playstyles"][name] = cb
                content.setVisible(False)  # 随规则勾选状态联动
                grp_layout.addWidget(content)
                widgets["content"] = content
            layout.addWidget(grp)
            self._rule_widgets[key] = widgets

    # ─── 联动 ────────────────────────────────────────────────

    def _on_rule_toggled(self, rule_key: str):
        """规则勾选变化 → 玩法区显隐并通知"""
        widgets = self._rule_widgets[rule_key]
        content = widgets["content"]
        if content is not None:
            content.setVisible(widgets["group"].isChecked())
        self.config_changed.emit()

    # ─── 读写配置 ────────────────────────────────────────────

    def get_config(self) -> dict[str, dict]:
        """从控件收集完整规则配置：{规则 key: 该规则配置 dict}"""
        result: dict[str, dict] = {}
        for key, widgets in self._rule_widgets.items():
            cfg: dict = {"enabled": widgets["group"].isChecked()}
            if widgets["playstyles"]:
                cfg["playstyles"] = [
                    name for name, cb in widgets["playstyles"].items()
                    if cb.isChecked()
                ]
            result[key] = cfg
        return result

    def set_config(self, rules_cfg: dict[str, dict]):
        """按配置 dict 回填控件（不触发 config_changed）

        规则配置缺 playstyles 键时视为全选（缺省 = 全部玩法）。
        """
        for key, widgets in self._rule_widgets.items():
            cfg = rules_cfg.get(key, {})
            grp = widgets["group"]
            enabled = bool(cfg.get("enabled", False))
            grp.blockSignals(True)
            grp.setChecked(enabled)
            grp.blockSignals(False)
            if widgets["content"] is not None:
                widgets["content"].setVisible(enabled)
            selected = cfg.get("playstyles")
            for name, cb in widgets["playstyles"].items():
                cb.blockSignals(True)
                cb.setChecked(selected is None or name in selected)
                cb.blockSignals(False)

    def get_switches(self) -> dict[str, bool]:
        """全局开关状态：{开关 key: 是否开启}"""
        return {key: cb.isChecked() for key, cb in self._switch_cbs.items()}

    def set_switches(self, switches: dict[str, bool]):
        """回填全局开关（不触发 config_changed；未配置的开关视为关闭）"""
        switches = switches or {}
        for key, cb in self._switch_cbs.items():
            cb.blockSignals(True)
            cb.setChecked(bool(switches.get(key, False)))
            cb.blockSignals(False)

    def get_skip_tuning(self) -> bool:
        """全局「跳过实际调律」开关（临时测试用）"""
        return self._skip_tuning_cb.isChecked()

    def set_skip_tuning(self, value: bool):
        """回填全局跳过调律开关（不触发 config_changed）"""
        self._skip_tuning_cb.blockSignals(True)
        self._skip_tuning_cb.setChecked(bool(value))
        self._skip_tuning_cb.blockSignals(False)
