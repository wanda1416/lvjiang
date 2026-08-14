"""装备状态面板 - 展示用户当前穿戴的八件装备"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr

# 品质颜色映射（适配浅色背景）
_QUALITY_COLORS = {
    "gold": "#B8860B",   # 暗金
    "purple": "#8B5CF6", # 紫色
    "blue": "#2563EB",   # 蓝色
    "green": "#16A34A",  # 绿色
    None: "#999999",
}

# 装备槽位定义（2行×4列）
# 左四：主武器、副武器、环、佩
# 右四：冠胄、胸甲、胫甲、腕甲
# part_label 为已装备时第一行展示的部位名（武器不区分主副）
_SLOT_LAYOUT = [
    # (row, col, slot_key, display_name, part_label)
    (0, 0, "main_weapon", tr("主武器"), tr("武器")),
    (0, 1, "sub_weapon", tr("副武器"), tr("武器")),
    (0, 2, "head", tr("冠胄"), "冠胄"),
    (0, 3, "chest", tr("胸甲"), "胸甲"),
    (1, 0, "ring", tr("环"), "环"),
    (1, 1, "pendant", tr("佩"), "佩"),
    (1, 2, "leg", tr("胫甲"), "胫甲"),
    (1, 3, "wrist", tr("腕甲"), "腕甲"),
]


class _EquipCard(QFrame):
    """单件装备卡片"""

    def __init__(self, slot_name: str, part_label: str, display_params: dict | None = None, parent=None):
        super().__init__(parent)
        self._slot_name = slot_name
        self._part_label = part_label
        dp = display_params or {}
        self._name_fs = dp.get("name_font_size", 13)
        self._level_fs = dp.get("level_font_size", 12)
        self._affix_fs = dp.get("affix_font_size", 11)

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            _EquipCard {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 6px;
                padding: 6px;
            }
        """)
        self.setFixedHeight(dp.get("card_min_height", 160))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(3)

        # 第一行：部位 · 装备名（未装备时仅显示槽位名）
        self.lbl_slot = QLabel(slot_name)
        self.lbl_slot.setStyleSheet(f"font-weight: bold; font-size: {self._name_fs}px; color: #333333;")
        layout.addWidget(self.lbl_slot)

        # 第二行：等级 + 承音标记
        self.lbl_info = QLabel("—")
        self.lbl_info.setStyleSheet(f"font-size: {self._level_fs}px; color: #666666;")
        layout.addWidget(self.lbl_info)

        # 分割线
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #dee2e6;")
        line.setFixedHeight(1)
        layout.addWidget(line)

        # 词条区域容器
        self.affix_container = QWidget()
        self.affix_layout = QVBoxLayout(self.affix_container)
        self.affix_layout.setContentsMargins(0, 0, 0, 0)
        self.affix_layout.setSpacing(1)
        layout.addWidget(self.affix_container)

        layout.addStretch()

    def set_empty(self):
        """显示为空槽位"""
        self.lbl_slot.setText(self._slot_name)
        self.lbl_slot.setStyleSheet(f"font-weight: bold; font-size: {self._name_fs}px; color: #333333;")
        self.lbl_info.setText(tr("未装备"))
        self.lbl_info.setStyleSheet(f"font-size: {self._level_fs}px; color: #999999;")
        self._clear_affixes()

    def _clear_affixes(self):
        """清空词条区域"""
        while self.affix_layout.count() > 0:
            item = self.affix_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                # 词条行是 QHBoxLayout，需递归清理其子控件
                sub = item.layout()
                while sub.count() > 0:
                    child = sub.takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
                sub.deleteLater()

    def set_equip(self, equip_data: dict):
        """填充装备数据"""
        quality = equip_data.get("quality")
        color = _QUALITY_COLORS.get(quality, "#888888")

        # 第一行：部位 · 装备名（武器不区分主副），按品质着色
        name = equip_data.get("name", tr("未知"))
        self.lbl_slot.setText(f"{self._part_label} · {name}")
        self.lbl_slot.setStyleSheet(f"font-weight: bold; font-size: {self._name_fs}px; color: {color};")

        # 第二行：等级 + 承音标记
        level = equip_data.get("level", "?")
        is_chengyin = equip_data.get("is_chengyin", False)
        chengyin_tag = "  [" + tr("承音") + "]" if is_chengyin else ""

        self.lbl_info.setText(f"Lv{level}{chengyin_tag}")
        self.lbl_info.setStyleSheet(f"font-size: {self._level_fs}px; color: #666666;")

        # 词条列表
        self._clear_affixes()
        has_affix = False
        for i in range(1, 6):
            key = f"affix_{i}"
            affix = equip_data.get(key)
            if not affix or not affix.get("name"):
                continue
            has_affix = True

            affix_name = affix["name"]
            value = affix.get("value", "")
            unit = affix.get("unit", "")
            cap_pct = affix.get("cap_pct")
            is_transferred = affix.get("is_transferred", False)

            # 数值格式化
            if isinstance(value, (int, float)):
                if unit == "%":
                    val_str = f"{value}%"
                elif isinstance(value, float):
                    val_str = f"{value:.1f}"
                else:
                    val_str = str(value)
            else:
                val_str = str(value)

            # 词条颜色按 cap_pct 分级
            val_color = "#333333"
            if cap_pct is not None:
                if cap_pct >= 90:
                    val_color = "#B8860B"  # 金色（暗金，浅色背景可见）
                elif cap_pct >= 80:
                    val_color = "#8B5CF6"  # 紫色
                elif cap_pct >= 60:
                    val_color = "#2563EB"  # 蓝色
                else:
                    val_color = "#16A34A"  # 绿色

            transfer_mark = " ⟳" if is_transferred else ""

            # 词条行：名称靠左，数值靠右
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)

            lbl_name = QLabel(f"{affix_name}{transfer_mark}")
            lbl_name.setStyleSheet(f"font-size: {self._affix_fs}px; color: #555555;")
            row.addWidget(lbl_name, stretch=1)

            lbl_val = QLabel(val_str)
            lbl_val.setStyleSheet(f"font-size: {self._affix_fs}px; color: {val_color}; font-weight: bold;")
            row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

            self.affix_layout.addLayout(row)

        if not has_affix:
            lbl = QLabel(tr("无词条"))
            lbl.setStyleSheet(f"font-size: {self._affix_fs}px; color: #999999;")
            self.affix_layout.addWidget(lbl)

        # 定音词条：与普通词条间用虚线分隔
        dingyin = equip_data.get("dingyin")
        if dingyin and dingyin.get("name"):
            dash = QFrame()
            dash.setFrameShape(QFrame.Shape.NoFrame)
            dash.setStyleSheet("border: none; border-top: 1px dashed #adb5bd;")
            dash.setFixedHeight(1)
            self.affix_layout.addWidget(dash)

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)

            lbl_name = QLabel(dingyin["name"])
            lbl_name.setStyleSheet(f"font-size: {self._affix_fs}px; color: #555555;")
            row.addWidget(lbl_name, stretch=1)

            dy_value = dingyin.get("value", "")
            dy_val_str = f"{dy_value}%" if isinstance(dy_value, (int, float)) else str(dy_value)
            lbl_val = QLabel(dy_val_str)
            lbl_val.setStyleSheet(f"font-size: {self._affix_fs}px; color: #333333; font-weight: bold;")
            row.addWidget(lbl_val, alignment=Qt.AlignmentFlag.AlignRight)

            self.affix_layout.addLayout(row)


class EquipStatusPanel(QWidget):
    """装备状态面板（2行×4列）"""

    def __init__(self, display_params: dict | None = None, parent=None):
        super().__init__(parent)
        self._display_params = display_params or {}
        self._cards: dict[str, _EquipCard] = {}
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # wrapper 贴顶：grid 在上，stretch 占剩余空间
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)

        container = QWidget()
        # 限制 container 垂直扩展，确保内容贴顶
        container.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        grid = QGridLayout(container)
        grid.setSpacing(8)

        for row, col, slot_key, display_name, part_label in _SLOT_LAYOUT:
            card = _EquipCard(display_name, part_label, self._display_params)
            grid.addWidget(card, row, col)
            self._cards[slot_key] = card

        wrapper_layout.addWidget(container)
        wrapper_layout.addStretch()
        scroll.setWidget(wrapper)
        layout.addWidget(scroll)

    def set_display_params(self, params: dict):
        """更新展示参数并刷新所有卡片样式"""
        self._display_params = params
        for card in self._cards.values():
            dp = params
            card._name_fs = dp.get("name_font_size", 13)
            card._level_fs = dp.get("level_font_size", 12)
            card._affix_fs = dp.get("affix_font_size", 11)
            card.setFixedHeight(dp.get("card_min_height", 160))
        # 触发重新渲染：如果有当前数据则刷新
        if hasattr(self, '_last_equipped'):
            self.refresh(self._last_equipped)

    def refresh(self, equipped_data: dict):
        """根据 equipped 字典刷新面板"""
        self._last_equipped = equipped_data
        for _row, _col, slot_key, _display_name, _part_label in _SLOT_LAYOUT:
            card = self._cards[slot_key]
            equip = equipped_data.get(slot_key)
            if equip:
                card.set_equip(equip)
            else:
                card.set_empty()
