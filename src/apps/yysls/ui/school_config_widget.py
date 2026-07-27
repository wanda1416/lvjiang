"""流派配置公共控件

调律 Tab 与「装备调律验证」面板共用的流派配置 UI：
顶部全局「保留 PVP 装备」复选框 + 每流派一个可勾选分组框
（勾选标题 = 启用流派），组内按流派声明（weapon_rule_options）
生成武器规则复选框（名字 + 主副武器摘要）。

配置结构与插件会话（config/local/yysls/session.json）tuning 节点一致：
- schools: {流派 key: {"enabled": bool, "weapon_rules": [名字]}}
  （weapon_rules 缺省 = 该规则声明的全部武器规则）
- keep_pvp: 全局布尔，由 get_keep_pvp/set_keep_pvp 单独读写
- skip_tuning: 全局布尔（临时测试开关），由 get_skip_tuning/set_skip_tuning 读写
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QCheckBox,
)


class SchoolConfigWidget(QWidget):
    """流派配置控件（可多选）

    - 未勾选流派时武器规则复选框整体隐藏（折叠为仅标题行）；
    - 任意控件变更发出 config_changed 信号。
    """

    config_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from src.apps.yysls.evaluator import get_school_rules
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 全局：保留 PVP 装备 ──
        self._keep_pvp_cb = QCheckBox("保留 PVP 装备（全局）")
        self._keep_pvp_cb.stateChanged.connect(
            lambda _state: self.config_changed.emit())
        layout.addWidget(self._keep_pvp_cb)

        # ── 全局：跳过实际调律（临时测试开关，仅模拟进出调律页）──
        self._skip_tuning_cb = QCheckBox("跳过实际调律（仅进出调律页，测试滚动用）")
        self._skip_tuning_cb.stateChanged.connect(
            lambda _state: self.config_changed.emit())
        layout.addWidget(self._skip_tuning_cb)

        # 流派 key → 控件集：{"group": QGroupBox, "content": QWidget|None,
        #   "weapon_rules": {名字: QCheckBox}}
        self._school_widgets: dict[str, dict] = {}
        for key, rule in get_school_rules().items():
            title = rule.name if rule.implemented else f"{rule.name}（未实现）"
            grp = QGroupBox(title)
            grp.setCheckable(True)
            grp.setChecked(False)
            grp.toggled.connect(
                lambda checked, k=key: self._on_school_toggled(k))
            grp_layout = QVBoxLayout(grp)
            grp_layout.setContentsMargins(8, 4, 8, 4)
            widgets: dict = {"group": grp, "content": None, "weapon_rules": {}}
            # 武器规则复选框：未勾选流派时整体隐藏
            options = rule.weapon_rule_options
            if options:
                content = QWidget()
                content_layout = QVBoxLayout(content)
                content_layout.setContentsMargins(0, 0, 0, 0)
                for name, summary in options.items():
                    cb = QCheckBox(f"{name}（{summary}）")
                    cb.setChecked(True)  # 缺省 = 全部武器规则
                    cb.stateChanged.connect(
                        lambda _state: self.config_changed.emit())
                    content_layout.addWidget(cb)
                    widgets["weapon_rules"][name] = cb
                content.setVisible(False)  # 随流派勾选状态联动
                grp_layout.addWidget(content)
                widgets["content"] = content
            layout.addWidget(grp)
            self._school_widgets[key] = widgets

    # ─── 联动 ────────────────────────────────────────────────

    def _on_school_toggled(self, school_key: str):
        """流派勾选变化 → 武器规则区显隐并通知"""
        widgets = self._school_widgets[school_key]
        content = widgets["content"]
        if content is not None:
            content.setVisible(widgets["group"].isChecked())
        self.config_changed.emit()

    # ─── 读写配置 ────────────────────────────────────────────

    def get_config(self) -> dict[str, dict]:
        """从控件收集完整流派配置：{流派 key: 该流派配置 dict}"""
        result: dict[str, dict] = {}
        for key, widgets in self._school_widgets.items():
            cfg: dict = {"enabled": widgets["group"].isChecked()}
            if widgets["weapon_rules"]:
                cfg["weapon_rules"] = [
                    name for name, cb in widgets["weapon_rules"].items()
                    if cb.isChecked()
                ]
            result[key] = cfg
        return result

    def set_config(self, schools_cfg: dict[str, dict]):
        """按配置 dict 回填控件（不触发 config_changed）

        流派配置缺 weapon_rules 键时视为全选（缺省 = 全部武器规则）。
        """
        for key, widgets in self._school_widgets.items():
            cfg = schools_cfg.get(key, {})
            grp = widgets["group"]
            enabled = bool(cfg.get("enabled", False))
            grp.blockSignals(True)
            grp.setChecked(enabled)
            grp.blockSignals(False)
            if widgets["content"] is not None:
                widgets["content"].setVisible(enabled)
            selected = cfg.get("weapon_rules")
            for name, cb in widgets["weapon_rules"].items():
                cb.blockSignals(True)
                cb.setChecked(selected is None or name in selected)
                cb.blockSignals(False)

    def get_keep_pvp(self) -> bool:
        """全局「保留 PVP 装备」开关"""
        return self._keep_pvp_cb.isChecked()

    def set_keep_pvp(self, value: bool):
        """回填全局 PVP 开关（不触发 config_changed）"""
        self._keep_pvp_cb.blockSignals(True)
        self._keep_pvp_cb.setChecked(bool(value))
        self._keep_pvp_cb.blockSignals(False)

    def get_skip_tuning(self) -> bool:
        """全局「跳过实际调律」开关（临时测试用）"""
        return self._skip_tuning_cb.isChecked()

    def set_skip_tuning(self, value: bool):
        """回填全局跳过调律开关（不触发 config_changed）"""
        self._skip_tuning_cb.blockSignals(True)
        self._skip_tuning_cb.setChecked(bool(value))
        self._skip_tuning_cb.blockSignals(False)
