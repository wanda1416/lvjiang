"""燕云「战斗属性」Tab

展示角色最终战斗属性（基础属性 + 装备 + 弓玦），支持：
- 选择流派 / 基础属性 / 弓玦 / 方案
- 创建基础属性（弹出对话框输入面板属性，反推基础属性并保存）
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr
from ....core.combat.combat_attrs import (
    COMBAT_ATTR_FIELDS,
    CombatAttributes,
    compute_gongjue_attrs,
    format_value,
)
from ...profile.tab import REFRESH_BTN_STYLE as _REFRESH_BTN_STYLE
from ...profile.tab import add_user_nav_buttons
from .cards import CombatCardsMixin
from .graduation import CombatGraduationMixin
from .layout import (
    DISPLAY_MODE_FULL,
    DISPLAY_MODE_HALF_COMPACT,
    CombatLayoutMixin,
)
from .layout_strategies import CardLayoutStrategy, FullCardLayout
from .play_style_dialog import PlayStyleDialogMixin

# 攻击属性字段：显示时四舍五入取整
_ATTACK_FIELDS = frozenset({
    "min_outer", "max_outer",
    "min_mingjin", "max_mingjin",
    "min_lieshi", "max_lieshi",
    "min_pozhu", "max_pozhu",
    "min_qiansi", "max_qiansi",
    "min_wuxiang", "max_wuxiang",
})

# 弓玦类型候选
_GONGJUE_TYPES = ["", "会意", "精准", "会心"]

# ─── 战斗属性展示模式 ─────────────────────────────────────────
# 常量已从 layout.py 导入，此处不再重复定义

_YELLOW_VALUE_COLOR = "#D97706"


class CombatAttrsTab(CombatCardsMixin, CombatGraduationMixin, CombatLayoutMixin, PlayStyleDialogMixin, QWidget):
    """战斗属性 Tab"""

    def __init__(self, host, parent=None):
        super().__init__(parent)
        self._host = host
        self._graduation_generation = 0
        self._pending_graduation: tuple[int, str, str, str, CombatAttributes] | None = None
        self._graduation_timer = QTimer(self)
        self._graduation_timer.setSingleShot(True)
        self._graduation_timer.setInterval(180)
        self._graduation_timer.timeout.connect(self._start_graduation_task)
        # 战斗属性展示模式：DISPLAY_MODE_FULL / DISPLAY_MODE_HALF / DISPLAY_MODE_HALF_COMPACT
        self._display_mode: str = DISPLAY_MODE_FULL
        self._strategy: CardLayoutStrategy = FullCardLayout()
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── 公共工具栏：与其他角色信息 Tab 保持位置和样式一致 ──
        self._toolbar_widget = QWidget()
        btn_row = QHBoxLayout(self._toolbar_widget)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_refresh = QPushButton(tr("刷新"))
        btn_refresh.setFixedWidth(60)
        btn_refresh.setToolTip(tr("刷新战斗属性"))
        btn_refresh.setStyleSheet(_REFRESH_BTN_STYLE)
        btn_refresh.clicked.connect(self._on_refresh)
        btn_row.addWidget(btn_refresh)
        add_user_nav_buttons(btn_row, self._host)
        btn_row.addStretch()
        layout.addWidget(self._toolbar_widget)

        # ── 当前配置 ──
        select_group = QGroupBox(tr("当前配置"))
        self._select_group = select_group
        select_layout = QGridLayout(select_group)
        self._select_layout = select_layout
        select_layout.setContentsMargins(14, 14, 14, 12)
        select_layout.setSpacing(8)

        school_field = QWidget()
        school_layout = QHBoxLayout(school_field)
        school_layout.setContentsMargins(0, 0, 0, 0)
        school_layout.addWidget(QLabel(tr("流派")))
        self._combo_school = QComboBox()
        self._combo_school.setFixedWidth(84)  # 4 汉字宽度
        self._combo_school.setMinimumHeight(30)
        self._combo_school.currentTextChanged.connect(self._on_school_changed)
        school_layout.addWidget(self._combo_school, 1)

        base_field = QWidget()
        base_layout = QHBoxLayout(base_field)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.addWidget(QLabel(tr("属性")))
        self._combo_play_style = QComboBox()
        self._combo_play_style.setFixedWidth(104)  # 5 汉字宽度
        self._combo_play_style.setMinimumHeight(30)
        self._combo_play_style.currentTextChanged.connect(self._on_play_style_changed)
        base_layout.addWidget(self._combo_play_style, 1)

        self._btn_edit_play_style = QPushButton(tr("编辑属性"))
        self._btn_edit_play_style.setMinimumHeight(30)
        self._btn_edit_play_style.setToolTip(tr("在游戏配置中编辑当前属性"))
        self._btn_edit_play_style.clicked.connect(self._on_edit_play_style)

        gongjue_field = QWidget()
        gongjue_layout = QHBoxLayout(gongjue_field)
        gongjue_layout.setContentsMargins(0, 0, 0, 0)
        gongjue_layout.addWidget(QLabel(tr("弓玦")))
        self._combo_gongjue = QComboBox()
        self._combo_gongjue.setFixedWidth(84)  # 4 汉字宽度
        self._combo_gongjue.setMinimumHeight(30)
        self._combo_gongjue.addItem(tr("无"), "")
        for gongjue_type in _GONGJUE_TYPES[1:]:
            self._combo_gongjue.addItem(gongjue_type, gongjue_type)
        self._combo_gongjue.currentTextChanged.connect(self._on_gongjue_changed)
        gongjue_layout.addWidget(self._combo_gongjue, 1)

        scheme_field = QWidget()
        scheme_layout = QHBoxLayout(scheme_field)
        scheme_layout.setContentsMargins(0, 0, 0, 0)
        scheme_layout.addWidget(QLabel(tr("方案")))
        self._combo_scheme = QComboBox()
        self._combo_scheme.setFixedWidth(104)  # 5 汉字宽度
        self._combo_scheme.setMinimumHeight(30)
        self._combo_scheme.currentTextChanged.connect(self._on_scheme_changed)
        scheme_layout.addWidget(self._combo_scheme, 1)

        self._btn_create_play = QPushButton(tr("新建属性"))
        self._btn_create_play.setMinimumHeight(30)
        self._btn_create_play.clicked.connect(self._on_create_play_style)

        self._config_fields = (
            school_field, base_field, self._btn_edit_play_style,
            gongjue_field, scheme_field, self._btn_create_play,
        )
        for col, widget in enumerate(self._config_fields):
            select_layout.addWidget(widget, 0, col)
        for col in range(6):
            select_layout.setColumnStretch(col, 1)

        self._config_row = QWidget()
        config_row_layout = QHBoxLayout(self._config_row)
        config_row_layout.setContentsMargins(0, 0, 0, 0)
        config_row_layout.setSpacing(4)
        config_row_layout.addWidget(select_group, 1)
        layout.addWidget(self._config_row)

        # ── 显示选项（独立区域） ──
        display_options_group = QGroupBox(tr("显示选项"))
        display_options_layout = QHBoxLayout(display_options_group)
        display_options_layout.setContentsMargins(14, 8, 14, 8)
        self._chk_resistance_only = QCheckBox(tr("仅黄字"))
        self._chk_resistance_only.setToolTip(
            tr("勾选后，判定属性和增益效果直接展示抗性后数值，节省空间")
        )
        self._chk_resistance_only.stateChanged.connect(self._refresh_display)
        self._chk_resistance_only.stateChanged.connect(lambda _: self._save_selection())
        display_options_layout.addWidget(self._chk_resistance_only)
        self._chk_full_chengyin = QCheckBox(tr("满承音"))
        self._chk_full_chengyin.setToolTip(
            tr("将已装备的承音装备词条数值视为承音上限参与计算"))
        self._chk_full_chengyin.stateChanged.connect(self._refresh_display)
        self._chk_full_chengyin.stateChanged.connect(lambda _: self._save_selection())
        display_options_layout.addWidget(self._chk_full_chengyin)
        self._chk_full_dingyin = QCheckBox(tr("满定音"))
        self._chk_full_dingyin.setToolTip(
            tr("将已装备的定音词条数值视为上限（100%）参与计算"))
        self._chk_full_dingyin.stateChanged.connect(self._refresh_display)
        self._chk_full_dingyin.stateChanged.connect(lambda _: self._save_selection())
        display_options_layout.addWidget(self._chk_full_dingyin)
        self._chk_full_level = QCheckBox(tr("满等级"))
        self._chk_full_level.setToolTip(
            tr("将低于最高等级的装备视为最高等级（提升基础属性），词条/定音数值由满承音/满定音决定"))
        self._chk_full_level.stateChanged.connect(self._refresh_display)
        self._chk_full_level.stateChanged.connect(lambda _: self._save_selection())
        display_options_layout.addWidget(self._chk_full_level)
        display_options_layout.addStretch()
        layout.addWidget(display_options_group)

        # DPS / 毕业率已由顶部公共区域展示

        # ── 属性展示区（主题一致的中性数据卡片） ──
        scroll = QScrollArea()
        self._attrs_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        self._attrs_widget = QWidget()
        self._main_layout = QHBoxLayout(self._attrs_widget)
        self._main_layout.setContentsMargins(2, 10, 2, 2)
        self._main_layout.setSpacing(12)

        self._attr_labels: dict[str, QLabel] = {}
        grid = QGridLayout()
        grid.setSpacing(12)
        self._main_layout.addLayout(grid, stretch=1)

        # 卡片创建时加入临时布局，随后由网格重新定位
        tmp = QVBoxLayout()
        self._attack_card = self._add_attack_card(tmp)
        grid.addWidget(self._attack_card, 0, 0)

        # ── 判定属性卡片 ──
        self._judgment_card = self._add_judgment_card(tmp)
        grid.addWidget(self._judgment_card, 1, 0)

        # ── 增益效果（含动态专项增益） ──
        self._gain_card = self._add_gain_card(tmp)
        grid.addWidget(self._gain_card, 0, 1)

        # ── 增伤效果卡片 ──
        self._damage_card = self._add_damage_card(tmp)
        grid.addWidget(self._damage_card, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        scroll.setWidget(self._attrs_widget)
        layout.addWidget(scroll, stretch=1)

        # 订阅装备变更信号（装备数据 Tab 中穿戴/卸下装备时触发）
        self._host.equipment_changed.connect(self._refresh_display)
        # 订阅用户切换信号（上一个/下一个用户按钮触发）
        self._host.user_changed.connect(self._on_user_changed)


    # ── 数据加载 ──────────────────────────────────────────────

    def _load_data(self):
        """加载数据并刷新显示"""
        self._refresh_schools()
        self._refresh_play_styles()
        self._refresh_schemes()
        self._restore_selection()
        self._refresh_display()

    def _refresh_schools(self):
        """刷新流派下拉"""
        from ....config import get_game_config
        gc = get_game_config()
        schools = gc.get_schools()

        self._combo_school.blockSignals(True)
        self._combo_school.clear()
        self._combo_school.addItem("")
        for name in schools:
            self._combo_school.addItem(name)
        self._combo_school.blockSignals(False)

    def _refresh_play_styles(self):
        """刷新基础属性下拉（根据当前选择的流派）。"""
        from ....config import get_play_styles

        school = self._get_current_school()
        self._combo_play_style.blockSignals(True)
        self._combo_play_style.clear()

        if school:
            play_styles = get_play_styles(school)
            for name in play_styles:
                self._combo_play_style.addItem(name)

        self._combo_play_style.blockSignals(False)
        self._btn_edit_play_style.setEnabled(
            bool(school and self._combo_play_style.currentText())
        )

    def _refresh_schemes(self):
        """刷新当前流派的毕业率方案；有配置时默认选中第一项。"""
        from ....config import get_game_config

        school = self._get_current_school()
        self._combo_scheme.blockSignals(True)
        self._combo_scheme.clear()
        if school:
            for name in get_game_config().get_graduation_schemes(school):
                self._combo_scheme.addItem(name)
        self._combo_scheme.blockSignals(False)

    def _get_current_school(self) -> str | None:
        """获取当前选择的流派"""
        school = self._combo_school.currentText()
        return school if school else None

    def _on_school_changed(self, _school: str):
        """流派切换 → 刷新基础属性和方案下拉。"""
        self._refresh_play_styles()
        self._refresh_schemes()
        self._refresh_display()
        self._save_selection()

    def _on_play_style_changed(self, _name: str):
        """基础属性切换。"""
        self._btn_edit_play_style.setEnabled(
            bool(_name and self._get_current_school())
        )
        self._refresh_display()
        self._save_selection()

    def _on_edit_play_style(self) -> None:
        """打开游戏配置，并定位到当前流派的当前基础属性。"""
        school = self._get_current_school()
        play_style = self._combo_play_style.currentText()
        if not school or not play_style:
            QMessageBox.warning(self, tr("无法编辑"), tr("请先选择属性"))
            return
        from ...game_settings import GameConfigDialog

        dialog = GameConfigDialog(parent=self._host)
        dialog.select_school_base_attr(school, play_style)
        dialog.exec()
        self._refresh_play_styles()
        index = self._combo_play_style.findText(play_style)
        if index >= 0:
            self._combo_play_style.setCurrentIndex(index)
        self._refresh_display()

    def _on_gongjue_changed(self, _gongjue: str):
        """弓玦切换"""
        self._refresh_display()
        self._save_selection()

    def _on_scheme_changed(self, _scheme: str):
        """毕业率方案切换。"""
        self._refresh_display()

    def _on_user_changed(self, _name: str) -> None:
        """切换用户时立即废弃尚未完成的毕业率请求。"""
        self._graduation_generation += 1
        self._graduation_timer.stop()
        self._pending_graduation = None
        self._load_data()
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
            "scheme": self._combo_scheme.currentText(),
            "resistance_only": self._chk_resistance_only.isChecked(),
            "full_chengyin": self._chk_full_chengyin.isChecked(),
            "full_dingyin": self._chk_full_dingyin.isChecked(),
            "full_level": self._chk_full_level.isChecked(),
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
            scheme = selection.get("scheme", "")
            resistance_only = selection.get("resistance_only", False)

            # 恢复流派
            if school:
                idx = self._combo_school.findText(school)
                if idx >= 0:
                    self._combo_school.setCurrentIndex(idx)
                    self._refresh_play_styles()
                    self._refresh_schemes()

            # 恢复基础属性
            if play_style:
                idx = self._combo_play_style.findText(play_style)
                if idx >= 0:
                    self._combo_play_style.setCurrentIndex(idx)

            # 恢复弓玦
            if gongjue:
                idx = self._combo_gongjue.findData(gongjue)
                if idx >= 0:
                    self._combo_gongjue.setCurrentIndex(idx)

            # 恢复方案；不存在或失效时保留第一项
            if scheme:
                idx = self._combo_scheme.findText(scheme)
                if idx >= 0:
                    self._combo_scheme.setCurrentIndex(idx)

            # 恢复仅展示抗性结果
            self._chk_resistance_only.setChecked(resistance_only)

            # 恢复满承音/满定音/满等级
            self._chk_full_chengyin.setChecked(
                selection.get("full_chengyin", False))
            self._chk_full_dingyin.setChecked(
                selection.get("full_dingyin", False))
            self._chk_full_level.setChecked(
                selection.get("full_level", False))
        except Exception as e:
            logger.debug(f"恢复战斗属性选择失败: {e}")

    # ── 属性展示 ──────────────────────────────────────────────

    @staticmethod
    def _current_resistances() -> tuple[float, float]:
        """读取等级配置中最高等级的判定抗性与增益抗性。"""
        from ....config import get_game_config

        configs = get_game_config().get_level_configs()
        if not configs:
            return 0.0, 0.0
        config = max(configs, key=lambda item: item.level)
        return float(config.judge_resistance or 0), float(config.buff_resistance or 0)

    def _refresh_display(self):
        """刷新属性显示"""
        from ....core.combat.combat_attrs import (
            WUXIANG_TO_ATTR_PEN,
            apply_bonus_resistance,
            apply_hypothetical_caps,
            apply_penetration_resistance,
            apply_three_rate_resistance,
            build_graduation_attrs,
            has_resistance,
            is_penetration_field,
            is_three_rate_field,
        )
        from ....core.combat.equipment import EquipmentInventory

        # 一次性加载并变换装备数据，避免重复读取仓库
        user_name = self._host.active_user_name()
        equipped = None
        if user_name:
            try:
                equipped = EquipmentInventory(user_name).equipped
                equipped = apply_hypothetical_caps(
                    equipped,
                    full_chengyin=self._chk_full_chengyin.isChecked(),
                    full_dingyin=self._chk_full_dingyin.isChecked(),
                    full_level=self._get_full_level(),
                )
            except Exception as e:
                logger.error(f"读取装备数据失败: {e}")

        # 一次性计算所有中间结果
        base_attrs = self._get_base_attrs()
        equip_base_attrs = self._compute_equip_base_attrs(equipped)
        equip_attrs = self._compute_equip_attrs(equipped)
        gongjue_attrs = self._compute_gongjue_attrs()
        combat_attrs = base_attrs + equip_base_attrs + equip_attrs + gongjue_attrs
        judge_resistance, buff_resistance = self._current_resistances()

        # 处理无相穿透转换：根据流派属性转换为对应的属攻穿透
        if equip_attrs.wuxiang_pen > 0:
            school = self._get_current_school()
            if school:
                from ....config import get_game_config
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
                        capped = apply_three_rate_resistance(
                            field_name, value, judge_resistance,
                        )
                    elif is_penetration_field(field_name):
                        # 穿透类：基础配置值 + 装备定音 / 1.15
                        base_val = getattr(base_attrs, field_name, 0.0)
                        equip_val = getattr(equip_attrs, field_name, 0.0)
                        capped = apply_penetration_resistance(
                            equip_val, base_val, buff_resistance,
                        )
                        # 显示：原始值(生效值)，其中原始值 = 基础 + 装备
                        original = base_val + equip_val
                        self._set_resistance_text(
                            label, original, capped, unit, force_decimal=True,
                        )
                        continue
                    else:
                        # 增伤类：整个值 / 1.15
                        capped = apply_bonus_resistance(
                            value, resistance=buff_resistance,
                        )
                    self._set_resistance_text(label, value, capped, unit)
                else:
                    if field_name in _ATTACK_FIELDS:
                        value = round(value)
                    label.setText(format_value(value, unit))

        self._refresh_attr_penetration(base_attrs, equip_attrs, buff_resistance)
        self._refresh_attr_bonus(combat_attrs)
        self._refresh_extra_attrs(combat_attrs.extra_attrs, buff_resistance)
        school = self._get_current_school()
        graduation_attrs = build_graduation_attrs(
            base_attrs + gongjue_attrs,
            equip_base_attrs + equip_attrs,
            school,
        )
        self._schedule_graduation(graduation_attrs)

        # 刷新退化模式：委托策略重新应用紧凑布局
        if self._display_mode == DISPLAY_MODE_HALF_COMPACT:
            self._strategy.on_refresh_display(self)

    def _refresh_attr_bonus(self, combat_attrs: CombatAttributes) -> None:
        """显示当前流派属攻伤害加成，并提供四系悬浮明细。"""
        from ....config import get_game_config
        from ....core.combat.combat_attrs import SCHOOL_ATTR_FIELD_MAP

        attr_fields = (
            ("鸣金", "mingjin_bonus"),
            ("裂石", "lieshi_bonus"),
            ("破竹", "pozhu_bonus"),
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

    def _refresh_attr_penetration(
        self,
        base_attrs: CombatAttributes,
        equip_attrs: CombatAttributes,
        buff_resistance: float,
    ) -> None:
        """显示当前流派属攻穿透，悬浮时展示完整四系明细。"""
        from ....config import get_game_config
        from ....core.combat.combat_attrs import (
            SCHOOL_ATTR_FIELD_MAP,
            apply_penetration_resistance,
        )

        attr_fields = (
            ("鸣金", "mingjin_pen"),
            ("裂石", "lieshi_pen"),
            ("破竹", "pozhu_pen"),
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
            effective = apply_penetration_resistance(
                equip_value, base_value, buff_resistance,
            )
            values[field] = (original, effective)
            details.append(
                f"{name}穿透：{original:.2f}"
                f"({effective:.2f})"
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

        self._set_resistance_text(
            self._attr_pen_label, original, effective, "", force_decimal=True,
        )
        tooltip = "\n".join(details)
        self._attr_pen_name.setToolTip(tooltip)
        self._attr_pen_label.setToolTip(tooltip)

    def _set_resistance_text(
        self, label: QLabel, original: float, effective: float,
        unit: str, *, force_decimal: bool = False,
    ) -> None:
        """按游戏的白字 (黄字) 语义显示原始值与抗性后数值。

        当勾选"仅展示抗性结果"时，直接展示黄字（抗性后数值）。
        force_decimal: 强制保留两位小数（用于穿透类）。
        """
        if force_decimal:
            original_text = f"{original:.2f}"
            effective_text = f"{effective:.2f}"
        else:
            original_text = format_value(original, unit)
            effective_text = format_value(effective, unit)
        if self._chk_resistance_only.isChecked():
            # 仅展示抗性结果（黄字）
            label.setText(
                f"<span style='color:{_YELLOW_VALUE_COLOR};'>"
                f"{effective_text}</span>"
            )
        else:
            # 白字 (黄字) 格式
            label.setText(
                f"{original_text}(<span style='color:{_YELLOW_VALUE_COLOR};'>"
                f"{effective_text}</span>)"
            )

    def _refresh_extra_attrs(
        self, extra_attrs: dict[str, float], buff_resistance: float,
    ) -> None:
        """按配置词组分类动态增益，技能定音按需扩展到第七行。"""
        from ....config import get_game_config
        from ....core.combat.combat_attrs import apply_bonus_resistance, has_resistance

        self._extra_labels.clear()

        weapon_items: list[tuple[str, float]] = []
        skill_items: list[tuple[str, float]] = []
        game_config = get_game_config()
        for key, value in sorted(extra_attrs.items()):
            if game_config.resolve_affix_category(key) == "指定技能增效":
                skill_items.append((key, value))
            else:
                weapon_items.append((key, value))

        # 常态只保留第六行两个槽位；第三、第四个技能定音出现时，
        # 才创建并显示第七行。
        if len(skill_items) > 2 and len(self._skill_bonus_slots) < 4:
            for col in range(2):
                slot = self._create_dynamic_slot(self._gain_grid, 6, col)
                self._skill_bonus_slots.append(slot)
        show_seventh_row = len(skill_items) > 2
        for index, (widget, name_label, value_label) in enumerate(
                self._weapon_bonus_slots + self._skill_bonus_slots):
            name_label.clear()
            value_label.clear()
            widget.setToolTip("")
            if index >= len(self._weapon_bonus_slots) + 2:
                widget.setVisible(show_seventh_row)

        def fill_slots(items: list[tuple[str, float]], slots) -> None:
            overflow = items[len(slots):]
            overflow_tip = "\n".join(
                f"{key}：{format_value(value, '%')}" for key, value in overflow
            )
            for (key, value), (widget, name_label, val_label) in zip(
                items, slots, strict=False,
            ):
                name_label.setText(tr(key))
                if has_resistance(key):
                    capped = apply_bonus_resistance(
                        value, resistance=buff_resistance,
                    )
                    self._set_resistance_text(val_label, value, capped, "%")
                else:
                    val_label.setText(format_value(value, "%"))
                if overflow_tip:
                    widget.setToolTip(tr("其他增益：\n") + overflow_tip)
                self._extra_labels[key] = val_label

        fill_slots(weapon_items, self._weapon_bonus_slots)
        fill_slots(skill_items, self._skill_bonus_slots)

        # 隐藏没有内容的动态槽位，避免单列模式产生空行
        for widget, name_label, _ in self._weapon_bonus_slots + self._skill_bonus_slots:
            widget.setVisible(bool(name_label.text()))

    def _get_base_attrs(self) -> CombatAttributes:
        """获取当前选择的基础属性。"""
        play_style = self._combo_play_style.currentText()
        if not play_style:
            return CombatAttributes()

        school = self._get_current_school()
        if not school:
            return CombatAttributes()

        from ....config import get_play_styles
        play_styles = get_play_styles(school)

        if play_style not in play_styles:
            return CombatAttributes()

        return CombatAttributes.from_dict(play_styles[play_style])

    def _compute_equip_attrs(
        self, equipped: dict | None = None,
    ) -> CombatAttributes:
        """计算装备词条属性总和（含五维转换，不含装备基础攻击值）

        Args:
            equipped: 已变换的装备数据；为 None 时从仓库加载。
        """
        from ....core.combat.combat_attrs import (
            aggregate_equipment_attrs,
            apply_hypothetical_caps,
        )
        from ....core.combat.equipment import EquipmentInventory

        user_name = self._host.active_user_name()
        if not user_name:
            return CombatAttributes()

        try:
            if equipped is None:
                equipped = EquipmentInventory(user_name).equipped
                equipped = apply_hypothetical_caps(
                    equipped,
                    full_chengyin=self._chk_full_chengyin.isChecked(),
                    full_dingyin=self._chk_full_dingyin.isChecked(),
                    full_level=self._get_full_level(),
                )
            return aggregate_equipment_attrs(equipped)
        except Exception as e:
            logger.error(f"读取装备数据失败: {e}")
            return CombatAttributes()

    def _compute_equip_base_attrs(
        self, equipped: dict | None = None,
    ) -> CombatAttributes:
        """计算装备基础外功攻击值（根据部位/等级/品阶）

        武器/环/佩 提供基础外功攻击，品阶不同数值不同。

        Args:
            equipped: 已变换的装备数据；为 None 时从仓库加载。
        """
        from ....config import get_game_config
        from ....core.combat.combat_attrs import compute_equip_base_attrs
        from ....core.combat.equipment import EquipmentInventory

        user_name = self._host.active_user_name()
        if not user_name:
            return CombatAttributes()

        try:
            if equipped is None:
                equipped = EquipmentInventory(user_name).equipped
            gc = get_game_config()
            return compute_equip_base_attrs(
                equipped,
                gc.get_base_attr_values,
            )
        except Exception as e:
            logger.error(f"计算装备基础攻击失败: {e}")
            return CombatAttributes()

    def _get_full_level(self) -> int:
        """返回满等级的目标等级；未勾选返回 0。"""
        if not self._chk_full_level.isChecked():
            return 0
        from ....config import get_game_config
        season = get_game_config().current_season()
        if season and season.equip_level:
            return season.equip_level
        # 兑底：无赛季配置时取等级配置最高项
        configs = get_game_config().get_level_configs()
        return configs[-1].level if configs else 0

    def _compute_gongjue_attrs(self) -> CombatAttributes:
        """计算弓玦属性：当前赛季最大等级三率词条上限的一半"""
        gongjue_type = self._get_current_gongjue()
        if not gongjue_type:
            return CombatAttributes()

        try:
            from ....config import get_game_config
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
