"""燕云十六声主窗口 —— 继承通用 MainWindow，追加燕云专属功能。

插件扩展点：
- 菜单：属性管理（F5）
- 左侧 Tab 2：调律（部位选择 + 开始调律）
- 右侧 Tab 2：装备状态
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QScrollArea,
    QCheckBox, QFormLayout,
)
from loguru import logger

from src.ui.main_window import MainWindow as GenericMainWindow
from .equip_status_panel import EquipStatusPanel


class MainWindow(GenericMainWindow):
    """燕云十六声专属主窗口"""

    # ─── 菜单扩展 ────────────────────────────────────────────

    def _extra_menu_items(self) -> list[tuple[str, Any, str]]:
        return [("属性管理", self._open_attr_manager, "F5")]

    def _open_attr_manager(self):
        from .attr_manager import AttrManagerDialog
        dialog = AttrManagerDialog(parent=self)
        dialog.exec()

    # ─── 左侧 Tab 扩展 ───────────────────────────────────────

    def _build_left_tabs(self):
        """通用 Tab（日常）+ 燕云 Tab（调律）"""
        super()._build_left_tabs()  # 先建「日常」Tab

        # ── Tab 2: 调律 ──
        # 顶部固定「开始调律」按钮 + 下方可滚动配置区（滚动时按钮始终可见）
        tab_container = QWidget()
        tab_layout = QVBoxLayout(tab_container)
        tab_layout.setContentsMargins(4, 4, 4, 4)

        self.btn_run_tuning = QPushButton("开始调律 (F9)")
        self.btn_run_tuning.clicked.connect(self._on_run_tuning)
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

        # ── 流派配置（每流派一个可勾选分组框，组内为该流派专属配置）──
        tuning_layout.addWidget(QLabel("<b>流派配置（可多选）：</b>"))
        self._build_school_config_ui(tuning_layout)

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
                       ("ring", "环佩"), ("pendant", "项链")]),
            ("防具类", [("head", "头部"), ("chest", "胸部"),
                       ("leg", "腿部"), ("wrist", "腕部")]),
        ]:
            grp = QGroupBox(group_name)
            grp_layout = QVBoxLayout(grp)
            for slot_key, slot_label in slots:
                cb = QCheckBox(slot_label)
                cb.setObjectName(slot_key)
                cb.setChecked(True)
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
        self._left_tabs.addTab(tab_container, "调律")

        self._load_tuning_config()

    def _build_school_config_ui(self, parent_layout):
        """生成层级流派配置：勾选分组框标题 = 启用流派，组内为该流派配置项；
        未勾选流派时子配置整体隐藏（折叠为仅标题行）；
        玩法复选框仅在对应指定流派勾选后显示。"""
        from src.apps.yysls.evaluator import (
            SCHOOL_CLASSES, SUB_SCHOOL_PLAYSTYLES, SUB_SCHOOLS,
        )
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
            if cls.needs_sub_school:
                content_layout.addWidget(QLabel("指定流派（必选）："))
                for sub_key, sub_name in SUB_SCHOOLS.items():
                    sub_cb = QCheckBox(sub_name)
                    sub_cb.toggled.connect(
                        lambda checked, k=key, sk=sub_key:
                        self._on_sub_school_toggled(k, sk))
                    content_layout.addWidget(sub_cb)
                    widgets["sub_schools"][sub_key] = sub_cb
                    playstyles = SUB_SCHOOL_PLAYSTYLES.get(sub_key)
                    if not playstyles:
                        continue  # 破竹无玩法区分
                    ps_row = QWidget()
                    ps_layout = QHBoxLayout(ps_row)
                    ps_layout.setContentsMargins(24, 0, 0, 0)
                    ps_layout.addWidget(QLabel("玩法："))
                    widgets["playstyles"][sub_key] = {}
                    for ps_key, ps_name in playstyles.items():
                        ps_cb = QCheckBox(ps_name)
                        ps_cb.stateChanged.connect(self._save_tuning_config)
                        ps_layout.addWidget(ps_cb)
                        widgets["playstyles"][sub_key][ps_key] = ps_cb
                    ps_layout.addStretch()
                    ps_row.setVisible(False)  # 未勾选指定流派时不出现玩法选项
                    content_layout.addWidget(ps_row)
                    widgets["playstyle_rows"][sub_key] = ps_row
            if cls.has_keep_pvp:
                pvp_cb = QCheckBox("保留 PVP 装备")
                pvp_cb.stateChanged.connect(self._save_tuning_config)
                content_layout.addWidget(pvp_cb)
                widgets["keep_pvp"] = pvp_cb
            if content_layout.count() > 0:
                content.setVisible(False)  # 随流派勾选状态联动
                grp_layout.addWidget(content)
                widgets["content"] = content
            parent_layout.addWidget(grp)
            self._school_widgets[key] = widgets

    def _on_school_toggled(self, school_key: str):
        """流派勾选变化 → 子配置整体显隐并保存"""
        widgets = self._school_widgets[school_key]
        content = widgets["content"]
        if content is not None:
            content.setVisible(widgets["group"].isChecked())
        self._save_tuning_config()

    def _on_sub_school_toggled(self, school_key: str, sub_key: str):
        """指定流派勾选变化 → 联动玩法行显隐并保存"""
        widgets = self._school_widgets[school_key]
        ps_row = widgets["playstyle_rows"].get(sub_key)
        if ps_row is not None:
            checked = widgets["sub_schools"][sub_key].isChecked()
            ps_row.setVisible(checked)
        self._save_tuning_config()

    # ─── 右侧 Tab 扩展 ───────────────────────────────────────

    def _build_right_tabs(self):
        """通用 Tab（运行日志）+ 燕云 Tab（装备状态）"""
        super()._build_right_tabs()  # 先建「运行日志」Tab

        self.equip_status_panel = EquipStatusPanel()
        self.equip_status_panel.refresh_requested.connect(self._refresh_equip_status)
        self.tabs.addTab(self.equip_status_panel, "装备状态")

        # 初始化装备面板
        self._refresh_equip_status()

    # ─── 装备状态 ────────────────────────────────────────────

    def _refresh_equip_status(self):
        import json
        from src.constants import LOCAL_CONFIG_DIR

        user_name = self._user_manager.get_active_user_name()
        if not user_name:
            self.equip_status_panel.refresh({})
            return
        user_file = LOCAL_CONFIG_DIR / "users" / f"{user_name}.json"
        if not user_file.exists():
            self.equip_status_panel.refresh({})
            return
        try:
            data = json.loads(user_file.read_text(encoding="utf-8"))
            equipped = data.get("equipped", {})
            self.equip_status_panel.refresh(equipped)
        except Exception as e:
            logger.error(f"加载用户装备数据失败: {e}")
            self.equip_status_panel.refresh({})

    # ─── 调律配置持久化 ──────────────────────────────────────

    def _load_tuning_config(self):
        from src.constants import SESSION_PATH
        default_slots = ["main_weapon", "sub_weapon", "ring", "pendant",
                         "head", "chest", "leg", "wrist"]
        selected = default_slots
        schools_cfg: dict = {"huiyi_general": {"enabled": True}}
        if SESSION_PATH.exists():
            try:
                import json
                data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
                tuning = data.get("tuning", {})
                saved = tuning.get("selected_slots", [])
                if saved:
                    selected = saved
                raw = tuning.get("schools")
                if isinstance(raw, dict):
                    schools_cfg = raw
                elif isinstance(raw, list):
                    # 旧 list 格式兼容：列表内流派视为启用（旧全局 keep_pvp 忽略）
                    schools_cfg = {k: {"enabled": True} for k in raw}
            except Exception:
                pass
        for cb in self._tuning_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(cb.objectName() in selected)
            cb.blockSignals(False)
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

    def _save_tuning_config(self):
        from src.constants import SESSION_PATH, LOCAL_CONFIG_DIR
        selected = self._get_tuning_selected_slots()
        schools_cfg = self._get_tuning_school_config()
        data = {}
        if SESSION_PATH.exists():
            try:
                import json
                data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        tuning = data.setdefault("tuning", {})
        tuning["selected_slots"] = selected
        tuning["schools"] = schools_cfg
        tuning.pop("school", None)    # 清理旧格式残留键
        tuning.pop("keep_pvp", None)
        try:
            import json
            LOCAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            SESSION_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存调律配置失败: {e}")

    def _set_all_tuning_checks(self, checked: bool):
        for cb in self._tuning_checkboxes:
            cb.setChecked(checked)
        self._save_tuning_config()

    def _get_tuning_selected_slots(self) -> list[str]:
        return [cb.objectName() for cb in self._tuning_checkboxes if cb.isChecked()]

    def _get_tuning_school_config(self) -> dict[str, dict]:
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
