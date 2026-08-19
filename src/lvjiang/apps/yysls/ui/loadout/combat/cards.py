"""卡片 UI 构建 Mix-in —— 创建各类属性展示卡片。

职责：
- _create_card：创建中性分组卡片
- _add_attack_card：攻击属性卡片
- _add_judgment_card：判定属性卡片
- _add_gain_card：增益效果卡片
- _add_damage_card：伤害加成卡片
- _create_dynamic_slot：动态槽位
- _create_attr_widget：属性展示组件
- _create_value_label：数值标签
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr

# 样式常量
_CARD_STYLE = """
    #combatAttrCard {
        background: palette(base);
        border: 1px solid palette(mid);
        border-radius: 6px;
    }
"""
_TITLE_STYLE = "font-size: 15px; font-weight: 600;"
_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_VALUE_STYLE = "font-size: 15px; font-weight: 600;"
_YELLOW_VALUE_COLOR = "#d4a017"
_YELLOW_VALUE_STYLE = (
    f"font-size: 15px; font-weight: 600; color: {_YELLOW_VALUE_COLOR};"
)


class CombatCardsMixin:
    """卡片 UI 构建 Mix-in。

    需要主类提供：
    - self._attr_labels: dict[str, QLabel]（属性标签字典）
    """

    def _create_card(self, title: str) -> QFrame:
        """创建与应用主题一致的中性分组卡片。"""
        card = QFrame()
        card.setObjectName("combatAttrCard")
        card.setStyleSheet(_CARD_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setStyleSheet(_TITLE_STYLE)
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(14, 12, 14, 4)
        title_layout.addWidget(title_label)
        title_layout.addStretch()
        card_layout.addLayout(title_layout)
        return card

    def _add_attack_card(self, parent_layout: QVBoxLayout):
        card = self._create_card(tr("攻击属性"))
        grid = QGridLayout()
        grid.setContentsMargins(14, 2, 14, 14)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        attacks = (
            ("外功", "min_outer", "max_outer"),
            ("鸣金", "min_mingjin", "max_mingjin"),
            ("裂石", "min_lieshi", "max_lieshi"),
            ("破竹", "min_pozhu", "max_pozhu"),
            ("牵丝", "min_qiansi", "max_qiansi"),
            ("无相", "min_wuxiang", "max_wuxiang"),
        )
        # 存储原始布局位置用于自适应重排
        self._attack_grid_items: list[tuple[QWidget, int, int]] = []
        for row, (name, min_field, max_field) in enumerate(attacks):
            min_widget = self._create_attr_widget(
                f"最小{name}攻击", min_field
            )
            max_widget = self._create_attr_widget(
                f"最大{name}攻击", max_field
            )
            grid.addWidget(min_widget, row, 0)
            grid.addWidget(max_widget, row, 1)
            self._attack_grid_items.append((min_widget, row, 0))
            self._attack_grid_items.append((max_widget, row, 1))
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._attack_grid = grid
        card_layout = card.layout()
        if card_layout is not None:
            card_layout.addLayout(grid)
        parent_layout.addWidget(card)
        return card

    def _add_judgment_card(self, parent_layout: QVBoxLayout):
        """判定属性按相同业务含义排成三行。"""
        card = self._create_card(tr("判定属性"))
        rows = (
            (("precision", "精准率", False),),
            (("crit_rate", "会心率", False),
             ("direct_crit", "直接会心率", True)),
            (("intent_rate", "会意率", False),
             ("direct_intent", "直接会意率", True)),
        )
        grid = QGridLayout()
        grid.setContentsMargins(14, 2, 14, 14)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        # 存储原始布局位置用于自适应重排
        self._judgment_grid_items: list[tuple[QWidget, int, int]] = []
        for row, fields in enumerate(rows):
            for col, (field_name, label_text, yellow) in enumerate(fields):
                widget = self._create_attr_widget(label_text, field_name, yellow)
                grid.addWidget(widget, row, col)
                self._judgment_grid_items.append((widget, row, col))
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._judgment_grid = grid
        card_layout = card.layout()
        if card_layout is not None:
            card_layout.addLayout(grid)
        parent_layout.addWidget(card)
        return card

    def _add_gain_card(self, parent_layout: QVBoxLayout):
        """创建具有固定六行槽位的增益效果卡片。"""
        card = self._create_card(tr("增益效果"))
        grid = QGridLayout()
        grid.setContentsMargins(14, 2, 14, 14)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)

        fixed_cells = (
            (0, 0, "外功穿透", "outer_pen"),
            (0, 1, "属攻穿透", "__attr_pen__"),
            (2, 0, "全武学增效", "all_skill_bonus"),
            (3, 0, "单体类奇术增伤", "single_qs_bonus"),
            (3, 1, "群体类奇术增伤", "group_qs_bonus"),
            (4, 0, "对首领单位增伤", "boss_bonus"),
            (4, 1, "对玩家单位增效", "player_bonus"),
        )
        # 存储原始布局位置用于自适应重排
        self._gain_grid_items: list[tuple[QWidget, int, int]] = []
        for row, col, label_text, field_name in fixed_cells:
            widget = self._create_attr_widget(label_text, field_name)
            grid.addWidget(widget, row, col)
            self._gain_grid_items.append((widget, row, col))
            if field_name == "__attr_pen__":
                self._attr_pen_name = widget.findChild(QLabel, "attrName")
                self._attr_pen_label = self._attr_labels[field_name]

        self._weapon_bonus_slots = [
            self._create_dynamic_slot(grid, 1, col) for col in range(2)
        ]
        self._skill_bonus_slots = [
            self._create_dynamic_slot(grid, 5, col) for col in range(2)
        ]
        # 存储动态槽位置
        for slot_widget, _, _ in self._weapon_bonus_slots:
            self._gain_grid_items.append((slot_widget, 1, 0 if slot_widget is self._weapon_bonus_slots[0][0] else 1))
        for slot_widget, _, _ in self._skill_bonus_slots:
            self._gain_grid_items.append((slot_widget, 5, 0 if slot_widget is self._skill_bonus_slots[0][0] else 1))
        self._gain_grid = grid
        self._extra_labels: dict[str, QLabel] = {}
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card_layout = card.layout()
        if card_layout is not None:
            card_layout.addLayout(grid)
        parent_layout.addWidget(card)
        return card

    def _add_damage_card(self, parent_layout: QVBoxLayout):
        """创建固定三行两列的伤害加成卡片。"""
        card = self._create_card(tr("伤害加成"))
        grid = QGridLayout()
        grid.setContentsMargins(14, 2, 14, 14)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)

        cells = (
            (0, 0, "会心伤害加成", "crit_dmg"),
            (0, 1, "会意伤害加成", "intent_dmg"),
            (1, 0, "外功伤害加成", "outer_bonus"),
            (2, 0, "属攻伤害加成", "__attr_bonus__"),
        )
        # 存储原始布局位置用于自适应重排
        self._damage_grid_items: list[tuple[QWidget, int, int]] = []
        for row, col, label_text, field_name in cells:
            widget = self._create_attr_widget(label_text, field_name)
            grid.addWidget(widget, row, col)
            self._damage_grid_items.append((widget, row, col))
            if field_name == "__attr_bonus__":
                self._attr_bonus_name = widget.findChild(QLabel, "attrName")
                self._attr_bonus_label = self._attr_labels[field_name]

        for row, label_text in (
            (1, "外功伤害减免"),
            (2, "属攻伤害减免"),
        ):
            widget = self._create_attr_widget(label_text, f"__reduction_{row}__")
            self._attr_labels[f"__reduction_{row}__"].setText("0.00%")
            grid.addWidget(widget, row, 1)
            self._damage_grid_items.append((widget, row, 1))

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._damage_grid = grid
        card_layout = card.layout()
        if card_layout is not None:
            card_layout.addLayout(grid)
        parent_layout.addWidget(card)
        return card

    def _create_dynamic_slot(self, grid: QGridLayout, row: int,
                             col: int) -> tuple[QWidget, QLabel, QLabel]:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        name_label = QLabel()
        name_label.setStyleSheet(_NAME_STYLE)
        value_label = self._create_value_label()
        layout.addWidget(name_label)
        layout.addStretch()
        layout.addWidget(value_label)
        grid.addWidget(widget, row, col)
        return widget, name_label, value_label

    def _create_attr_widget(self, label_text: str, field_name: str,
                            yellow: bool = False) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        name_label = QLabel(tr(label_text))
        name_label.setObjectName("attrName")
        name_label.setStyleSheet(_NAME_STYLE)
        value_label = self._create_value_label(yellow=yellow)
        layout.addWidget(name_label)
        layout.addStretch()
        layout.addWidget(value_label)
        self._attr_labels[field_name] = value_label
        return widget

    def _create_value_label(self, yellow: bool = False) -> QLabel:
        """创建右对齐数值标签（无最小宽度限制，允许压缩）"""
        label = QLabel("0")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # 不设置最小宽度，允许标签压缩以适应可用空间
        label.setStyleSheet(_YELLOW_VALUE_STYLE if yellow else _VALUE_STYLE)
        return label
