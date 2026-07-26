"""燕云十六声主窗口 —— 继承通用 MainWindow，追加燕云专属功能。

插件扩展点：
- 「燕云」菜单：装备属性管理（F5）、装备调律规则（内含调律验证入口）
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
    QCheckBox,
)
from loguru import logger

from src.ui.main_window import MainWindow as GenericMainWindow
from .equip_status_panel import EquipStatusPanel
from .school_config_widget import SchoolConfigWidget


class MainWindow(GenericMainWindow):
    """燕云十六声专属主窗口"""

    # ─── 菜单扩展 ────────────────────────────────────────────

    def _plugin_menu_spec(self) -> tuple[str, list[tuple[str, Any, str]]] | None:
        return ("燕云", [
            ("装备属性管理", self._open_attr_manager, "F5"),
            ("装备调律规则", self._open_tuning_rules, ""),
        ])

    def _open_attr_manager(self):
        from .attr_manager import AttrManagerDialog
        dialog = AttrManagerDialog(parent=self)
        dialog.exec()

    def _open_tuning_rules(self):
        from .tuning_rules import TuningRulesDialog
        dialog = TuningRulesDialog(parent=self)
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

        # ── 流派配置（公共控件，变更即持久化）──
        tuning_layout.addWidget(QLabel("<b>流派配置（可多选）：</b>"))
        self._school_config = SchoolConfigWidget()
        self._school_config.config_changed.connect(self._save_tuning_config)
        tuning_layout.addWidget(self._school_config)

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

    # ─── 调律配置持久化（插件会话 config/local/yysls/session.json）──

    def _load_tuning_config(self):
        from ..session import get_plugin_session
        default_slots = ["main_weapon", "sub_weapon", "ring", "pendant",
                         "head", "chest", "leg", "wrist"]
        tuning = get_plugin_session().get_section("tuning")
        selected = tuning.get("selected_slots") or default_slots
        raw = tuning.get("schools")
        if isinstance(raw, dict):
            schools_cfg = raw
        elif isinstance(raw, list):
            # 旧 list 格式兼容：列表内流派视为启用（旧全局 keep_pvp 忽略）
            schools_cfg = {k: {"enabled": True} for k in raw}
        else:
            schools_cfg = {"huiyi_general": {"enabled": True}}
        for cb in self._tuning_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(cb.objectName() in selected)
            cb.blockSignals(False)
        self._school_config.set_config(schools_cfg)

    def _save_tuning_config(self):
        from ..session import get_plugin_session
        get_plugin_session().set_section("tuning", {
            "selected_slots": self._get_tuning_selected_slots(),
            "schools": self._get_tuning_school_config(),
        })

    def _set_all_tuning_checks(self, checked: bool):
        for cb in self._tuning_checkboxes:
            cb.setChecked(checked)
        self._save_tuning_config()

    def _get_tuning_selected_slots(self) -> list[str]:
        return [cb.objectName() for cb in self._tuning_checkboxes if cb.isChecked()]

    def _get_tuning_school_config(self) -> dict[str, dict]:
        """流派配置委托公共控件收集"""
        return self._school_config.get_config()
