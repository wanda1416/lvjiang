"""流派配置公共控件

调律 Tab 与「装备识别测试」面板共用的层级流派配置 UI：
每流派一个可勾选分组框（勾选标题 = 启用流派），组内按流派声明
（has_keep_pvp / sub_school_options / sub_school_playstyles）
生成该流派专属配置项。

配置结构与 session.json tuning.schools 节点一致：
{流派 key: {"enabled": bool, "keep_pvp": bool,
            "sub_schools": [...], "playstyles": {sub_key: [...]}}}
"""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox, QCheckBox,
)


class SchoolConfigWidget(QWidget):
    """层级流派配置控件（可多选）

    - 未勾选流派时子配置整体隐藏（折叠为仅标题行）；
    - 玩法复选框仅在对应指定流派勾选后显示；
    - 任意控件变更发出 config_changed 信号。
    """

    config_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from src.apps.yysls.evaluator import SCHOOL_CLASSES
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # 流派 key → 控件集：{"group": QGroupBox, "content": QWidget|None,
        #   "keep_pvp": QCheckBox|None, "sub_schools": {sub_key: QCheckBox},
        #   "playstyles": {sub_key: {ps_key: QCheckBox}}, "playstyle_rows": {sub_key: QWidget}}
        self._school_widgets: dict[str, dict] = {}
        for key, cls in SCHOOL_CLASSES.items():
            title = cls.school_name if cls.implemented else f"{cls.school_name}（未实现）"
            grp = QGroupBox(title)
            grp.setCheckable(True)
            grp.setChecked(False)
            grp.toggled.connect(
                lambda checked, k=key: self._on_school_toggled(k))
            grp_layout = QVBoxLayout(grp)
            grp_layout.setContentsMargins(8, 4, 8, 4)
            widgets: dict = {"group": grp, "content": None, "keep_pvp": None,
                            "sub_schools": {}, "playstyles": {},
                            "playstyle_rows": {}}
            # 子配置容器：未勾选流派时整体隐藏
            content = QWidget()
            content_layout = QVBoxLayout(content)
            content_layout.setContentsMargins(0, 0, 0, 0)
            if cls.sub_school_options:
                content_layout.addWidget(QLabel(cls.sub_school_label))
                for sub_key, sub_name in cls.sub_school_options.items():
                    sub_cb = QCheckBox(sub_name)
                    sub_cb.toggled.connect(
                        lambda checked, k=key, sk=sub_key:
                        self._on_sub_school_toggled(k, sk))
                    content_layout.addWidget(sub_cb)
                    widgets["sub_schools"][sub_key] = sub_cb
                    playstyles = cls.sub_school_playstyles.get(sub_key)
                    if not playstyles:
                        continue  # 破竹无玩法区分
                    ps_row = QWidget()
                    ps_layout = QHBoxLayout(ps_row)
                    ps_layout.setContentsMargins(24, 0, 0, 0)
                    ps_layout.addWidget(QLabel("玩法："))
                    widgets["playstyles"][sub_key] = {}
                    for ps_key, ps_name in playstyles.items():
                        ps_cb = QCheckBox(ps_name)
                        ps_cb.stateChanged.connect(
                            lambda _state: self.config_changed.emit())
                        ps_layout.addWidget(ps_cb)
                        widgets["playstyles"][sub_key][ps_key] = ps_cb
                    ps_layout.addStretch()
                    ps_row.setVisible(False)  # 未勾选指定流派时不出现玩法选项
                    content_layout.addWidget(ps_row)
                    widgets["playstyle_rows"][sub_key] = ps_row
            if cls.has_keep_pvp:
                pvp_cb = QCheckBox("保留 PVP 装备")
                pvp_cb.stateChanged.connect(
                    lambda _state: self.config_changed.emit())
                content_layout.addWidget(pvp_cb)
                widgets["keep_pvp"] = pvp_cb
            if content_layout.count() > 0:
                content.setVisible(False)  # 随流派勾选状态联动
                grp_layout.addWidget(content)
                widgets["content"] = content
            layout.addWidget(grp)
            self._school_widgets[key] = widgets

    # ─── 联动 ────────────────────────────────────────────────

    def _on_school_toggled(self, school_key: str):
        """流派勾选变化 → 子配置整体显隐并通知"""
        widgets = self._school_widgets[school_key]
        content = widgets["content"]
        if content is not None:
            content.setVisible(widgets["group"].isChecked())
        self.config_changed.emit()

    def _on_sub_school_toggled(self, school_key: str, sub_key: str):
        """指定流派勾选变化 → 联动玩法行显隐并通知"""
        widgets = self._school_widgets[school_key]
        ps_row = widgets["playstyle_rows"].get(sub_key)
        if ps_row is not None:
            checked = widgets["sub_schools"][sub_key].isChecked()
            ps_row.setVisible(checked)
        self.config_changed.emit()

    # ─── 读写配置 ────────────────────────────────────────────

    def get_config(self) -> dict[str, dict]:
        """从控件收集完整流派配置：{流派 key: 该流派配置 dict}"""
        result: dict[str, dict] = {}
        for key, widgets in self._school_widgets.items():
            cfg: dict = {"enabled": widgets["group"].isChecked()}
            if widgets["keep_pvp"] is not None:
                cfg["keep_pvp"] = widgets["keep_pvp"].isChecked()
            if widgets["sub_schools"]:
                cfg["sub_schools"] = [
                    sk for sk, cb in widgets["sub_schools"].items()
                    if cb.isChecked()
                ]
                cfg["playstyles"] = {
                    sk: [pk for pk, cb in ps_boxes.items() if cb.isChecked()]
                    for sk, ps_boxes in widgets["playstyles"].items()
                }
            result[key] = cfg
        return result

    def set_config(self, schools_cfg: dict[str, dict]):
        """按配置 dict 回填控件（不触发 config_changed）"""
        for key, widgets in self._school_widgets.items():
            cfg = schools_cfg.get(key, {})
            grp = widgets["group"]
            enabled = bool(cfg.get("enabled", False))
            grp.blockSignals(True)
            grp.setChecked(enabled)
            grp.blockSignals(False)
            if widgets["content"] is not None:
                widgets["content"].setVisible(enabled)
            if widgets["keep_pvp"] is not None:
                pvp_cb = widgets["keep_pvp"]
                pvp_cb.blockSignals(True)
                pvp_cb.setChecked(bool(cfg.get("keep_pvp", False)))
                pvp_cb.blockSignals(False)
            sub_selected = cfg.get("sub_schools", [])
            playstyles_cfg = cfg.get("playstyles", {})
            for sub_key, sub_cb in widgets["sub_schools"].items():
                sub_cb.blockSignals(True)
                sub_cb.setChecked(sub_key in sub_selected)
                sub_cb.blockSignals(False)
                ps_row = widgets["playstyle_rows"].get(sub_key)
                if ps_row is not None:
                    ps_row.setVisible(sub_key in sub_selected)
            for sub_key, ps_boxes in widgets["playstyles"].items():
                chosen = playstyles_cfg.get(sub_key, [])
                for ps_key, ps_cb in ps_boxes.items():
                    ps_cb.blockSignals(True)
                    ps_cb.setChecked(ps_key in chosen)
                    ps_cb.blockSignals(False)
