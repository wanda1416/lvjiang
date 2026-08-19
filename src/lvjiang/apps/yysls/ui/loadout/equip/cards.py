"""装备卡片组件 —— 槽位卡片、背包装备卡片、状态标签栏。

从 status_tab.py 拆出，仅包含 UI 卡片组件及其依赖的常量/样式。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr

if TYPE_CHECKING:
    from .status_tab import EquipStatusTab

# 品质颜色映射（适配浅色背景）
_QUALITY_COLORS = {
    "gold": "#B8860B",
    "purple": "#8B5CF6",
    "blue": "#2563EB",
    "green": "#16A34A",
    None: "#999999",
}

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


# ── 状态标签栏 ──────────────────────────────────────────


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
        """右键菜单：已装备物品可卸载，模拟装备可编辑，所有可复制。"""
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
        copy_action = menu.addAction(tr("复制"))
        copy_action.setToolTip(tr("复制装备数据到创建对话框"))
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
        elif action == copy_action:
            parent = self.parent()
            while parent and not isinstance(parent, EquipStatusTab):
                parent = parent.parent()
            if parent:
                parent._on_copy_requested(self._equip_data, self._equip_data.get("_extra", {}).get("group_key", ""))
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

        # 词条平均百分比（内联在等级后面，字号跟随 affix_font_size）
        pct_fs = self._affix_fs
        cap_pcts = []
        for i in range(1, 6):
            affix = equip_data.get(f"affix_{i}")
            if affix and affix.get("name") and affix.get("cap_pct") is not None:
                cap_pcts.append(affix["cap_pct"])
        if cap_pcts:
            avg_pct = sum(cap_pcts) / len(cap_pcts)
            pct_color = _affix_value_color(avg_pct)
            pct_html = f'&nbsp;&nbsp;<span style="font-size:{pct_fs}px;color:{pct_color};font-weight:bold;">{avg_pct:.0f}%</span>'
            self.lbl_info.setTextFormat(Qt.TextFormat.RichText)
            self.lbl_info.setText(f"Lv{level}{tag}{pct_html}")
        else:
            self.lbl_info.setTextFormat(Qt.TextFormat.PlainText)
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
    copy_requested = pyqtSignal(dict, str)

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
        self.status_tags.define("loadout", tr("备战中"), "#00897B")
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
        """显示右键菜单：装备/编辑/复制/删除。"""
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
        copy_action = menu.addAction(tr("复制"))
        copy_action.setToolTip(tr("复制装备数据到创建对话框"))
        delete_action = menu.addAction(tr("删除"))
        delete_action.setToolTip(tr("删除此装备"))

        action = menu.exec(global_pos)
        if action == equip_action:
            self.equip_requested.emit(self._equip_data, self._group_key)
        elif is_mock and action == edit_action:
            self.edit_requested.emit(self._equip_data, self._group_key)
        elif action == copy_action:
            self.copy_requested.emit(self._equip_data, self._group_key)
        elif action == delete_action:
            self.delete_requested.emit(self._equip_data, self._group_key)

    def set_equip(
        self, equip_data: dict, part_label: str,
        group_key: str = "", is_mock: bool = False,
        is_loadout: bool = False,
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
        self.status_tags.set_visible("loadout", is_loadout)

        level = equip_data.get("level") or "?"
        is_chengyin = equip_data.get("is_chengyin", False)
        tag = " [" + tr("承音") + "]" if is_chengyin else ""

        # 词条平均百分比（内联在等级后面，字号跟随 affix_font_size）
        pct_fs = self._affix_fs
        cap_pcts = []
        for i in range(1, 6):
            affix = equip_data.get(f"affix_{i}")
            if affix and affix.get("name") and affix.get("cap_pct") is not None:
                cap_pcts.append(affix["cap_pct"])
        if cap_pcts:
            avg_pct = sum(cap_pcts) / len(cap_pcts)
            pct_color = _affix_value_color(avg_pct)
            pct_html = f'&nbsp;&nbsp;<span style="font-size:{pct_fs}px;color:{pct_color};font-weight:bold;">{avg_pct:.0f}%</span>'
            self.lbl_level.setTextFormat(Qt.TextFormat.RichText)
            self.lbl_level.setText(f"Lv{level}{tag}{pct_html}")
        else:
            self.lbl_level.setTextFormat(Qt.TextFormat.PlainText)
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
