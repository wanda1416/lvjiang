"""燕云「战斗属性」Tab

展示角色最终战斗属性（基础属性 + 装备 + 弓玦），支持：
- 选择流派 / 玩法 / 弓玦
- 创建玩法（弹出对话框输入面板属性，反推基础属性并保存）
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtGui import QDoubleValidator
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr
from ..combat_attrs import (CombatAttributes, COMBAT_ATTR_FIELDS, format_value,
                            compute_gongjue_attrs, PLAY_STYLE_FIELD_GROUPS)
from .profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from .profile.tab import add_user_nav_buttons

# 弓玦类型候选
_GONGJUE_TYPES = ["", "会意", "精准", "会心"]

_YELLOW_VALUE_COLOR = "#D97706"
_CARD_STYLE = """
    QFrame#combatAttrCard {
        background-color: palette(base);
        border: 1px solid palette(midlight);
        border-radius: 6px;
    }
"""
_TITLE_STYLE = "font-size: 15px; font-weight: 600;"
_NAME_STYLE = "font-size: 13px; color: palette(mid);"
_VALUE_STYLE = "font-size: 15px; font-weight: 600;"
_YELLOW_VALUE_STYLE = (
    f"font-size: 15px; font-weight: 600; color: {_YELLOW_VALUE_COLOR};"
)


class CombatAttrsTab(QWidget):
    """战斗属性 Tab"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 公共工具栏：与其他角色信息 Tab 保持位置和样式一致 ──
        btn_row = QHBoxLayout()
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新战斗属性"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        add_user_nav_buttons(btn_row, self._host)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 当前配置 ──
        select_group = QGroupBox(tr("当前配置"))
        select_layout = QHBoxLayout(select_group)
        select_layout.setContentsMargins(14, 14, 14, 12)
        select_layout.setSpacing(8)

        select_layout.addWidget(QLabel(tr("流派")))
        self._combo_school = QComboBox()
        self._combo_school.setMinimumWidth(140)
        self._combo_school.setMinimumHeight(30)
        self._combo_school.currentTextChanged.connect(self._on_school_changed)
        select_layout.addWidget(self._combo_school)

        select_layout.addSpacing(8)
        select_layout.addWidget(QLabel(tr("玩法")))
        self._combo_play_style = QComboBox()
        self._combo_play_style.setMinimumWidth(170)
        self._combo_play_style.setMinimumHeight(30)
        self._combo_play_style.currentTextChanged.connect(self._on_play_style_changed)
        select_layout.addWidget(self._combo_play_style)

        select_layout.addSpacing(8)
        select_layout.addWidget(QLabel(tr("弓玦")))
        self._combo_gongjue = QComboBox()
        self._combo_gongjue.setMinimumWidth(100)
        self._combo_gongjue.setMinimumHeight(30)
        self._combo_gongjue.addItem(tr("无"), "")
        for gongjue_type in _GONGJUE_TYPES[1:]:
            self._combo_gongjue.addItem(gongjue_type, gongjue_type)
        self._combo_gongjue.currentTextChanged.connect(self._on_gongjue_changed)
        select_layout.addWidget(self._combo_gongjue)

        select_layout.addStretch()
        self._btn_create_play = QPushButton(tr("新建玩法…"))
        self._btn_create_play.setMinimumHeight(30)
        self._btn_create_play.clicked.connect(self._on_create_play_style)
        select_layout.addWidget(self._btn_create_play)

        layout.addWidget(select_group)

        # ── 毕业率（独立区域，单列展示） ──
        self._add_graduation_card(layout)

        # ── 属性展示区（主题一致的中性数据卡片） ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._attrs_widget = QWidget()
        self._main_layout = QHBoxLayout(self._attrs_widget)
        self._main_layout.setContentsMargins(2, 10, 2, 2)
        self._main_layout.setSpacing(12)

        self._attr_labels: dict[str, QLabel] = {}
        left_layout = QVBoxLayout()
        right_layout = QVBoxLayout()
        left_layout.setSpacing(12)
        right_layout.setSpacing(12)
        self._main_layout.addLayout(left_layout, stretch=1)
        self._main_layout.addLayout(right_layout, stretch=1)

        self._add_attack_card(left_layout)

        # ── 判定属性卡片 ──
        self._add_judgment_card(left_layout)
        left_layout.addStretch()

        # ── 增益效果（含动态专项增益） ──
        self._add_gain_card(right_layout)

        # ── 增伤效果卡片 ──
        self._add_damage_card(right_layout)
        right_layout.addStretch()
        scroll.setWidget(self._attrs_widget)
        layout.addWidget(scroll, stretch=1)

        # 订阅装备变更信号（装备数据 Tab 中穿戴/卸下装备时触发）
        self._host.equipment_changed.connect(self._refresh_display)
        # 订阅用户切换信号（上一个/下一个用户按钮触发）
        self._host.user_changed.connect(lambda _name: self._load_data())

    def _add_graduation_card(self, parent_layout: QVBoxLayout):
        """毕业率展示卡片 — 当前配置下方、攻击属性上方"""
        card = self._create_card(tr("毕业率"))
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(14, 2, 14, 14)
        content_layout.setSpacing(20)

        # DPS
        dps_widget = QWidget()
        dps_layout = QHBoxLayout(dps_widget)
        dps_layout.setContentsMargins(0, 0, 0, 0)
        dps_layout.setSpacing(8)
        dps_name = QLabel(tr("DPS"))
        dps_name.setStyleSheet(_NAME_STYLE)
        self._dps_value = self._create_value_label()
        self._dps_value.setText("--")
        dps_layout.addWidget(dps_name)
        dps_layout.addStretch()
        dps_layout.addWidget(self._dps_value)
        content_layout.addWidget(dps_widget)

        # 毕业率
        rate_widget = QWidget()
        rate_layout = QHBoxLayout(rate_widget)
        rate_layout.setContentsMargins(0, 0, 0, 0)
        rate_layout.setSpacing(8)
        rate_name = QLabel(tr("毕业率"))
        rate_name.setStyleSheet(_NAME_STYLE)
        self._graduation_value = self._create_value_label(yellow=True)
        self._graduation_value.setText("--")
        rate_layout.addWidget(rate_name)
        rate_layout.addStretch()
        rate_layout.addWidget(self._graduation_value)
        content_layout.addWidget(rate_widget)

        card.layout().addWidget(content)
        parent_layout.addWidget(card)

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
        for row, (name, min_field, max_field) in enumerate(attacks):
            min_widget = self._create_attr_widget(
                f"最小{name}攻击", min_field
            )
            max_widget = self._create_attr_widget(
                f"最大{name}攻击", max_field
            )
            grid.addWidget(min_widget, row, 0)
            grid.addWidget(max_widget, row, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.layout().addLayout(grid)
        parent_layout.addWidget(card)

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
        for row, fields in enumerate(rows):
            for col, (field_name, label_text, yellow) in enumerate(fields):
                widget = self._create_attr_widget(label_text, field_name, yellow)
                grid.addWidget(widget, row, col)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.layout().addLayout(grid)
        parent_layout.addWidget(card)

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
        for row, col, label_text, field_name in fixed_cells:
            widget = self._create_attr_widget(label_text, field_name)
            grid.addWidget(widget, row, col)
            if field_name == "__attr_pen__":
                self._attr_pen_name = widget.findChild(QLabel, "attrName")
                self._attr_pen_label = self._attr_labels[field_name]

        self._weapon_bonus_slots = [
            self._create_dynamic_slot(grid, 1, col) for col in range(2)
        ]
        self._skill_bonus_slots = [
            self._create_dynamic_slot(grid, 5, col) for col in range(2)
        ]
        self._extra_labels: dict[str, QLabel] = {}
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.layout().addLayout(grid)
        parent_layout.addWidget(card)

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
        for row, col, label_text, field_name in cells:
            widget = self._create_attr_widget(label_text, field_name)
            grid.addWidget(widget, row, col)
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

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.layout().addLayout(grid)
        parent_layout.addWidget(card)

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

    def _add_section_card(self, title: str, items: list[tuple],
                          parent_layout: QVBoxLayout) -> QFrame:
        """添加规则对齐的属性卡片。

        Args:
            title: 分区标题
            items: 属性列表，每项为:
                   ("normal", field_name, label, unit)
                   ("resist", field_name, label, unit)  # 有抗性
        """
        card = self._create_card(title)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(8)
        grid.setContentsMargins(14, 2, 14, 14)

        row = 0
        col = 0
        max_cols = 2  # 双列布局

        for item in items:
            if item[0] in ("normal", "resist", "summary"):
                _, field_name, label_text, unit = item
                attr_widget = self._create_attr_widget(
                    label_text, field_name,
                    field_name in ("direct_crit", "direct_intent"),
                )
                grid.addWidget(attr_widget, row, col, 1, 1)
                if field_name == "__attr_pen__":
                    self._attr_pen_name = attr_widget.findChild(QLabel, "attrName")
                    self._attr_pen_label = self._attr_labels[field_name]

            # 双列流动
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.layout().addLayout(grid)
        parent_layout.addWidget(card)
        return card

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
        """创建右对齐数值标签"""
        label = QLabel("0")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumWidth(120)
        label.setStyleSheet(_YELLOW_VALUE_STYLE if yellow else _VALUE_STYLE)
        return label

    # ── 数据加载 ──────────────────────────────────────────────

    def _load_data(self):
        """加载数据并刷新显示"""
        self._refresh_schools()
        self._refresh_play_styles()
        self._restore_selection()
        self._refresh_display()

    def _refresh_schools(self):
        """刷新流派下拉"""
        from ..config import get_game_config
        gc = get_game_config()
        schools = gc.get_schools()

        self._combo_school.blockSignals(True)
        self._combo_school.clear()
        for name in schools:
            self._combo_school.addItem(name)
        self._combo_school.blockSignals(False)

    def _refresh_play_styles(self):
        """刷新玩法下拉（根据当前选择的流派）"""
        from ..config import get_play_styles

        school = self._get_current_school()
        self._combo_play_style.blockSignals(True)
        self._combo_play_style.clear()
        self._combo_play_style.addItem("")  # 空选项

        if school:
            play_styles = get_play_styles(school)
            for name in play_styles:
                self._combo_play_style.addItem(name)

        self._combo_play_style.blockSignals(False)

    def _get_current_school(self) -> str | None:
        """获取当前选择的流派"""
        school = self._combo_school.currentText()
        return school if school else None

    def _on_school_changed(self, _school: str):
        """流派切换 → 刷新玩法下拉"""
        self._refresh_play_styles()
        self._refresh_display()
        self._save_selection()

    def _on_play_style_changed(self, _name: str):
        """玩法切换"""
        self._refresh_display()
        self._save_selection()

    def _on_gongjue_changed(self, _gongjue: str):
        """弓玦切换"""
        self._refresh_display()
        self._save_selection()

    def _get_current_gongjue(self) -> str:
        value = self._combo_gongjue.currentData()
        return value if isinstance(value, str) else self._combo_gongjue.currentText()

    def _on_refresh(self):
        """刷新按钮"""
        self._load_data()

    # ── 配置选择持久化 ──────────────────────────────────────────

    def _save_selection(self):
        """保存当前配置选择到 session.json settings.combat_attrs_selections"""
        from lvjiang.core.config.session import load_settings, save_settings

        user_name = self._host.active_user_name()
        if not user_name:
            return

        selection = {
            "school": self._combo_school.currentText(),
            "play_style": self._combo_play_style.currentText(),
            "gongjue": self._get_current_gongjue(),
        }

        try:
            settings = load_settings()
            selections = settings.get("combat_attrs_selections", {})
            if not isinstance(selections, dict):
                selections = {}
            selections[user_name] = selection
            settings["combat_attrs_selections"] = selections
            save_settings(settings)
        except Exception as e:
            logger.debug(f"保存战斗属性选择失败: {e}")

    def _restore_selection(self):
        """从 session.json 恢复当前用户的配置选择"""
        from lvjiang.core.config.session import load_settings

        user_name = self._host.active_user_name()
        if not user_name:
            return

        try:
            settings = load_settings()
            selections = settings.get("combat_attrs_selections", {})
            if not isinstance(selections, dict):
                return
            selection = selections.get(user_name, {})
            if not isinstance(selection, dict):
                return

            school = selection.get("school", "")
            play_style = selection.get("play_style", "")
            gongjue = selection.get("gongjue", "")

            # 恢复流派
            if school:
                idx = self._combo_school.findText(school)
                if idx >= 0:
                    self._combo_school.setCurrentIndex(idx)
                    self._refresh_play_styles()

            # 恢复玩法
            if play_style:
                idx = self._combo_play_style.findText(play_style)
                if idx >= 0:
                    self._combo_play_style.setCurrentIndex(idx)

            # 恢复弓玦
            if gongjue:
                idx = self._combo_gongjue.findData(gongjue)
                if idx >= 0:
                    self._combo_gongjue.setCurrentIndex(idx)
        except Exception as e:
            logger.debug(f"恢复战斗属性选择失败: {e}")

    # ── 属性展示 ──────────────────────────────────────────────

    def _refresh_display(self):
        """刷新属性显示"""
        from ..combat_attrs import apply_three_rate_resistance, apply_bonus_resistance, apply_penetration_resistance, has_resistance, is_three_rate_field, is_penetration_field, WUXIANG_TO_ATTR_PEN
        
        # 一次性计算所有中间结果，避免重复加载装备数据
        base_attrs = self._get_base_attrs()
        equip_base_attrs = self._compute_equip_base_attrs()
        equip_attrs = self._compute_equip_attrs()
        gongjue_attrs = self._compute_gongjue_attrs()
        combat_attrs = base_attrs + equip_base_attrs + equip_attrs + gongjue_attrs
        
        # 处理无相穿透转换：根据流派属性转换为对应的属攻穿透
        if equip_attrs.wuxiang_pen > 0:
            school = self._get_current_school()
            if school:
                from ..config import get_game_config
                gc = get_game_config()
                school_attr = gc.get_school_attr(school)
                if school_attr and school_attr in WUXIANG_TO_ATTR_PEN:
                    target_field = WUXIANG_TO_ATTR_PEN[school_attr]
                    current = getattr(combat_attrs, target_field, 0.0)
                    setattr(combat_attrs, target_field, current + equip_attrs.wuxiang_pen)

        for field_name, _display_name, unit, _ in COMBAT_ATTR_FIELDS:
            value = getattr(combat_attrs, field_name, 0.0)
            label = self._attr_labels.get(field_name)
            if label:
                if has_resistance(field_name):
                    # 有抗性：显示 原始值(抗性值)
                    if is_three_rate_field(field_name):
                        capped = apply_three_rate_resistance(field_name, value)
                    elif is_penetration_field(field_name):
                        # 穿透类：基础值(从玩法) + 装备定音 / 1.15
                        base_val = getattr(base_attrs, field_name, 0.0)
                        equip_val = getattr(equip_attrs, field_name, 0.0)
                        capped = apply_penetration_resistance(equip_val, base_val)
                        # 显示：原始值(生效值)，其中原始值 = 基础 + 装备
                        original = base_val + equip_val
                        self._set_resistance_text(label, original, capped, unit)
                        continue
                    else:
                        # 增伤类：整个值 / 1.15
                        capped = apply_bonus_resistance(value)
                    self._set_resistance_text(label, value, capped, unit)
                else:
                    label.setText(format_value(value, unit))

        self._refresh_attr_penetration(base_attrs, equip_attrs)
        self._refresh_attr_bonus(combat_attrs)
        self._refresh_extra_attrs(combat_attrs.extra_attrs)
        self._refresh_graduation(combat_attrs)

    def _refresh_attr_bonus(self, combat_attrs: CombatAttributes) -> None:
        """显示当前流派属攻伤害加成，并提供四系悬浮明细。"""
        from ..combat_attrs import SCHOOL_ATTR_FIELD_MAP
        from ..config import get_game_config

        attr_fields = (
            ("鸣金", "mingjin_bonus"),
            ("裂石", "lieshi_bonus"),
            ("破竹", "pozhua_bonus"),
            ("牵丝", "qiansi_bonus"),
        )
        school = self._get_current_school()
        school_attr = get_game_config().get_school_attr(school) if school else None
        current_field = SCHOOL_ATTR_FIELD_MAP.get(school_attr or "", {}).get("attr_bonus")
        current_name = next(
            (name for name, field in attr_fields if field == current_field), None
        )
        values = {
            field: getattr(combat_attrs, field, 0.0)
            for _name, field in attr_fields
        }

        if current_field:
            value = values[current_field]
            self._attr_bonus_name.setText(
                tr("属攻伤害加成（{name}）").format(name=current_name)
            )
        else:
            non_zero = [(name, field) for name, field in attr_fields
                        if values[field] != 0]
            if len(non_zero) == 1:
                current_name, current_field = non_zero[0]
                value = values[current_field]
                self._attr_bonus_name.setText(
                    tr("属攻伤害加成（{name}）").format(name=current_name)
                )
            else:
                value = 0.0
                self._attr_bonus_name.setText(tr("属攻伤害加成"))

        self._attr_bonus_label.setText(format_value(value, "%"))
        tooltip = "\n".join(
            f"{name}伤害加成：{format_value(values[field], '%')}"
            for name, field in attr_fields
        )
        self._attr_bonus_name.setToolTip(tooltip)
        self._attr_bonus_label.setToolTip(tooltip)

    def _refresh_attr_penetration(self, base_attrs: CombatAttributes,
                                  equip_attrs: CombatAttributes) -> None:
        """显示当前流派属攻穿透，悬浮时展示完整四系明细。"""
        from ..combat_attrs import (SCHOOL_ATTR_FIELD_MAP,
                                    apply_penetration_resistance)
        from ..config import get_game_config

        attr_fields = (
            ("鸣金", "mingjin_pen"),
            ("裂石", "lieshi_pen"),
            ("破竹", "pozhua_pen"),
            ("牵丝", "qiansi_pen"),
        )
        school = self._get_current_school()
        school_attr = get_game_config().get_school_attr(school) if school else None
        current_field = SCHOOL_ATTR_FIELD_MAP.get(school_attr or "", {}).get("attr_pen")
        current_name = next(
            (name for name, field in attr_fields if field == current_field), None
        )

        details: list[str] = []
        values: dict[str, tuple[float, float]] = {}
        for name, field in attr_fields:
            base_value = getattr(base_attrs, field, 0.0)
            equip_value = getattr(equip_attrs, field, 0.0)
            # 无相穿透属于装备定音，计入当前流派对应的属攻。
            if field == current_field:
                equip_value += getattr(equip_attrs, "wuxiang_pen", 0.0)
            original = base_value + equip_value
            effective = apply_penetration_resistance(equip_value, base_value)
            values[field] = (original, effective)
            details.append(
                f"{name}穿透：{format_value(original, '')}"
                f"({format_value(effective, '')})"
            )

        if current_field:
            original, effective = values[current_field]
            self._attr_pen_name.setText(tr("属攻穿透（{name}）").format(name=current_name))
        else:
            # 没有关联流派时，用唯一非零项作为摘要，否则展示 0。
            non_zero = [(name, field) for name, field in attr_fields
                        if values[field][0] != 0]
            if len(non_zero) == 1:
                current_name, current_field = non_zero[0]
                original, effective = values[current_field]
                self._attr_pen_name.setText(
                    tr("属攻穿透（{name}）").format(name=current_name)
                )
            else:
                original = effective = 0.0
                self._attr_pen_name.setText(tr("属攻穿透"))

        self._set_resistance_text(self._attr_pen_label, original, effective, "")
        tooltip = "\n".join(details)
        self._attr_pen_name.setToolTip(tooltip)
        self._attr_pen_label.setToolTip(tooltip)

    @staticmethod
    def _set_resistance_text(label: QLabel, original: float,
                             effective: float, unit: str) -> None:
        """按游戏的白字(黄字)语义显示原始值与抗性后数值。"""
        original_text = format_value(original, unit)
        effective_text = format_value(effective, unit)
        label.setText(
            f"{original_text}(<span style='color:{_YELLOW_VALUE_COLOR};'>"
            f"{effective_text}</span>)"
        )

    def _refresh_extra_attrs(self, extra_attrs: dict[str, float]):
        """将动态增益填入武器和技能各自的两个固定槽位。"""
        from ..combat_attrs import apply_bonus_resistance, has_resistance

        self._extra_labels.clear()
        for widget, name_label, value_label in (
                self._weapon_bonus_slots + self._skill_bonus_slots):
            name_label.clear()
            value_label.clear()
            widget.setToolTip("")

        weapon_items: list[tuple[str, float]] = []
        skill_items: list[tuple[str, float]] = []
        for key, value in sorted(extra_attrs.items()):
            if key.endswith(("武学增伤", "武学增效")):
                weapon_items.append((key, value))
            else:
                skill_items.append((key, value))

        def fill_slots(items: list[tuple[str, float]], slots) -> None:
            overflow = items[len(slots):]
            overflow_tip = "\n".join(
                f"{key}：{format_value(value, '%')}" for key, value in overflow
            )
            for (key, value), (widget, name_label, val_label) in zip(items, slots):
                name_label.setText(tr(key))
                if has_resistance(key):
                    capped = apply_bonus_resistance(value)
                    self._set_resistance_text(val_label, value, capped, "%")
                else:
                    val_label.setText(format_value(value, "%"))
                if overflow_tip:
                    widget.setToolTip(tr("其他增益：\n") + overflow_tip)
                self._extra_labels[key] = val_label

        fill_slots(weapon_items, self._weapon_bonus_slots)
        fill_slots(skill_items, self._skill_bonus_slots)

    def _refresh_graduation(self, combat_attrs: CombatAttributes) -> None:
        """计算并刷新毕业率显示"""
        from ..evaluator.graduation import get_graduation_calculator

        school = self._get_current_school()
        if not school:
            self._dps_value.setText("--")
            self._graduation_value.setText("--")
            return

        calc = get_graduation_calculator(school)
        if calc is None:
            self._dps_value.setText(tr("未实现"))
            self._graduation_value.setText(tr("未实现"))
            return

        try:
            result = calc.calculate(combat_attrs)
            self._dps_value.setText(f"{result.dps:,.0f}")
            self._graduation_value.setText(f"{result.graduation_rate * 100:.2f}%")
            tooltip = (
                f"{tr('总伤害')}: {result.total_damage:,.0f}\n"
                f"{tr('基准DPS')}: {result.baseline_dps:,.2f}\n"
                f"{tr('战斗时间')}: {result.combat_time}s"
            )
            self._dps_value.setToolTip(tooltip)
            self._graduation_value.setToolTip(tooltip)
        except Exception as e:
            logger.error(f"毕业率计算失败: {e}")
            self._dps_value.setText(tr("错误"))
            self._graduation_value.setText(tr("错误"))

    def _compute_combat_attrs(self) -> CombatAttributes:
        """计算最终战斗属性 = 基础属性(玩法) + 装备基础攻击 + 装备词条 + 弓玦属性"""
        base_attrs = self._get_base_attrs()
        equip_base_attrs = self._compute_equip_base_attrs()
        equip_attrs = self._compute_equip_attrs()
        gongjue_attrs = self._compute_gongjue_attrs()
        result = base_attrs + equip_base_attrs + equip_attrs + gongjue_attrs
        
        # 处理无相穿透转换：根据流派属性转换为对应的属攻穿透
        if equip_attrs.wuxiang_pen > 0:
            school = self._get_current_school()
            if school:
                from ..config import get_game_config
                from ..combat_attrs import WUXIANG_TO_ATTR_PEN
                gc = get_game_config()
                school_attr = gc.get_school_attr(school)
                if school_attr and school_attr in WUXIANG_TO_ATTR_PEN:
                    target_field = WUXIANG_TO_ATTR_PEN[school_attr]
                    current = getattr(result, target_field, 0.0)
                    setattr(result, target_field, current + equip_attrs.wuxiang_pen)
        
        return result

    def _get_base_attrs(self) -> CombatAttributes:
        """获取当前玩法的基础属性"""
        play_style = self._combo_play_style.currentText()
        if not play_style:
            return CombatAttributes()

        school = self._get_current_school()
        if not school:
            return CombatAttributes()

        from ..config import get_play_styles
        play_styles = get_play_styles(school)

        if play_style not in play_styles:
            return CombatAttributes()

        return CombatAttributes.from_dict(play_styles[play_style])

    def _compute_equip_attrs(self) -> CombatAttributes:
        """计算装备词条属性总和（含五维转换，不含装备基础攻击值）"""
        from ..combat_attrs import aggregate_equipment_attrs
        from lvjiang.core.config import SessionManager

        user_name = self._host.active_user_name()
        if not user_name:
            return CombatAttributes()

        try:
            data = SessionManager().load(user_name)
            equipped = data.get("equipped", {})
            return aggregate_equipment_attrs(equipped)
        except Exception as e:
            logger.error(f"读取装备数据失败: {e}")
            return CombatAttributes()

    def _compute_equip_base_attrs(self) -> CombatAttributes:
        """计算装备基础外功攻击值（根据部位/等级/品阶）

        武器/环/佩 提供基础外功攻击，品阶不同数值不同。
        """
        from ..combat_attrs import compute_equip_base_attrs
        from ..config import get_game_config
        from lvjiang.core.config import SessionManager

        user_name = self._host.active_user_name()
        if not user_name:
            return CombatAttributes()

        try:
            gc = get_game_config()
            data = SessionManager().load(user_name)
            equipped = data.get("equipped", {})
            return compute_equip_base_attrs(equipped, gc.get_base_attr_values)
        except Exception as e:
            logger.error(f"计算装备基础攻击失败: {e}")
            return CombatAttributes()

    def _compute_gongjue_attrs(self) -> CombatAttributes:
        """计算弓玦属性：当前赛季最大等级三率词条上限的一半"""
        gongjue_type = self._get_current_gongjue()
        if not gongjue_type:
            return CombatAttributes()

        try:
            from ..config import get_game_config
            gc = get_game_config()
            # 获取当前赛季装备等级
            seasons = gc.get_season_configs()
            if not seasons:
                return CombatAttributes()
            equip_level = seasons[-1].equip_level
            if not equip_level:
                return CombatAttributes()
            return compute_gongjue_attrs(gongjue_type, equip_level, gc.get_affix_caps)
        except Exception as e:
            logger.error(f"计算弓玦属性失败: {e}")
            return CombatAttributes()

    # ── 创建玩法（反推基础属性）────────────────────────────────

    def _on_create_play_style(self):
        """创建玩法按钮 → 弹出对话框"""
        school = self._get_current_school()
        if not school:
            QMessageBox.warning(self, tr("无法创建"), tr("请先选择一个流派"))
            return

        # 获取流派属性
        from ..config import get_game_config
        gc = get_game_config()
        school_attr = gc.get_school_attr(school)

        dlg = _CreatePlayStyleDialog(self, school_attr=school_attr)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        panel_attrs = dlg.get_panel_attrs()
        name = dlg.get_play_style_name()

        if not name:
            QMessageBox.warning(self, tr("无法保存"), tr("玩法名称不能为空"))
            return

        # 检查重名
        from ..config import get_play_styles
        existing = get_play_styles(school)
        if name in existing:
            ret = QMessageBox.question(
                self, tr("玩法已存在"),
                tr("玩法「{name}」已存在，是否覆盖？").format(name=name),
            )
            if ret != QMessageBox.StandardButton.Yes:
                return

        # 反推：base = panel - equip_base - equip_affix - gongjue
        # equip_base: 装备基础外功攻击（武器/环/佩，根据品阶不同）
        # equip_affix: 装备词条属性（含劲/势/敏五维转换）
        # gongjue: 弓玦属性
        # 注意：穿透类用户填写的就是基础值，不需要扣减装备
        equip_base_attrs = self._compute_equip_base_attrs()
        equip_attrs = self._compute_equip_attrs()
        gongjue_attrs = self._compute_gongjue_attrs()
        base_attrs = panel_attrs - equip_base_attrs - equip_attrs - gongjue_attrs
        
        # 穿透类特殊处理：用户填写的就是基础值，直接保存
        from ..combat_attrs import PENETRATION_FIELDS
        for pen_field in PENETRATION_FIELDS:
            panel_val = getattr(panel_attrs, pen_field, 0.0)
            setattr(base_attrs, pen_field, panel_val)

        # 保存
        self._save_play_style(school, name, base_attrs)
        QMessageBox.information(
            self, tr("保存成功"),
            tr("玩法「{name}」已保存到流派「{school}」").format(name=name, school=school),
        )

        # 刷新
        self._refresh_play_styles()
        self._combo_play_style.setCurrentText(name)

    def _save_play_style(self, school: str, name: str, base_attrs: CombatAttributes):
        """保存玩法到 session 配置（只保存 PLAY_STYLE_FIELD_GROUPS 中定义的字段）"""
        from ..config import save_play_style, get_game_config
        from ..combat_attrs import PLAY_STYLE_FIELD_GROUPS, SCHOOL_ATTR_FIELD_MAP

        # 解析占位符：根据流派属性获取实际字段名
        gc = get_game_config()
        school_attr = gc.get_school_attr(school)
        attr_map = SCHOOL_ATTR_FIELD_MAP.get(school_attr, {}) if school_attr else {}

        play_style_data = {}
        for _, fields in PLAY_STYLE_FIELD_GROUPS:
            for fn, _, _ in fields:
                # 解析占位符
                if fn == "__attr_pen__":
                    fn = attr_map.get("attr_pen", "")
                elif fn == "__attr_bonus__":
                    fn = attr_map.get("attr_bonus", "")
                elif fn == "__min_attr__":
                    fn = attr_map.get("min_attr", "")
                elif fn == "__max_attr__":
                    fn = attr_map.get("max_attr", "")
                if not fn or fn.startswith("__"):
                    continue
                v = getattr(base_attrs, fn, 0)
                if v:
                    play_style_data[fn] = v

        try:
            save_play_style(school, name, play_style_data)
        except Exception as e:
            logger.error(f"保存玩法失败: {e}")
            raise


class _CreatePlayStyleDialog(QDialog):
    """创建玩法对话框 — 输入面板属性，反推基础属性"""

    def __init__(self, parent=None, school_attr: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("创建玩法"))
        self.setMinimumWidth(720)
        self._school_attr = school_attr
        self._edits: dict[str, QLineEdit] = {}
        self._setup_ui()

    def _get_resolved_fields(self) -> list[tuple[str, list[tuple[str, str, str]]]]:
        """解析占位符字段，返回实际字段列表"""
        from ..combat_attrs import PLAY_STYLE_FIELD_GROUPS, SCHOOL_ATTR_FIELD_MAP

        if not self._school_attr or self._school_attr not in SCHOOL_ATTR_FIELD_MAP:
            # 无流派属性时，跳过属攻相关字段
            result = []
            for label, fields in PLAY_STYLE_FIELD_GROUPS:
                resolved = [(fn, dl, u) for fn, dl, u in fields if not fn.startswith("__")]
                if resolved:
                    result.append((label, resolved))
            return result

        # 有流派属性时，替换占位符
        attr_map = SCHOOL_ATTR_FIELD_MAP[self._school_attr]
        result = []
        for label, fields in PLAY_STYLE_FIELD_GROUPS:
            resolved = []
            for fn, dl, u in fields:
                if fn == "__attr_pen__":
                    resolved.append((attr_map["attr_pen"], dl, u))
                elif fn == "__attr_bonus__":
                    resolved.append((attr_map["attr_bonus"], dl, u))
                elif fn == "__min_attr__":
                    resolved.append((attr_map["min_attr"], dl, u))
                elif fn == "__max_attr__":
                    resolved.append((attr_map["max_attr"], dl, u))
                else:
                    resolved.append((fn, dl, u))
            result.append((label, resolved))
        return result

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(8)

        title = QLabel(tr("创建玩法"))
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title)

        hint = QLabel(
            tr("填写玩法本身提供的面板属性。装备专属属性无需填写；"
               "外功穿透和属攻穿透仅填写基础值，不包含装备定音。")
        )
        hint.setStyleSheet(
            "color: palette(mid); font-size: 13px; padding-bottom: 4px;"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form_widget = QWidget()
        form_layout = QVBoxLayout(form_widget)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(6)

        display_names = {
            field_name: display_name
            for field_name, display_name, _unit, _interval in COMBAT_ATTR_FIELDS
        }
        categories: dict[str, list[tuple[str, str, str]]] = {
            "攻击属性": [], "判定属性": [], "增益效果": [], "伤害加成": [],
        }
        judgement_fields = {
            "precision", "crit_rate", "intent_rate",
            "direct_crit", "direct_intent",
        }
        for _group, fields in self._get_resolved_fields():
            for field_name, fallback_label, unit in fields:
                display_label = display_names.get(field_name, fallback_label)
                item = (field_name, display_label, unit)
                if field_name.startswith(("min_", "max_")):
                    categories["攻击属性"].append(item)
                elif field_name in judgement_fields:
                    categories["判定属性"].append(item)
                elif field_name.endswith("_pen"):
                    categories["增益效果"].append(item)
                else:
                    categories["伤害加成"].append(item)

        judgement_by_field = {
            field_name: item
            for item in categories["判定属性"]
            for field_name in (item[0],)
        }
        categories["判定属性"] = [
            judgement_by_field["precision"], ("", "", ""),
            judgement_by_field["crit_rate"], judgement_by_field["direct_crit"],
            judgement_by_field["intent_rate"], judgement_by_field["direct_intent"],
        ]

        for section_title, fields in categories.items():
            subtitle = tr("三率填写白字") if section_title == "判定属性" else ""
            card = self._create_input_section(
                tr(section_title), fields, subtitle=subtitle
            )
            form_layout.addWidget(card)
        layout.addWidget(form_widget)

        name_row = QHBoxLayout()
        name_label = QLabel(tr("玩法名称"))
        name_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        name_row.addWidget(name_label)
        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText(tr("输入玩法名称"))
        self._edit_name.setMaxLength(20)
        self._edit_name.setMinimumHeight(32)
        name_row.addWidget(self._edit_name)
        layout.addLayout(name_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save |
            QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("取消"))
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_input_section(self, title: str,
                              fields: list[tuple[str, str, str]],
                              subtitle: str = "") -> QFrame:
        card = QFrame()
        card.setObjectName("combatAttrCard")
        card.setStyleSheet(_CARD_STYLE)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 7, 12, 8)
        card_layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setStyleSheet(_TITLE_STYLE)
        header_layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setStyleSheet(
                "font-size: 12px; font-weight: normal; color: palette(mid);"
            )
            header_layout.addWidget(subtitle_label)
        header_layout.addStretch()
        card_layout.addLayout(header_layout)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(3)
        for index, (field_name, display_label, unit) in enumerate(fields):
            if not field_name:
                continue
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(8)

            if field_name.endswith("_pen"):
                display_label = tr("{label}（基础值）").format(label=display_label)
            if unit:
                display_label = tr("{label}（{unit}）").format(
                    label=display_label, unit=unit
                )
            label = QLabel(tr(display_label))
            label.setStyleSheet(_NAME_STYLE)
            edit = QLineEdit()
            edit.setAlignment(Qt.AlignmentFlag.AlignRight)
            edit.setFixedWidth(130)
            edit.setFixedHeight(26)
            validator = QDoubleValidator(-999999.0, 999999.0, 2, edit)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            validator.setLocale(QLocale.c())
            edit.setValidator(validator)
            edit.setPlaceholderText("")
            cell_layout.addWidget(label)
            cell_layout.addStretch()
            cell_layout.addWidget(edit)
            grid.addWidget(cell, index // 2, index % 2)
            self._edits[field_name] = edit

        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card_layout.addLayout(grid)
        return card

    def _on_save(self):
        """保存按钮"""
        if not self._edit_name.text().strip():
            QMessageBox.warning(self, tr("名称为空"), tr("玩法名称不能为空"))
            return
        self.accept()

    def get_panel_attrs(self) -> CombatAttributes:
        """获取用户输入的面板属性"""
        attrs = CombatAttributes()
        for _, fields in self._get_resolved_fields():
            for fn, _, unit in fields:
                edit = self._edits.get(fn)
                if not edit:
                    continue
                text = edit.text().strip()
                value = float(text) if text else 0.0
                if unit == "%":
                    value = value / 100.0
                setattr(attrs, fn, value)
        return attrs

    def get_play_style_name(self) -> str:
        """获取玩法名称"""
        return self._edit_name.text().strip()
