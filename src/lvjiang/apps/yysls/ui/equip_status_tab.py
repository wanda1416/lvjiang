"""燕云「装备数据」Tab —— 拆分为「当前装备」和「其他装备」两个子面板。"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from .equip_status_panel import _QUALITY_COLORS, EquipStatusPanel
from .profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from .profile.tab import add_user_nav_buttons

# 背包部位筛选器：key → session.bag_items 分组名
_SLOT_FILTERS = [
    ("all", None, tr("全部")),
    ("weapon", "weapon", tr("武器")),
    ("ring", "ring", tr("环")),
    ("pendant", "pendant", tr("佩")),
    ("head", "head", tr("冠胄")),
    ("chest", "chest", tr("胸甲")),
    ("leg", "leg", tr("胫甲")),
    ("wrist", "wrist", tr("腕甲")),
]

# 部位显示名（背包分组 → 卡片标签）
_GROUP_PART_LABELS = {
    "weapon": tr("武器"),
    "ring": tr("环"),
    "pendant": tr("佩"),
    "head": tr("冠胄"),
    "chest": tr("胸甲"),
    "leg": tr("胫甲"),
    "wrist": tr("腕甲"),
}

_GRID_COLS = 4  # 默认值，实际从 settings.equip_display.grid_columns 读取

_FILTER_STYLE_NORMAL = (
    "QPushButton { background-color: #e9ecef; border: 1px solid #ced4da; "
    "border-radius: 3px; padding: 2px 8px; font-size: 12px; }"
    "QPushButton:hover { background-color: #dee2e6; }"
)
_FILTER_STYLE_ACTIVE = (
    "QPushButton { background-color: #4CAF50; color: white; "
    "border: 1px solid #388E3C; border-radius: 3px; "
    "padding: 2px 8px; font-size: 12px; font-weight: bold; }"
)


class _CompactEquipCard(QFrame):
    """紧凑装备卡片 —— 用于其他装备网格"""

    def __init__(self, display_params: dict | None = None, parent=None):
        super().__init__(parent)
        dp = display_params or {}
        self._name_fs = dp.get("name_font_size", 13)
        self._level_fs = dp.get("level_font_size", 12)
        self._affix_fs = dp.get("affix_font_size", 11)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            _CompactEquipCard {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 4px;
            }
        """)
        self.setFixedHeight(dp.get("card_min_height", 180))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # 第一行：部位 · 装备名
        self.lbl_name = QLabel()
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")
        layout.addWidget(self.lbl_name)

        # 第二行：等级
        self.lbl_level = QLabel()
        self.lbl_level.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #666666;")
        layout.addWidget(self.lbl_level)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #dee2e6;")
        line.setFixedHeight(1)
        layout.addWidget(line)

        # 词条区域
        self.affix_container = QWidget()
        self.affix_layout = QVBoxLayout(self.affix_container)
        self.affix_layout.setContentsMargins(0, 0, 0, 0)
        self.affix_layout.setSpacing(2)
        layout.addWidget(self.affix_container)

        layout.addStretch()

    def set_equip(self, equip_data: dict, part_label: str):
        """填充装备数据"""
        quality = equip_data.get("quality")
        color = _QUALITY_COLORS.get(quality, "#888888")

        name = equip_data.get("name", tr("未知"))
        self.lbl_name.setText(f"{part_label} · {name}")
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px; color: {color};")

        level = equip_data.get("level", "?")
        is_chengyin = equip_data.get("is_chengyin", False)
        chengyin_tag = " [" + tr("承音") + "]" if is_chengyin else ""
        self.lbl_level.setText(f"Lv{level}{chengyin_tag}")
        self.lbl_level.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #666666;")

        # 词条列表
        self._clear_affixes()
        for i in range(1, 6):
            key = f"affix_{i}"
            affix = equip_data.get(key)
            if not affix or not affix.get("name"):
                continue

            value = affix.get("value", "")
            unit = affix.get("unit", "")
            cap_pct = affix.get("cap_pct")

            if isinstance(value, (int, float)):
                if unit == "%":
                    val_str = f"{value}%"
                elif isinstance(value, float):
                    val_str = f"{value:.1f}"
                else:
                    val_str = str(value)
            else:
                val_str = str(value)

            val_color = "#333333"
            if cap_pct is not None:
                if cap_pct >= 90:
                    val_color = "#B8860B"
                elif cap_pct >= 80:
                    val_color = "#8B5CF6"
                elif cap_pct >= 60:
                    val_color = "#2563EB"
                else:
                    val_color = "#16A34A"

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)

            lbl_name = QLabel(affix["name"])
            lbl_name.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: #555555;")
            row.addWidget(lbl_name, stretch=1)

            lbl_val = QLabel(val_str)
            lbl_val.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: {val_color}; font-weight: bold;")
            row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

            self.affix_layout.addLayout(row)

        # 定音词条
        dingyin = equip_data.get("dingyin")
        if dingyin and dingyin.get("name"):
            dash = QFrame()
            dash.setFrameShape(QFrame.Shape.NoFrame)
            dash.setStyleSheet(
                "border: none; border-top: 1px dashed #adb5bd;")
            dash.setFixedHeight(1)
            self.affix_layout.addWidget(dash)

            dy_value = dingyin.get("value", "")
            dy_val_str = (
                f"{dy_value}%"
                if isinstance(dy_value, (int, float))
                else str(dy_value)
            )

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)

            lbl_name = QLabel(dingyin["name"])
            lbl_name.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: #555555;")
            row.addWidget(lbl_name, stretch=1)

            lbl_val = QLabel(dy_val_str)
            lbl_val.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: #333333; font-weight: bold;")
            row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

            self.affix_layout.addLayout(row)

    def _clear_affixes(self):
        while self.affix_layout.count() > 0:
            item = self.affix_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                sub = item.layout()
                while sub.count() > 0:
                    child = sub.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                sub.deleteLater()


class _OtherEquipPage(QWidget):
    """其他装备页 —— 网格 + 部位筛选"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_filter: str | None = None
        self._bag_items: dict = {}
        self._filter_buttons: list[tuple[str, QPushButton]] = []
        self._display_params: dict = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 筛选栏
        self._filter_bar = QHBoxLayout()
        self._filter_bar.setSpacing(4)
        for key, _group, label in _SLOT_FILTERS:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(_FILTER_STYLE_NORMAL)
            btn.clicked.connect(lambda _, k=key: self._on_filter_clicked(k))
            self._filter_bar.addWidget(btn)
            self._filter_buttons.append((key, btn))
        self._filter_bar.addStretch()
        layout.addLayout(self._filter_bar)

        # 滚动网格
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # wrapper 贴顶：grid 在上，stretch 占剩余空间
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)

        self._grid_container = QWidget()
        # 限制 grid_container 垂直扩展，确保内容贴顶
        self._grid_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(8)
        wrapper_layout.addWidget(self._grid_container)
        wrapper_layout.addStretch()

        scroll.setWidget(wrapper)
        layout.addWidget(scroll, stretch=1)

        # 默认选中「全部」
        self._on_filter_clicked("all")

    def _on_filter_clicked(self, key: str):
        self._current_filter = None if key == "all" else key
        for k, btn in self._filter_buttons:
            is_active = (k == key)
            btn.setChecked(is_active)
            btn.setStyleSheet(
                _FILTER_STYLE_ACTIVE if is_active else _FILTER_STYLE_NORMAL)
        self._rebuild_grid()

    def _rebuild_grid(self):
        cols = self._display_params.get("grid_columns", _GRID_COLS)

        # 清空网格
        while self._grid.count() > 0:
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        # 重置列拉伸策略
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

        # 收集当前筛选条件下的装备
        cards: list[tuple[dict, str]] = []
        for group_key, items in self._bag_items.items():
            if (self._current_filter is not None
                    and group_key != self._current_filter):
                continue
            part_label = _GROUP_PART_LABELS.get(group_key, group_key)
            for _fp, equip in items.items():
                cards.append((equip, part_label))

        # 填充网格
        for i, (equip, part_label) in enumerate(cards):
            card = _CompactEquipCard(self._display_params)
            card.set_equip(equip, part_label)
            self._grid.addWidget(card, i // cols, i % cols)

        if not cards:
            placeholder = QLabel(tr("暂无数据"))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color: #999; font-size: 14px; padding: 40px;")
            self._grid.addWidget(placeholder, 0, 0, 1, cols)

    def refresh(self, user_name: str):
        """从 session 加载背包数据并刷新"""
        from lvjiang.core.config import SessionManager, load_equip_display

        self._display_params = load_equip_display()
        self._bag_items = {}
        if not user_name:
            self._rebuild_grid()
            return
        try:
            data = SessionManager().load(user_name)
            bag = data.get("bag_items", {})
            if isinstance(bag, dict):
                self._bag_items = bag
        except Exception as e:
            logger.error(f"加载背包数据失败: {e}")
        self._rebuild_grid()


class EquipStatusTab(QWidget):
    """装备数据 Tab —— 包含「当前装备」和「其他装备」两个子面板。"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()
        self._refresh_current()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 刷新按钮
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新装备数据"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        add_user_nav_buttons(btn_row, self._host)
        btn_row.addStretch()
        btn_export = QPushButton(tr("导出数据"))
        btn_export.setToolTip(tr("导出为 leoq7 格式"))
        btn_export.setFixedWidth(80)
        btn_export.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_export.clicked.connect(self._on_export)
        btn_row.addWidget(btn_export)
        layout.addLayout(btn_row)

        # 子 Tab
        self._sub_tabs = QTabWidget()
        layout.addWidget(self._sub_tabs, stretch=1)

        # 当前装备 —— 复用 EquipStatusPanel
        self._current_equip_panel = EquipStatusPanel()
        self._sub_tabs.addTab(self._current_equip_panel, tr("当前装备"))

        # 其他装备 —— 六列网格 + 部位筛选
        self._other_equip_page = _OtherEquipPage()
        self._sub_tabs.addTab(self._other_equip_page, tr("其他装备"))

        # 订阅宿主用户切换
        self._host.user_changed.connect(lambda _name: self._refresh_current())

    def _on_refresh(self):
        """刷新当前激活的子面板"""
        self._refresh_current()

    def _on_export(self):
        """导出当前用户的全部装备数据为 leoq7 格式"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("导出失败"), tr("没有激活的用户"))
            return
        try:
            from lvjiang.core.config import SessionManager

            from ..leoq7_export import export_leoq7
            data = SessionManager().load(user_name)
            text = export_leoq7(data, user_name)
        except Exception as e:
            logger.error(f"导出 leoq7 数据失败: {e}")
            QMessageBox.critical(self, tr("导出失败"), str(e))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出装备数据"),
            f"{user_name}_leoq7.txt",
            tr("文本文件 (*.txt)"),
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            QMessageBox.information(
                self, tr("导出成功"),
                tr("已导出到\n{path}").format(path=path),
            )

    def _refresh_current(self):
        """从当前用户加载数据并刷新两个面板"""
        from lvjiang.core.config import load_equip_display
        dp = load_equip_display()
        self._current_equip_panel.set_display_params(dp)

        user_name = self._host.active_user_name()
        if not user_name:
            self._current_equip_panel.refresh({})
            self._other_equip_page.refresh("")
            return
        try:
            from lvjiang.core.config import SessionManager
            data = SessionManager().load(user_name)
            equipped = data.get("equipped", {})
            self._current_equip_panel.refresh(equipped)
        except Exception as e:
            logger.error(f"加载用户装备数据失败: {e}")
            self._current_equip_panel.refresh({})
        self._other_equip_page.refresh(user_name)
