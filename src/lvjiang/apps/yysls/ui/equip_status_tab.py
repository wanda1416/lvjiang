"""燕云「装备数据」Tab —— 装备背包统一视图。

顶部 8 个可点击槽位（固定 2×4），下方全部装备网格（可配置列数）。
点击槽位触发部位筛选，再次点击取消选中。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from .mock_equip_dialog import MockEquipDialog
from .profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from .profile.tab import add_user_nav_buttons

# 品质颜色映射（适配浅色背景）
_QUALITY_COLORS = {
    "gold": "#B8860B",
    "purple": "#8B5CF6",
    "blue": "#2563EB",
    "green": "#16A34A",
    None: "#999999",
}

# 状态展示行样式（与角色详情毕业率卡片一致）
_STATUS_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_STATUS_VALUE_STYLE = "font-size: 15px; font-weight: 600;"
_STATUS_YELLOW_VALUE_STYLE = "font-size: 15px; font-weight: 600; color: #D97706;"

# 顶部槽位布局（固定 2×4）
# (row, col, slot_key, display_name, filter_type)
# filter_type 对应 bag_items 的分组 key；主副武器共享 "weapon"
_SLOT_LAYOUT = [
    (0, 0, "main_weapon", tr("主武器"), "weapon"),
    (0, 1, "sub_weapon", tr("副武器"), "weapon"),
    (0, 2, "head", tr("冠胄"), "head"),
    (0, 3, "chest", tr("胸甲"), "chest"),
    (1, 0, "ring", tr("环"), "ring"),
    (1, 1, "pendant", tr("佩"), "pendant"),
    (1, 2, "leg", tr("胫甲"), "leg"),
    (1, 3, "wrist", tr("腕甲"), "wrist"),
]

# 部位显示名（bag_items 分组 key → 卡片标签）—— 使用 gc.get_group_to_part() 替代
# 保留此常量作为 fallback，实际运行时优先用 GameConfigManager
_GROUP_PART_LABELS: dict[str, str] = {}

_GRID_COLS = 4  # 默认值，实际从 settings.equip_display.grid_columns 读取

# 品质背景色（半透明，仅金/紫）
_QUALITY_BG_COLORS = {
    "gold": "rgba(210, 179, 102, 0.25)",
    "purple": "rgba(113, 102, 120, 0.25)",
}

# 槽位卡片样式
_SLOT_STYLE_EMPTY = (
    "_SlotCard { background-color: #f0f0f0; border: 2px dashed #ccc; "
    "border-radius: 6px; }"
)


def _slot_style_normal(bg: str = "#f8f9fa", border: str = "#dee2e6") -> str:
    return (
        f"_SlotCard {{ background-color: {bg}; border: 2px solid {border}; "
        "border-radius: 6px; }"
    )


def _slot_style_selected(bg: str = "#e8f5e9") -> str:
    return (
        f"_SlotCard {{ background-color: {bg}; border: 2px solid #607D8B; "
        "border-radius: 7px; }"
    )


def _slot_style_hovered(bg: str = "#f8f9fa") -> str:
    return (
        f"_SlotCard {{ background-color: {bg}; border: 2px solid #90A4AE; "
        "border-radius: 7px; }"
    )


def _affix_value_color(cap_pct: int | float | None) -> str:
    """词条数值颜色：>=90 金，[70,90) 紫，<70 蓝"""
    if cap_pct is None:
        return "#333"
    if cap_pct >= 90:
        return "#B8860B"
    if cap_pct >= 70:
        return "#8B5CF6"
    return "#2563EB"


# ── 标签样式 ──────────────────────────────────────────

_TAG_STYLE = (
    "color: white; border-radius: 8px; "
    "font-size: 11px; font-weight: 600; padding: 2px 7px;"
)


def _make_tag(text: str, bg: str = "#607D8B", parent=None) -> QLabel:
    """创建标准标签胶囊（用于 name_row 的标签序列）。"""
    lbl = QLabel(text, parent)
    lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    lbl.setStyleSheet(f"background-color: {bg}; {_TAG_STYLE}")
    return lbl


class _StatusTagBar(QWidget):
    """名称行右侧的通用多状态标签容器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._tags: dict[str, QLabel] = {}

    def define(self, key: str, text: str, bg: str = "#607D8B") -> None:
        label = _make_tag(text, bg, self)
        label.setVisible(False)
        self._tags[key] = label
        self._layout.addWidget(label)

    def set_visible(self, key: str, visible: bool) -> None:
        self._tags[key].setVisible(visible)

    def is_visible(self, key: str) -> bool:
        return not self._tags[key].isHidden()


# ── 顶部：可点击槽位卡片 ──────────────────────────────


class _SlotCard(QFrame):
    """可点击的装备槽位卡片，支持选中/取消选中"""

    def __init__(
        self,
        slot_key: str,
        display_name: str,
        filter_type: str,
        display_params: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.slot_key = slot_key
        self.filter_type = filter_type
        self._selected = False
        self._hovered = False
        self._display_name = display_name
        self._equip_data: dict = {}

        dp = display_params or {}
        self._name_fs = dp.get("name_font_size", 13)
        self._level_fs = dp.get("level_font_size", 12)
        self._affix_fs = dp.get("affix_font_size", 11)
        self._card_h = dp.get("card_min_height", 160)

        self.setFixedHeight(self._card_h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._quality_bg: str | None = None
        self._apply_style(_SLOT_STYLE_EMPTY)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # 槽位名 + 标签序列
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.lbl_name = QLabel(display_name)
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px; color: #333;")
        header.addWidget(self.lbl_name, stretch=1)
        self.status_tags = _StatusTagBar()
        self.status_tags.define("filtered", tr("筛选中"))
        self.status_tags.define("mock", tr("模拟"), "#7E57C2")
        header.addWidget(self.status_tags)
        layout.addLayout(header)

        # 等级行
        self.lbl_info = QLabel("")
        self.lbl_info.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #999;")
        layout.addWidget(self.lbl_info)

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

    # ── 选中态 ──

    def set_selected(self, selected: bool):
        self._selected = selected
        self.status_tags.set_visible("filtered", selected)
        bg = self._quality_bg or "#f8f9fa"
        if selected:
            self._apply_style(_slot_style_selected(bg))
        elif self.lbl_info.text() == tr("未装备"):
            self._apply_style(_SLOT_STYLE_EMPTY)
        else:
            border = "#b0a080" if self._quality_bg else "#dee2e6"
            self._apply_style(_slot_style_normal(bg, border))

    def is_selected(self) -> bool:
        return self._selected

    def _apply_style(self, style: str):
        self.setStyleSheet(style)

    def enterEvent(self, event):
        self._hovered = True
        if not self._selected:
            self._apply_style(_slot_style_hovered(self._quality_bg or "#f8f9fa"))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        if not self._selected:
            if self.lbl_info.text() == tr("未装备"):
                self._apply_style(_SLOT_STYLE_EMPTY)
            else:
                border = "#b0a080" if self._quality_bg else "#dee2e6"
                self._apply_style(_slot_style_normal(
                    self._quality_bg or "#f8f9fa", border
                ))
        super().leaveEvent(event)

    # ── 点击事件 ──

    def mousePressEvent(self, event):
        # 仅左键触发部位筛选，右键留给 contextMenuEvent
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_slot_clicked(self.slot_key)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：已装备物品可卸载，模拟装备可编辑。"""
        if not getattr(self, '_equip_data', None):
            event.ignore()
            return
        is_mock = (
            self._equip_data.get("_extra", {})
            .get("is_mock", False)
        )
        menu = QMenu(self)
        unequip_action = menu.addAction(tr("卸载"))
        if is_mock:
            edit_action = menu.addAction(tr("编辑"))
        action = menu.exec(event.globalPos())
        if action == unequip_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_slot_unequip(self.slot_key)
        elif is_mock and action == edit_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_slot_edit(self.slot_key)
        event.accept()

    # ── 数据填充 ──

    def set_empty(self):
        self._quality_bg = None
        self._equip_data = {}
        self.status_tags.set_visible("mock", False)
        self.lbl_name.setText(self._display_name)
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px; color: #333;")
        self.lbl_info.setText(tr("未装备"))
        self.lbl_info.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #999;")
        self._clear_affixes()
        if not self._selected:
            self._apply_style(_SLOT_STYLE_EMPTY)

    def set_equip(self, equip_data: dict):
        self._equip_data = equip_data
        self.status_tags.set_visible(
            "mock", bool(equip_data.get("_extra", {}).get("is_mock", False)),
        )
        quality = equip_data.get("quality") or ""
        color = _QUALITY_COLORS.get(quality, "#888888")
        self._quality_bg = _QUALITY_BG_COLORS.get(quality)

        name = equip_data.get("name", tr("未知"))
        self.lbl_name.setText(f"{self._display_name} · {name}")
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px; color: {color};")

        level = equip_data.get("level") or "?"
        is_chengyin = equip_data.get("is_chengyin", False)
        tag = " [" + tr("承音") + "]" if is_chengyin else ""
        self.lbl_info.setText(f"Lv{level}{tag}")
        self.lbl_info.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #666; font-weight: bold;")

        if not self._selected:
            border = "#b0a080" if self._quality_bg else "#dee2e6"
            bg = self._quality_bg or "#f8f9fa"
            self._apply_style(_slot_style_normal(bg, border))

        # 词条
        self._clear_affixes()
        for i in range(1, 6):
            affix = equip_data.get(f"affix_{i}")
            if not affix or not affix.get("name"):
                continue
            self._add_affix_row(affix)

        # 定音
        dingyin = equip_data.get("dingyin")
        if dingyin and dingyin.get("name"):
            dash = QFrame()
            dash.setFrameShape(QFrame.Shape.NoFrame)
            dash.setStyleSheet(
                "border: none; border-top: 1px dashed #adb5bd;")
            dash.setFixedHeight(1)
            self.affix_layout.addWidget(dash)
            self._add_affix_row(dingyin)

    def _add_affix_row(self, affix: dict):
        value = affix.get("value", "")
        unit = affix.get("unit", "")
        cap_pct = affix.get("cap_pct")

        if isinstance(value, (int, float)):
            val_str = f"{value}%" if unit == "%" else (
                f"{value:.1f}" if isinstance(value, float) else str(value))
        else:
            val_str = str(value)

        val_color = _affix_value_color(cap_pct)

        is_transferred = affix.get("is_transferred", False)
        transfer_mark = " ⟳" if is_transferred else ""

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)

        lbl = QLabel(f"{affix['name']}{transfer_mark}")
        lbl.setStyleSheet(
            f"font-size: {self._affix_fs}px; color: #555; font-weight: bold;")
        row.addWidget(lbl, stretch=1)

        val = QLabel(val_str)
        val.setStyleSheet(
            f"font-size: {self._affix_fs}px; color: {val_color}; font-weight: bold;")
        row.addWidget(val, alignment=Qt.AlignmentFlag.AlignRight)

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


# ── 底部：背包装备卡片 ──────────────────────────────


class _CompactEquipCard(QFrame):
    """紧凑装备卡片 —— 用于背包网格

    Signals:
        equip_requested(dict, str): 请求装备到槽位，参数为 (equip_data, group_key)
        edit_requested(dict, str): 请求编辑模拟装备，参数为 (equip_data, group_key)
        delete_requested(dict, str): 请求删除装备，参数为 (equip_data, group_key)
    """

    equip_requested = pyqtSignal(dict, str)
    edit_requested = pyqtSignal(dict, str)
    delete_requested = pyqtSignal(dict, str)

    def __init__(self, display_params: dict | None = None, parent=None):
        super().__init__(parent)
        dp = display_params or {}
        self._name_fs = dp.get("name_font_size", 13)
        self._level_fs = dp.get("level_font_size", 12)
        self._affix_fs = dp.get("affix_font_size", 11)

        # 装备数据和分组 key（用于右键菜单）
        self._equip_data: dict = {}
        self._group_key: str = ""
        self._quality_bg: str | None = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_card_style()
        self.setFixedHeight(dp.get("card_min_height", 180))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("右键菜单"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # 装备名 + 标签序列
        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(6)
        self.lbl_name = QLabel()
        self.lbl_name.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px;")
        name_row.addWidget(self.lbl_name, stretch=1)
        self.status_tags = _StatusTagBar()
        self.status_tags.define("mock", tr("模拟"), "#7E57C2")
        name_row.addWidget(self.status_tags)
        layout.addLayout(name_row)

        self.lbl_level = QLabel()
        self.lbl_level.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.lbl_level.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #666;")
        layout.addWidget(self.lbl_level)

        line = QFrame()
        line.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #dee2e6;")
        line.setFixedHeight(1)
        layout.addWidget(line)

        self.affix_container = QWidget()
        self.affix_container.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.affix_layout = QVBoxLayout(self.affix_container)
        self.affix_layout.setContentsMargins(0, 0, 0, 0)
        self.affix_layout.setSpacing(2)
        layout.addWidget(self.affix_container)

        layout.addStretch()

    def _apply_card_style(self, hovered: bool = False):
        border = "#78909C" if hovered else "#dee2e6"
        width = 2 if hovered else 1
        bg = self._quality_bg or "#f8f9fa"
        self.setStyleSheet(f"""
            _CompactEquipCard {{
                background-color: {bg};
                border: {width}px solid {border};
                border-radius: 6px;
                padding: 4px;
            }}
        """)

    def enterEvent(self, event):
        self._apply_card_style(hovered=True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_card_style(hovered=False)
        super().leaveEvent(event)

    def contextMenuEvent(self, event):
        """右键菜单：所有装备卡片均弹出。"""
        if not self._equip_data:
            event.ignore()
            return
        self._show_context_menu(event.globalPos())
        event.accept()

    def _show_context_menu(self, global_pos):
        """显示右键菜单：装备/编辑/删除。"""
        is_mock = (
            self._equip_data.get("_extra", {})
            .get("is_mock", False)
        )
        menu = QMenu(self)
        equip_action = menu.addAction(tr("装备"))
        equip_action.setToolTip(tr("穿戴到对应槽位"))
        if is_mock:
            edit_action = menu.addAction(tr("编辑"))
            edit_action.setToolTip(tr("编辑模拟装备数据"))
        delete_action = menu.addAction(tr("删除"))
        delete_action.setToolTip(tr("删除此装备"))

        action = menu.exec(global_pos)
        if action == equip_action:
            self.equip_requested.emit(self._equip_data, self._group_key)
        elif is_mock and action == edit_action:
            self.edit_requested.emit(self._equip_data, self._group_key)
        elif action == delete_action:
            self.delete_requested.emit(self._equip_data, self._group_key)

    def set_equip(
        self, equip_data: dict, part_label: str,
        group_key: str = "", is_mock: bool = False,
    ):
        # 存储装备数据和分组 key（用于右键菜单）
        self._equip_data = equip_data
        self._group_key = group_key

        quality = equip_data.get("quality") or ""
        color = _QUALITY_COLORS.get(quality, "#888888")
        self._quality_bg = _QUALITY_BG_COLORS.get(quality)
        self._apply_card_style()

        name = equip_data.get("name", tr("未知"))
        self.lbl_name.setText(f"{part_label} · {name}")
        self.lbl_name.setStyleSheet(
            f"font-weight: bold; font-size: {self._name_fs}px; color: {color};")
        self.status_tags.set_visible(
            "mock",
            bool(is_mock or equip_data.get("_extra", {}).get("is_mock", False)),
        )

        level = equip_data.get("level") or "?"
        is_chengyin = equip_data.get("is_chengyin", False)
        tag = " [" + tr("承音") + "]" if is_chengyin else ""
        self.lbl_level.setText(f"Lv{level}{tag}")
        self.lbl_level.setStyleSheet(
            f"font-size: {self._level_fs}px; color: #666; font-weight: bold;")

        self._clear_affixes()
        for i in range(1, 6):
            affix = equip_data.get(f"affix_{i}")
            if not affix or not affix.get("name"):
                continue

            value = affix.get("value", "")
            unit = affix.get("unit", "")
            cap_pct = affix.get("cap_pct")

            if isinstance(value, (int, float)):
                val_str = f"{value}%" if unit == "%" else (
                    f"{value:.1f}" if isinstance(value, float) else str(value))
            else:
                val_str = str(value)

            val_color = _affix_value_color(cap_pct)

            is_transferred = affix.get("is_transferred", False)
            transfer_mark = " ⟳" if is_transferred else ""

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)

            lbl_name = QLabel(f"{affix['name']}{transfer_mark}")
            lbl_name.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: #555; font-weight: bold;")
            row.addWidget(lbl_name, stretch=1)

            lbl_val = QLabel(val_str)
            lbl_val.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: {val_color}; font-weight: bold;")
            row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

            self.affix_layout.addLayout(row)

        # 定音
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
                f"{dy_value}%" if isinstance(dy_value, (int, float))
                else str(dy_value))
            dy_cap_pct = dingyin.get("cap_pct")
            dy_color = _affix_value_color(dy_cap_pct)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(2)

            lbl_name = QLabel(dingyin["name"])
            lbl_name.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: #555; font-weight: bold;")
            row.addWidget(lbl_name, stretch=1)

            lbl_val = QLabel(dy_val_str)
            lbl_val.setStyleSheet(
                f"font-size: {self._affix_fs}px; color: {dy_color}; font-weight: bold;")
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


# ── 主 Tab ──────────────────────────────────────────


class EquipStatusTab(QWidget):
    """装备数据 Tab —— 装备背包统一视图。

    顶部 8 个可点击槽位（固定 2×4），下方全部装备网格。
    点击槽位触发部位筛选，再次点击取消选中。
    """

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._equipped: dict = {}
        self._bag_items: dict = {}
        self._mock_items: dict = {}
        self._display_params: dict = {}
        self._selected_slot: str | None = None
        self._slot_cards: dict[str, _SlotCard] = {}
        self._setup_ui()
        self._refresh_all()
        # 订阅装备变更信号，更新状态展示行
        self._host.equipment_changed.connect(self._update_status_row)
        self._host.graduation_updated.connect(self._update_status_row)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 按钮栏 ──
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新装备数据"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        add_user_nav_buttons(btn_row, self._host)
        btn_row.addStretch()

        # 模拟装备按钮
        btn_mock = QPushButton(tr("模拟装备▼"))
        btn_mock.setToolTip(tr("创建/管理模拟装备"))
        btn_mock.setFixedWidth(90)
        btn_mock.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_mock.clicked.connect(self._on_mock_menu)
        btn_row.addWidget(btn_mock)

        # ── 筛选下拉框 ──
        # 类型筛选（与槽位按钮双向联动）
        lbl_type = QLabel(tr("类型"))
        lbl_type.setStyleSheet("font-size: 12px; color: #555;")
        btn_row.addWidget(lbl_type)
        self._type_filter = QComboBox()
        self._type_filter.addItem(tr("全部"), "all")
        for sk, dn, _ in [
            ("main_weapon", tr("主武器"), "weapon"),
            ("sub_weapon", tr("副武器"), "weapon"),
            ("ring", tr("环"), "ring"),
            ("pendant", tr("佩"), "pendant"),
            ("head", tr("冠胄"), "head"),
            ("chest", tr("胸甲"), "chest"),
            ("leg", tr("胫甲"), "leg"),
            ("wrist", tr("腕甲"), "wrist"),
        ]:
            self._type_filter.addItem(dn, sk)
        self._type_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._type_filter.currentIndexChanged.connect(self._on_filter_changed)
        btn_row.addWidget(self._type_filter)

        lbl_level = QLabel(tr("等级"))
        lbl_level.setStyleSheet("font-size: 12px; color: #555;")
        btn_row.addWidget(lbl_level)
        self._level_filter = QComboBox()
        self._level_filter.addItem(tr("全部"), "all")
        # 从游戏配置动态填充等级（降序）
        from lvjiang.apps.yysls.config import get_game_config
        for lvl in sorted([c.level for c in get_game_config().get_level_configs()], reverse=True):
            self._level_filter.addItem(tr("≥{level}").format(level=lvl), str(lvl))
        self._level_filter.setMinimumWidth(70)
        self._level_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._level_filter.currentIndexChanged.connect(self._on_filter_changed)
        btn_row.addWidget(self._level_filter)

        lbl_affix = QLabel(tr("词条"))
        lbl_affix.setStyleSheet("font-size: 12px; color: #555;")
        btn_row.addWidget(lbl_affix)
        self._affix_filter = QComboBox()
        self._affix_filter.addItem(tr("全部"), "all")
        self._affix_filter.addItem(tr("定音"), "dingyin")
        self._affix_filter.addItem(tr("满调律"), "full_tuning")
        self._affix_filter.setFixedWidth(70)
        self._affix_filter.currentIndexChanged.connect(self._on_filter_changed)
        btn_row.addWidget(self._affix_filter)

        btn_export = QPushButton(tr("导出数据"))
        btn_export.setToolTip(tr("导出为 leoq7 格式"))
        btn_export.setFixedWidth(80)
        btn_export.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_export.clicked.connect(self._on_export)
        btn_row.addWidget(btn_export)
        layout.addLayout(btn_row)

        # ── 状态展示行：DPS / 毕业率（复用角色详情配置） ──
        # 与下方战斗属性卡片同结构：左右两栏各含两列，当前占前两列
        status_root = QWidget()
        status_root.setFixedHeight(32)
        status_columns = QHBoxLayout(status_root)
        status_columns.setContentsMargins(8, 2, 8, 4)
        status_columns.setSpacing(10)

        # 左栏：DPS（列 0-1）+ 毕业率（列 2-3 中的列 2）
        status_left = QWidget()
        status_grid = QGridLayout(status_left)
        status_grid.setContentsMargins(0, 0, 0, 0)
        status_grid.setHorizontalSpacing(20)
        status_grid.setColumnStretch(0, 1)
        status_grid.setColumnStretch(1, 1)

        # DPS（第 0 列）
        dps_layout = QHBoxLayout()
        dps_layout.setContentsMargins(0, 0, 0, 0)
        dps_layout.setSpacing(8)
        dps_name = QLabel(tr("DPS"))
        dps_name.setStyleSheet(_STATUS_NAME_STYLE)
        self._status_dps = QLabel("--")
        self._status_dps.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_dps.setMinimumWidth(80)
        self._status_dps.setStyleSheet(_STATUS_VALUE_STYLE)
        dps_layout.addWidget(dps_name)
        dps_layout.addStretch()
        dps_layout.addWidget(self._status_dps)
        status_grid.addLayout(dps_layout, 0, 0)

        # 毕业率（第 1 列）
        rate_layout = QHBoxLayout()
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.setSpacing(8)
        rate_name = QLabel(tr("毕业率"))
        rate_name.setStyleSheet(_STATUS_NAME_STYLE)
        self._status_graduation = QLabel("--")
        self._status_graduation.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._status_graduation.setMinimumWidth(80)
        self._status_graduation.setStyleSheet(_STATUS_YELLOW_VALUE_STYLE)
        rate_layout.addWidget(rate_name)
        rate_layout.addStretch()
        rate_layout.addWidget(self._status_graduation)
        status_grid.addLayout(rate_layout, 0, 1)

        status_columns.addWidget(status_left, stretch=1)

        # 右栏：留空，未来补充更多数据
        status_right = QWidget()
        status_columns.addWidget(status_right, stretch=1)

        layout.addWidget(status_root)

        # ── 滚动区域：槽位 + 背包网格统一滚动 ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 始终保留垂直滚动条槽位，筛选结果变少时页面宽度不跳动。
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn)

        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.setSpacing(0)

        # 顶部：8 个可点击槽位（固定 2×4）
        self._slot_container = QWidget()
        self._slot_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        slot_grid = QGridLayout(self._slot_container)
        slot_grid.setSpacing(8)
        slot_grid.setContentsMargins(8, 8, 8, 8)

        for row, col, slot_key, display_name, _filter_type in _SLOT_LAYOUT:
            card = _SlotCard(slot_key, display_name, _filter_type)
            slot_grid.addWidget(card, row, col)
            self._slot_cards[slot_key] = card

        wrapper_layout.addWidget(self._slot_container)

        # 分割线 —— 区分可点击槽位区与背包区
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(
            "background-color: #ccc; max-height: 1px; margin: 4px 8px;")
        sep.setFixedHeight(1)
        wrapper_layout.addWidget(sep)

        # 背包网格
        self._grid_container = QWidget()
        self._grid_container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._grid = QGridLayout(self._grid_container)
        self._grid.setSpacing(8)
        self._grid.setContentsMargins(8, 8, 8, 8)

        wrapper_layout.addWidget(self._grid_container)
        wrapper_layout.addStretch()
        scroll.setWidget(wrapper)
        layout.addWidget(scroll, stretch=1)

        # 订阅用户切换
        self._host.user_changed.connect(lambda _name: self._refresh_all())

        # 加载筛选配置
        self._load_filter_settings()

    # ── 筛选 ──

    def _load_filter_settings(self):
        """从 session 加载筛选配置并设置下拉框"""
        from ....core.config import load_equip_filter
        filters = load_equip_filter()
        # 屏蔽信号，避免初始化时触发 _on_filter_changed
        self._type_filter.blockSignals(True)
        self._level_filter.blockSignals(True)
        self._affix_filter.blockSignals(True)
        try:
            # 类型筛选 → 联动槽位选中态
            type_data = filters.get("type", "all")
            type_idx = self._type_filter.findData(type_data)
            self._type_filter.setCurrentIndex(type_idx if type_idx >= 0 else 0)
            if type_data != "all":
                self._selected_slot = type_data
                for key, card in self._slot_cards.items():
                    card.set_selected(key == self._selected_slot)
            # 等级筛选
            level_idx = self._level_filter.findData(filters.get("level", "all"))
            self._level_filter.setCurrentIndex(level_idx if level_idx >= 0 else 0)
            # 词条筛选
            affix_idx = self._affix_filter.findData(filters.get("affix", "all"))
            self._affix_filter.setCurrentIndex(affix_idx if affix_idx >= 0 else 0)
        finally:
            self._type_filter.blockSignals(False)
            self._level_filter.blockSignals(False)
            self._affix_filter.blockSignals(False)

    def _save_filter_settings(self):
        """保存筛选配置到 session"""
        from ....core.config import save_equip_filter
        filters = {
            "type": self._type_filter.currentData(),
            "level": self._level_filter.currentData(),
            "affix": self._affix_filter.currentData(),
        }
        save_equip_filter(filters)

    def _on_filter_changed(self):
        """筛选下拉框变化时触发"""
        # 类型下拉框变化 → 联动槽位选中态
        type_data = self._type_filter.currentData()
        new_slot = type_data if type_data != "all" else None
        if new_slot != self._selected_slot:
            self._selected_slot = new_slot
            for key, card in self._slot_cards.items():
                card.set_selected(key == self._selected_slot)
        self._save_filter_settings()
        self._rebuild_grid()

    def _get_level_threshold(self) -> int:
        """获取等级筛选阈值，0 表示不筛选"""
        level_str = self._level_filter.currentData()
        return int(level_str) if level_str != "all" else 0

    def _get_affix_filter(self) -> str:
        """获取词条筛选类型: all/dingyin/full_tuning"""
        return self._affix_filter.currentData()

    def _equip_passes_filter(self, equip: dict) -> bool:
        """检查装备是否通过筛选条件"""
        # 等级筛选
        level_threshold = self._get_level_threshold()
        if level_threshold > 0:
            equip_level = equip.get("level") or 0
            if isinstance(equip_level, str):
                try:
                    equip_level = int(equip_level)
                except (ValueError, TypeError):
                    equip_level = 0
            if equip_level < level_threshold:
                return False

        # 词条筛选
        affix_filter = self._get_affix_filter()
        if affix_filter == "all":
            return True
        elif affix_filter == "dingyin":
            # 有定音词条（包含满调律）
            dingyin = equip.get("dingyin")
            return bool(dingyin and dingyin.get("name"))
        elif affix_filter == "full_tuning":
            # 满调律：5 条非定音词条（affix_1 到 affix_5 都有）
            return all(equip.get(f"affix_{i}", {}).get("name") for i in range(1, 6))
        return True

    # ── 槽位点击 ──

    def _on_slot_clicked(self, slot_key: str):
        if self._selected_slot == slot_key:
            # 再次点击同一槽位 → 取消选中
            self._selected_slot = None
        else:
            self._selected_slot = slot_key

        # 更新所有槽位的选中态
        for key, card in self._slot_cards.items():
            card.set_selected(key == self._selected_slot)

        # 联动类型下拉框
        self._type_filter.blockSignals(True)
        try:
            idx = self._type_filter.findData(slot_key if self._selected_slot else "all")
            self._type_filter.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._type_filter.blockSignals(False)

        self._rebuild_grid()

    def _on_slot_unequip(self, slot_key: str):
        """卸载槽位装备：从 equipped 移回 bag_items 或 mock_items"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("卸载失败"), tr("没有激活的用户"))
            return
        equip = self._equipped.get(slot_key)
        if not equip:
            return
        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)
            equipped = data.get("equipped", {})
            bag_items = data.get("bag_items", {})
            mock_items = data.get("mock_items", {})
            is_mock = equip.get("_extra", {}).get("is_mock", False)
            # 确定分组 key（使用全局映射，武器具体名称自动映射为 "weapon"）
            from ..config import get_game_config
            equip_type = equip.get("type", "")
            group_key = get_game_config().get_type_to_group().get(equip_type, "")
            if not group_key:
                logger.warning(f"卸载装备未知类型 '{equip_type}'，跳过回存")
            else:
                fp = equip.get("_fp", "")
                if not fp:
                    logger.warning(f"卸载装备缺少 _fp，跳过回存: {equip.get('name', '?')}")
                else:
                    # 移回对应存储
                    if is_mock:
                        mock_items.setdefault(group_key, {})[fp] = equip
                    else:
                        bag_items.setdefault(group_key, {})[fp] = equip
            # 从 equipped 移除
            equipped.pop(slot_key, None)
            data.update({
                "equipped": equipped,
                "bag_items": bag_items,
                "mock_items": mock_items,
            })
            mgr.save(user_name, data)
            self._equipped = equipped
            self._bag_items = bag_items
            self._mock_items = mock_items
            self._refresh_slots()
            self._rebuild_grid()
        except Exception as e:
            logger.error(f"卸载装备失败: {e}")
            QMessageBox.critical(self, tr("卸载失败"), str(e))

    def _on_slot_edit(self, slot_key: str):
        """编辑槽位中的模拟装备"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("编辑失败"), tr("没有激活的用户"))
            return
        equip = self._equipped.get(slot_key)
        if not equip:
            return
        dialog = MockEquipDialog(equip, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return
        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)
            equipped = data.get("equipped", {})
            # 更新 equipped 中的条目
            equipped[slot_key] = result
            data["equipped"] = equipped
            mgr.save(user_name, data)
            self._equipped = equipped
            self._refresh_slots()
            self._rebuild_grid()
        except Exception as e:
            logger.error(f"编辑槽位模拟装备失败: {e}")
            QMessageBox.critical(self, tr("编辑失败"), str(e))

    # ── 背包网格 ──

    def _rebuild_grid(self):
        cols = self._display_params.get("grid_columns", _GRID_COLS)

        # 清空
        while self._grid.count() > 0:
            item = self._grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        for c in range(self._grid.columnCount()):
            self._grid.setColumnStretch(c, 0)
        for c in range(cols):
            self._grid.setColumnStretch(c, 1)

        # 确定筛选部位
        filter_type = None
        if self._selected_slot:
            for _, _, sk, _, ft in _SLOT_LAYOUT:
                if sk == self._selected_slot:
                    filter_type = ft
                    break

        # 收集装备（bag_items + mock_items）
        from ..config import get_game_config
        group_to_part = get_game_config().get_group_to_part()
        cards: list[tuple[dict, str, str, bool]] = []
        for group_key, items in self._bag_items.items():
            if filter_type is not None and group_key != filter_type:
                continue
            part_label = group_to_part.get(group_key, group_key)
            for _fp, equip in items.items():
                # 应用等级/词条筛选
                if not self._equip_passes_filter(equip):
                    continue
                cards.append((equip, part_label, group_key, False))

        # 合并模拟装备
        for group_key, items in self._mock_items.items():
            if filter_type is not None and group_key != filter_type:
                continue
            part_label = group_to_part.get(group_key, group_key)
            for _fp, equip in items.items():
                if not self._equip_passes_filter(equip):
                    continue
                cards.append((equip, part_label, group_key, True))

        # 填充
        for i, (equip, part_label, group_key, is_mock) in enumerate(cards):
            card = _CompactEquipCard(self._display_params)
            card.set_equip(equip, part_label, group_key, is_mock=is_mock)
            card.equip_requested.connect(self._on_equip_requested)
            card.edit_requested.connect(self._on_mock_edit_requested)
            card.delete_requested.connect(self._on_delete_requested)
            self._grid.addWidget(card, i // cols, i % cols)

        if not cards:
            placeholder = QLabel(tr("暂无数据"))
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet(
                "color: #999; font-size: 14px; padding: 40px;")
            self._grid.addWidget(placeholder, 0, 0, 1, cols)

    # ── 数据刷新 ──

    def _on_refresh(self):
        self._refresh_all()

    def _update_status_row(self):
        """更新状态展示行：从角色详情 Tab 读取 DPS 和毕业率。"""
        from .combat_attrs_tab import CombatAttrsTab
        for child in self._host.findChildren(QWidget):
            if isinstance(child, CombatAttrsTab):
                self._status_dps.setText(child._dps_value.text())
                self._status_graduation.setText(child._graduation_value.text())
                self._status_dps.setToolTip(child._dps_value.toolTip())
                self._status_graduation.setToolTip(
                    child._graduation_value.toolTip())
                return
        self._status_dps.setText("--")
        self._status_graduation.setText("--")

    def _refresh_all(self):
        from lvjiang.core.config import load_equip_display

        self._display_params = load_equip_display()

        user_name = self._host.active_user_name()
        if not user_name:
            self._equipped = {}
            self._bag_items = {}
            self._mock_items = {}
            self._refresh_slots()
            self._rebuild_grid()
            return

        try:
            from lvjiang.core.config import SessionManager
            data = SessionManager().load(user_name)
            self._equipped = data.get("equipped", {})
            bag = data.get("bag_items", {})
            self._bag_items = bag if isinstance(bag, dict) else {}
            self._mock_items = data.get("mock_items", {})
        except Exception as e:
            logger.error(f"加载装备数据失败: {e}")
            self._equipped = {}
            self._bag_items = {}
            self._mock_items = {}

        self._refresh_slots()
        self._rebuild_grid()
        self._update_status_row()

    def _refresh_slots(self):
        dp = self._display_params
        for _row, _col, slot_key, _display_name, _filter_type in _SLOT_LAYOUT:
            card = self._slot_cards[slot_key]
            card._name_fs = dp.get("name_font_size", 13)
            card._level_fs = dp.get("level_font_size", 12)
            card._affix_fs = dp.get("affix_font_size", 11)
            card._card_h = dp.get("card_min_height", 160)
            card.setFixedHeight(card._card_h)

            equip = self._equipped.get(slot_key)
            if equip:
                card.set_equip(equip)
            else:
                card.set_empty()
            # 保持选中态
            card.set_selected(slot_key == self._selected_slot)

    # ── 装备操作 ──

    def _on_equip_requested(self, equip_data: dict, group_key: str):
        """处理装备请求：将背包/模拟中的装备穿戴到对应槽位"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("装备失败"), tr("没有激活的用户"))
            return

        new_fp = equip_data.get("_fp", "")
        if not new_fp:
            QMessageBox.warning(self, tr("装备失败"), tr("装备数据缺少 _fp 字段"))
            return

        # 确定目标槽位
        target_slots = self._get_slots_for_group(group_key)
        if not target_slots:
            logger.error(f"无法找到 {group_key} 对应的槽位")
            return

        # 如果是武器，需要选择主/副武器
        if len(target_slots) > 1:
            from PyQt6.QtWidgets import QInputDialog
            slot_names = [tr("主武器") if s == "main_weapon" else tr("副武器") for s in target_slots]
            choice, ok = QInputDialog.getItem(
                self, tr("选择槽位"), tr("请选择要穿戴到的槽位:"),
                slot_names, 0, False
            )
            if not ok:
                return
            target_slot = target_slots[slot_names.index(choice)]
        else:
            target_slot = target_slots[0]

        is_new_mock = equip_data.get("_extra", {}).get("is_mock", False)

        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)

            equipped = data.get("equipped", {})
            bag_items = data.get("bag_items", {})
            mock_items = data.get("mock_items", {})

            # 获取当前槽位的装备（如果有）
            current_equipped = equipped.get(target_slot)

            # 将当前装备移回对应存储（如果有）
            if current_equipped:
                current_fp = current_equipped.get("_fp", "")
                if current_fp:
                    is_current_mock = (
                        current_equipped.get("_extra", {})
                        .get("is_mock", False)
                    )
                    if is_current_mock:
                        # 模拟装备移回 mock_items
                        if group_key not in mock_items:
                            mock_items[group_key] = {}
                        mock_items[group_key][current_fp] = current_equipped
                    else:
                        # 真实装备移回 bag_items
                        if group_key not in bag_items:
                            bag_items[group_key] = {}
                        bag_items[group_key][current_fp] = current_equipped

            # 从对应存储中移除新装备
            if is_new_mock:
                if group_key in mock_items and new_fp in mock_items[group_key]:
                    del mock_items[group_key][new_fp]
                    if not mock_items[group_key]:
                        del mock_items[group_key]
            else:
                if group_key in bag_items and new_fp in bag_items[group_key]:
                    del bag_items[group_key][new_fp]
                    if not bag_items[group_key]:
                        del bag_items[group_key]

            # 设置新装备到槽位
            equipped[target_slot] = equip_data

            # 保存数据
            data["equipped"] = equipped
            data["bag_items"] = bag_items
            data["mock_items"] = mock_items
            mgr.save(user_name, data)

            # 刷新显示
            self._equipped = equipped
            self._bag_items = bag_items
            self._mock_items = mock_items
            self._refresh_slots()
            self._rebuild_grid()

            # 通知战斗属性 Tab 刷新
            self._host.equipment_changed.emit()

            logger.info(f"已装备 {equip_data.get('name', '未知')} 到 {target_slot}")

        except Exception as e:
            logger.error(f"装备失败: {e}")
            QMessageBox.critical(self, tr("装备失败"), str(e))

    def _on_delete_requested(self, equip_data: dict, group_key: str):
        """处理删除请求：区分模拟/真实装备"""
        is_mock = equip_data.get("_extra", {}).get("is_mock", False)
        if is_mock:
            self._on_mock_delete_requested(equip_data, group_key)
        else:
            self._on_real_delete_requested(equip_data, group_key)

    def _on_real_delete_requested(self, equip_data: dict, group_key: str):
        """处理删除请求：从背包中删除装备"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("删除失败"), tr("没有激活的用户"))
            return

        fp = equip_data.get("_fp", "")
        if not fp:
            QMessageBox.warning(self, tr("删除失败"), tr("装备数据缺少 _fp 字段"))
            return

        # 二次确认
        equip_name = equip_data.get("name", tr("未知"))
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            tr("确定要从背包中删除【{name}】吗？\n此操作不可撤销。").format(name=equip_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)

            bag_items = data.get("bag_items", {})

            # 从背包中移除装备（直接用 _fp 作为 key）
            if group_key in bag_items and fp in bag_items[group_key]:
                del bag_items[group_key][fp]
                if not bag_items[group_key]:
                    del bag_items[group_key]

            # 保存数据
            data["bag_items"] = bag_items
            mgr.save(user_name, data)

            # 刷新显示
            self._bag_items = bag_items
            self._rebuild_grid()

            logger.info(f"已删除 {equip_name}")

        except Exception as e:
            logger.error(f"删除失败: {e}")
            QMessageBox.critical(self, tr("删除失败"), str(e))

    def _on_mock_delete_requested(self, equip_data: dict, group_key: str):
        """处理模拟装备删除请求"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("删除失败"), tr("没有激活的用户"))
            return

        fp = equip_data.get("_fp", "")
        if not fp:
            QMessageBox.warning(self, tr("删除失败"), tr("装备数据缺少 _fp 字段"))
            return

        equip_name = equip_data.get("name", tr("未知"))
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            tr("确定要删除模拟装备【{name}】吗？\n此操作不可撤销。").format(name=equip_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)
            mock_items = data.get("mock_items", {})

            if group_key in mock_items and fp in mock_items[group_key]:
                del mock_items[group_key][fp]
                if not mock_items[group_key]:
                    del mock_items[group_key]

            data["mock_items"] = mock_items
            mgr.save(user_name, data)

            self._mock_items = mock_items
            self._rebuild_grid()

            logger.info(f"已删除模拟装备 {equip_name}")

        except Exception as e:
            logger.error(f"删除失败: {e}")
            QMessageBox.critical(self, tr("删除失败"), str(e))

    def _on_mock_edit_requested(self, equip_data: dict, group_key: str):
        """处理模拟装备编辑请求"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("编辑失败"), tr("没有激活的用户"))
            return

        old_fp = equip_data.get("_fp", "")
        dialog = MockEquipDialog(equip_data, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return

        # 确定新类型和分组（使用全局映射）
        from ..config import get_game_config
        new_type = result.get("type", "")
        new_group_key = get_game_config().get_type_to_group().get(new_type, group_key)

        # _fp 已由 MockEquipDialog 自动计算（含 mock_ 前缀）
        new_fp = result["_fp"]

        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)
            mock_items = data.get("mock_items", {})

            # 移除旧条目
            if group_key in mock_items and old_fp in mock_items[group_key]:
                del mock_items[group_key][old_fp]
                if not mock_items[group_key]:
                    del mock_items[group_key]

            # 添加新条目
            if new_group_key not in mock_items:
                mock_items[new_group_key] = {}
            mock_items[new_group_key][new_fp] = result

            data["mock_items"] = mock_items
            mgr.save(user_name, data)

            self._mock_items = mock_items
            self._rebuild_grid()

            logger.info(f"已编辑模拟装备 {result.get('name', '未知')}")

        except Exception as e:
            logger.error(f"编辑模拟装备失败: {e}")
            QMessageBox.critical(self, tr("编辑失败"), str(e))

    def _on_mock_menu(self):
        """模拟装备下拉菜单"""
        sender = self.sender()
        if sender is None:
            return
        menu = QMenu(self)
        create_action = menu.addAction(tr("创建模拟装备"))
        action = menu.exec(sender.mapToGlobal(sender.rect().bottomLeft()))
        if action == create_action:
            self._on_mock_create()

    def _on_mock_create(self):
        """创建模拟装备"""
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("创建失败"), tr("没有激活的用户"))
            return

        dialog = MockEquipDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if not result:
            return

        # _fp 已由 MockEquipDialog 自动计算（含 mock_ 前缀）
        fp = result["_fp"]

        # 确定分组
        # 确定分组 key（使用全局映射）
        from ..config import get_game_config
        equip_type = result.get("type", "")
        group_key = get_game_config().get_type_to_group().get(equip_type, "ring")

        try:
            from lvjiang.core.config import SessionManager
            mgr = SessionManager()
            data = mgr.load(user_name)
            mock_items = data.get("mock_items", {})

            if group_key not in mock_items:
                mock_items[group_key] = {}
            mock_items[group_key][fp] = result

            data["mock_items"] = mock_items
            mgr.save(user_name, data)

            self._mock_items = mock_items
            self._rebuild_grid()

            logger.info(f"已创建模拟装备 {result.get('name', '未知')}")

        except Exception as e:
            logger.error(f"创建模拟装备失败: {e}")
            QMessageBox.critical(self, tr("创建失败"), str(e))

    def _get_slots_for_group(self, group_key: str) -> list[str]:
        """根据分组 key 获取对应的槽位 key 列表"""
        slots = []
        for _, _, slot_key, _, filter_type in _SLOT_LAYOUT:
            if filter_type == group_key:
                slots.append(slot_key)
        return slots

    # ── 导出 ──

    def _on_export(self):
        user_name = self._host.active_user_name()
        if not user_name:
            QMessageBox.warning(self, tr("导出失败"), tr("没有激活的用户"))
            return
        try:
            from lvjiang.core.config import SessionManager

            from .leoq7_export import export_leoq7
            data = SessionManager().load(user_name)
            text = export_leoq7(
                data,
                user_name,
                level_threshold=self._get_level_threshold(),
                affix_filter=self._get_affix_filter(),
            )
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
