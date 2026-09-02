"""模拟装备创建/编辑对话框

允许用户创建虚拟装备。
承音为装备属性（复选框，默认不勾选）。
词条区提供三种数值填充模式（自定义/满数值/满承音），
用户切换词条名时根据所选模式自动填充数值。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ......i18n import tr
from ......ui.button_styles import apply_dialog_button_box_style

# 部位 → group_key 映射
_PART_TO_GROUP = {
    tr("武器"): "weapon",
    tr("环"): "ring",
    tr("佩"): "pendant",
    tr("冠胄"): "head",
    tr("胸甲"): "chest",
    tr("胫甲"): "leg",
    tr("腕甲"): "wrist",
}

# group_key → 部位显示名已移至 GameConfigManager.get_group_to_part()

# 品质选项
_QUALITY_OPTIONS = [
    (tr("金"), "gold"),
    (tr("紫"), "purple"),
]

# 词条数值填充模式
_MODE_CUSTOM = 0   # 自定义：不自动填充
_MODE_MAX_VAL = 1  # 满数值：填充 cap
_MODE_MAX_CY = 2   # 满承音：填充 chengyin

_DINGYIN_BUTTON_STYLE = (
    "QPushButton { text-align: left; padding: 5px 24px 5px 9px; "
    "border: 1px solid palette(mid); border-radius: 4px; "
    "background: palette(base); }"
    "QPushButton:hover { border-color: palette(highlight); }"
    "QPushButton::menu-indicator { image: none; subcontrol-origin: padding; "
    "subcontrol-position: bottom right; right: 6px; }"
)

_PRIMARY_BUTTON_STYLE = (
    "QPushButton { background: palette(highlight); color: palette(highlighted-text); "
    "border: 1px solid palette(highlight); border-radius: 5px; padding: 6px 18px; "
    "font-weight: 700; }"
    "QPushButton:hover { border-color: palette(text); }"
    "QPushButton:pressed { background: palette(dark); }"
)


class _AffixRow(QWidget):
    """单行词条编辑：词条名下拉 + 数值输入"""

    def __init__(self, index: int, affix_names: list[str], parent=None):
        super().__init__(parent)
        self._index = index
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 序号标签（宫商角徵羽，与定音标签等宽对齐）
        _AFFIX_LABELS = ["宫", "商", "角", "徵", "羽"]
        lbl_idx = QLabel(_AFFIX_LABELS[index - 1])
        lbl_idx.setFixedWidth(28)
        lbl_idx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_idx.setStyleSheet("color: palette(mid); font-size: 12px;")
        layout.addWidget(lbl_idx)

        # 词条名下拉
        self._combo_name = QComboBox()
        self._combo_name.setMinimumWidth(220)
        self._combo_name.addItem(tr("（空）"), "")
        for name in affix_names:
            self._combo_name.addItem(name, name)
        self._combo_name.currentIndexChanged.connect(self._on_name_changed)
        layout.addWidget(self._combo_name, 1)

        # 数值输入（加宽一个汉字宽度）
        self._spin_value = QDoubleSpinBox()
        self._spin_value.setFixedWidth(104)
        self._spin_value.setDecimals(1)
        self._spin_value.setRange(0.0, 9999.0)
        self._spin_value.setSingleStep(0.1)
        self._spin_value.valueChanged.connect(self._update_pct)
        layout.addWidget(self._spin_value)

        # ≤ 满值
        self._lbl_le = QLabel("≤")
        self._lbl_le.setFixedWidth(14)
        self._lbl_le.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_le.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self._lbl_le)

        self._lbl_cap_val = QLabel("")
        self._lbl_cap_val.setFixedWidth(50)
        self._lbl_cap_val.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self._lbl_cap_val)

        # X%（两个汉字宽度）
        self._lbl_pct = QLabel("")
        self._lbl_pct.setFixedWidth(56)
        self._lbl_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_pct.setStyleSheet("color: palette(mid); font-size: 11px;")
        layout.addWidget(self._lbl_pct)

        self._affix_names = affix_names
        self._cap = 0.0
        self._unit = ""

    def _on_name_changed(self, _index: int):
        """词条名改变时更新单位和上限，并根据模式预填数值"""
        self._refresh_cap_info()
        self._prefill_value()

    def _refresh_cap_info(self):
        """从 GameConfig 获取当前词条的上限和单位"""
        from ....config import get_game_config
        name = self._combo_name.currentData()
        if not name:
            self._lbl_cap_val.setText("")
            self._lbl_pct.setText("")
            self._cap = 0.0
            self._unit = ""
            return

        gc = get_game_config()
        # 需要从父级获取 level
        level = self._get_level()
        caps_info = gc.get_affix_caps(level, name)
        if caps_info:
            self._cap = caps_info["cap"]
            self._unit = caps_info.get("unit", "")
            self._lbl_cap_val.setText(str(self._cap))
        else:
            self._cap = 0.0
            self._unit = ""
            self._lbl_cap_val.setText("")
        self._update_pct()

    def _get_level(self) -> int:
        """获取父级对话框的等级设置"""
        dialog = self.window()
        if dialog is not None and hasattr(dialog, "_get_level"):
            return dialog._get_level()
        return 110

    def _get_affix_mode(self) -> int:
        """获取父级对话框的词条数值模式"""
        dialog = self.window()
        if dialog is not None and hasattr(dialog, "_get_affix_mode"):
            return dialog._get_affix_mode()
        return _MODE_CUSTOM

    def _prefill_value(self):
        """根据数值模式预填数值"""
        name = self._combo_name.currentData()
        if not name or self._cap <= 0:
            return
        from ....config import get_game_config
        caps_info = get_game_config().get_affix_caps(self._get_level(), name)
        if not caps_info:
            return
        mode = self._get_affix_mode()
        if mode == _MODE_MAX_CY:
            self._spin_value.setValue(caps_info["chengyin"])
        elif mode == _MODE_MAX_VAL:
            self._spin_value.setValue(caps_info["cap"])

    def _update_pct(self):
        """更新百分比标签"""
        value = self._spin_value.value()
        if self._cap > 0:
            self._lbl_pct.setText(f"{value / self._cap * 100:.1f}%")
        else:
            self._lbl_pct.setText("")

    def set_value(self, value: float):
        """设置数值"""
        self._spin_value.setValue(value)

    def get_data(self) -> dict | None:
        """获取词条数据"""
        name = self._combo_name.currentData()
        if not name:
            return None
        value = self._spin_value.value()
        result = {
            "name": name,
            "value": round(value, 1),
        }
        if self._unit:
            result["unit"] = self._unit
        # 计算 cap_pct
        if self._cap > 0:
            result["cap_pct"] = round(value / self._cap * 100, 1)
        return result

    def set_data(self, data: dict | None):
        """设置词条数据"""
        if not data:
            self._combo_name.setCurrentIndex(0)
            self._spin_value.setValue(0.0)
            return
        name = data.get("name", "")
        idx = self._combo_name.findData(name)
        if idx >= 0:
            self._combo_name.setCurrentIndex(idx)
        self._refresh_cap_info()
        self._spin_value.setValue(data.get("value", 0.0))


class MockEquipDialog(QDialog):
    """模拟装备创建/编辑对话框"""

    def __init__(
        self,
        equip_data: dict | None = None,
        parent=None,
        default_school: str = "",
    ):
        super().__init__(parent)
        self._equip_data = equip_data or {}
        self._is_edit = bool(equip_data)
        self._result_data: dict | None = None
        self._default_school = default_school  # 默认流派，用于右四件定音词条排序

        self.setWindowTitle(tr("编辑模拟装备") if self._is_edit else tr("创建模拟装备"))
        self.setMinimumSize(680, 620)
        self.resize(760, 700)
        self._init_ui()
        if self._is_edit:
            self._load_data()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        context = QLabel(
            tr("用于备战方案比较  ·  不会修改真实背包装备")
        )
        context.setProperty("tone", "muted")
        context.setStyleSheet("font-size: 12px;")
        layout.addWidget(context)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        # ── 基本信息 ──
        self._basic_group = QFrame()
        self._basic_group.setProperty("surface", "card")
        basic_card_layout = QVBoxLayout(self._basic_group)
        basic_card_layout.setContentsMargins(14, 11, 14, 13)
        basic_card_layout.setSpacing(8)
        basic_title = QLabel(tr("装备信息"))
        basic_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        basic_card_layout.addWidget(basic_title)
        self._basic_form = QFormLayout()
        self._basic_form.setContentsMargins(0, 0, 0, 0)
        self._basic_form.setHorizontalSpacing(12)
        self._basic_form.setVerticalSpacing(8)
        basic_card_layout.addLayout(self._basic_form)
        basic_layout = self._basic_form

        # 部位
        self._combo_part = QComboBox()
        for part_name in _PART_TO_GROUP.keys():
            self._combo_part.addItem(part_name, part_name)
        basic_layout.addRow(tr("部位:"), self._combo_part)

        # 武器类型（直接加入表单布局，与部位输入框对齐）
        self._lbl_weapon_type = QLabel(tr("类型:"))
        self._combo_weapon_type = QComboBox()
        self._refresh_weapon_types()
        basic_layout.insertRow(1, self._lbl_weapon_type, self._combo_weapon_type)
        self._combo_part.currentIndexChanged.connect(self._on_part_changed)
        # 初始化时同步可见性（默认部位是武器，需显示）
        self._on_part_changed(0)

        # 装备名称
        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText(tr("如：踏雪云珑"))
        basic_layout.addRow(tr("名称:"), self._edit_name)

        # 等级
        self._combo_level = QComboBox()
        from ....config import get_game_config
        for lvl in sorted([c.level for c in get_game_config().get_level_configs()], reverse=True):
            self._combo_level.addItem(str(lvl), lvl)
        basic_layout.addRow(tr("等级:"), self._combo_level)

        # 品质
        self._combo_quality = QComboBox()
        for label, value in _QUALITY_OPTIONS:
            self._combo_quality.addItem(label, value)
        basic_layout.addRow(tr("品质:"), self._combo_quality)

        # 承音（装备属性，默认不勾选）
        self._check_chengyin = QCheckBox(tr("承音"))
        basic_layout.addRow(tr("承音:"), self._check_chengyin)

        body_layout.addWidget(self._basic_group)

        # ── 词条编辑 ──
        affix_group = QFrame()
        affix_group.setProperty("surface", "card")
        affix_layout = QVBoxLayout(affix_group)
        affix_layout.setContentsMargins(14, 11, 14, 13)
        affix_layout.setSpacing(8)

        affix_header = QHBoxLayout()
        affix_title = QLabel(tr("词条配置"))
        affix_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        affix_header.addWidget(affix_title)
        affix_header.addStretch()
        affix_hint = QLabel(tr("宫为首词条 · 商角徵羽不可重复"))
        affix_hint.setProperty("tone", "muted")
        affix_hint.setStyleSheet("font-size: 11px;")
        affix_header.addWidget(affix_hint)
        affix_layout.addLayout(affix_header)

        # 词条数值模式（三个 radio，默认自定义）
        self._radio_mode_custom = QRadioButton(tr("自定义"))
        self._radio_mode_max_val = QRadioButton(tr("满数值"))
        self._radio_mode_max_cy = QRadioButton(tr("满承音"))
        self._affix_mode_group = QButtonGroup(self)
        self._affix_mode_group.addButton(self._radio_mode_custom)
        self._affix_mode_group.addButton(self._radio_mode_max_val)
        self._affix_mode_group.addButton(self._radio_mode_max_cy)
        self._radio_mode_custom.setChecked(True)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._radio_mode_custom)
        mode_row.addWidget(self._radio_mode_max_val)
        mode_row.addWidget(self._radio_mode_max_cy)
        mode_row.addStretch()
        mode_panel = QFrame()
        mode_panel.setProperty("status", "info")
        mode_panel_layout = QHBoxLayout(mode_panel)
        mode_panel_layout.setContentsMargins(10, 6, 10, 6)
        mode_label = QLabel(tr("数值填充"))
        mode_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        mode_panel_layout.addWidget(mode_label)
        mode_panel_layout.addLayout(mode_row)
        affix_layout.addWidget(mode_panel)
        for rb in (self._radio_mode_custom, self._radio_mode_max_val, self._radio_mode_max_cy):
            rb.toggled.connect(self._on_affix_mode_changed)

        # 词条冲突提示标签（红字），实际内容在按钮行左侧显示；先创建好，
        # 词条行的下拉信号可以立即连上，不用等按钮行布局出来。
        self._lbl_affix_warning = QLabel("")
        self._lbl_affix_warning.setProperty("status", "danger")
        self._lbl_affix_warning.setStyleSheet(
            "padding: 7px 10px; font-weight: 600; border-radius: 5px;")
        self._lbl_affix_warning.setWordWrap(True)
        self._lbl_affix_warning.setVisible(False)

        self._affix_rows: list[_AffixRow] = []
        # 宫（首词条）使用首词条候选，商角徵羽使用普通词条过滤
        first_affixes = self._get_first_affix_names()
        filtered_affixes = self._get_filtered_affix_names()
        for i in range(1, 6):
            affix_list = first_affixes if i == 1 else filtered_affixes
            row = _AffixRow(i, affix_list)
            self._affix_rows.append(row)
            # 只有词条名变化才可能影响约束，数值变化不需要重新校验。
            row._combo_name.currentIndexChanged.connect(self._refresh_affix_warning)
            affix_layout.addWidget(row)

        # 定音词条（与词条行对齐）
        dingyin_layout = QHBoxLayout()
        lbl_dingyin_idx = QLabel(tr("定音"))
        lbl_dingyin_idx.setFixedWidth(28)
        lbl_dingyin_idx.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_dingyin_idx.setStyleSheet("color: palette(mid); font-size: 12px;")
        dingyin_layout.addWidget(lbl_dingyin_idx)

        # 定音词条选择：级联菜单按钮（左三件平铺，右四件按流派分组）
        self._btn_dingyin = QPushButton(tr("（无）"))
        self._btn_dingyin.setMinimumWidth(220)
        self._btn_dingyin.setStyleSheet(_DINGYIN_BUTTON_STYLE)
        self._btn_dingyin.clicked.connect(self._show_dingyin_cascade_menu)
        dingyin_layout.addWidget(self._btn_dingyin, 1)

        self._spin_dingyin = QDoubleSpinBox()
        self._spin_dingyin.setFixedWidth(104)
        self._spin_dingyin.setDecimals(1)
        self._spin_dingyin.setRange(0.0, 999.0)
        self._spin_dingyin.valueChanged.connect(self._update_dingyin_pct)
        dingyin_layout.addWidget(self._spin_dingyin)

        self._lbl_dingyin_le = QLabel("≤")
        self._lbl_dingyin_le.setFixedWidth(14)
        self._lbl_dingyin_le.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_dingyin_le.setStyleSheet("color: palette(mid); font-size: 11px;")
        dingyin_layout.addWidget(self._lbl_dingyin_le)

        self._lbl_dingyin_cap_val = QLabel("")
        self._lbl_dingyin_cap_val.setFixedWidth(50)
        self._lbl_dingyin_cap_val.setStyleSheet(
            "color: palette(mid); font-size: 11px;")
        dingyin_layout.addWidget(self._lbl_dingyin_cap_val)

        self._lbl_dingyin_pct = QLabel("")
        self._lbl_dingyin_pct.setFixedWidth(56)
        self._lbl_dingyin_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._lbl_dingyin_pct.setStyleSheet("color: palette(mid); font-size: 11px;")
        dingyin_layout.addWidget(self._lbl_dingyin_pct)

        self._dingyin_cap = 0.0
        self._dingyin_selected = ""  # 级联菜单选中的词条名
        affix_layout.addLayout(dingyin_layout)

        # 等级变化时刷新所有词条行的满值和比例
        self._combo_level.currentIndexChanged.connect(self._on_level_changed)

        affix_layout.addWidget(self._lbl_affix_warning)
        body_layout.addWidget(affix_group)
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        # ── 操作按钮 ──
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        apply_dialog_button_box_style(buttons)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            ok_button.setText(tr("保存修改") if self._is_edit else tr("创建装备"))
            ok_button.setStyleSheet(_PRIMARY_BUTTON_STYLE)
            ok_button.setMinimumHeight(32)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.setText(tr("取消"))
            cancel_button.setMinimumHeight(32)
        layout.addWidget(buttons)

        # 武器类型变化时过滤词条列表
        self._combo_weapon_type.currentIndexChanged.connect(
            self._on_weapon_type_changed)

        # 初始化定音词条按钮（所有控件创建完毕后刷新）
        self._refresh_dingyin_button()

    def _get_level(self) -> int:
        """获取当前选择的等级（供 _AffixRow 调用）"""
        return self._combo_level.currentData() or 110

    def _on_part_changed(self, _index: int):
        """部位改变时更新武器类型行可见性，并刷新词条候选"""
        part = self._combo_part.currentData()
        is_weapon = part == tr("武器")
        self._lbl_weapon_type.setVisible(is_weapon)
        self._combo_weapon_type.setVisible(is_weapon)
        # 部位变化时同步刷新普通词条和定音词条（初始化阶段跳过）
        if hasattr(self, '_affix_rows') and self._affix_rows:
            self._update_affix_rows()
        if hasattr(self, '_btn_dingyin'):
            self._dingyin_selected = ""
            self._refresh_dingyin_button()

    def _on_weapon_type_changed(self, _index: int):
        """武器类型改变时更新词条下拉列表"""
        if hasattr(self, "_affix_rows") and self._affix_rows:
            self._update_affix_rows()

    def _refresh_weapon_types(self):
        """刷新武器类型下拉"""
        from ....config import get_game_config
        self._combo_weapon_type.clear()
        for wt in get_game_config().get_weapon_types():
            self._combo_weapon_type.addItem(wt, wt)

    def _get_first_affix_names(self) -> list[str]:
        """获取当前部位的首词条候选列表（来自 base_attrs.<part>._first_affixes）"""
        from ....config import get_game_config
        gc = get_game_config()
        part_name = self._combo_part.currentData()
        if not part_name:
            return []
        group_key = _PART_TO_GROUP.get(part_name, "")
        if not group_key:
            return []
        return gc.get_first_affixes(group_key)

    def _get_filtered_affix_names(self) -> list[str]:
        """根据当前部位和武器类型过滤普通词条列表

        1. 武器类型过滤：武学增效词条只保留当前武器类型绑定的那条
        2. 部位过滤：只保留可出现在当前部位的词条
        """
        from ....config import get_game_config
        gc = get_game_config()
        all_normal = gc.get_normal_affix_names()

        # 武器类型过滤（武学增效）
        weapon_type = self._combo_weapon_type.currentData() or ""
        wuxue_affix = gc.get_weapon_wuxue_affix(weapon_type) if weapon_type else ""
        all_wuxue = set(gc.get_wuxue_affix_names())

        # 部位过滤
        part_name = self._combo_part.currentData()
        if not part_name:
            return [name for name in all_normal if name not in all_wuxue or name == wuxue_affix]

        group_key = _PART_TO_GROUP.get(part_name, "")
        if not group_key:
            return [name for name in all_normal if name not in all_wuxue or name == wuxue_affix]

        part_display = gc.get_group_to_part().get(group_key, "")
        if not part_display:
            return [name for name in all_normal if name not in all_wuxue or name == wuxue_affix]

        return [
            name for name in all_normal
            if (name not in all_wuxue or name == wuxue_affix)
            and part_display in gc.get_affix_parts(name)
        ]

    def _update_affix_rows(self):
        """武器类型或部位变化时重建所有词条行的下拉列表"""
        first_affixes = self._get_first_affix_names()
        filtered = self._get_filtered_affix_names()
        for i, row in enumerate(self._affix_rows):
            # 宫（首词条）使用首词条候选，商角徵羽使用普通词条过滤
            affix_list = first_affixes if i == 0 else filtered
            current = row._combo_name.currentData()
            row._combo_name.blockSignals(True)
            row._combo_name.clear()
            row._combo_name.addItem(tr("（空）"), "")
            for name in affix_list:
                row._combo_name.addItem(name, name)
            if current:
                idx = row._combo_name.findData(current)
                if idx >= 0:
                    row._combo_name.setCurrentIndex(idx)
                else:
                    row._combo_name.setCurrentIndex(0)
            row._combo_name.blockSignals(False)
            row._refresh_cap_info()
            row._update_pct()
        # 上面重建下拉时 blockSignals，选中项被静默清空/改变也不会触发
        # currentIndexChanged，这里手动补一次刷新。
        self._refresh_affix_warning()

    def _on_level_changed(self, _index: int):
        """等级变化时刷新所有词条行的满值和百分比"""
        for row in self._affix_rows:
            row._refresh_cap_info()
        self._update_dingyin_pct()

    def _get_normal_affix_names(self) -> list[str]:
        """获取普通词条列表"""
        from ....config import get_game_config
        return get_game_config().get_normal_affix_names()

    def _is_right_side_part(self) -> bool:
        """判断当前部位是否为右四件（冠胄/胸甲/胫甲/腕甲）"""
        part_name = self._combo_part.currentData()
        if not part_name:
            return False
        group_key = _PART_TO_GROUP.get(part_name, "")
        return group_key in ("head", "chest", "leg", "wrist")

    def _get_dingyin_groups(self) -> list[str]:
        """获取右四件定音词条的分组列表（指定技能增效的 _aliases keys）

        根据 default_school 排序：将包含流派名的分组提到最前。
        仅返回当前部位有合法词条的分组。
        """
        from ....config import get_game_config
        gc = get_game_config()
        # 获取指定技能增效分类的分组
        # "指定技能增效" 是 attributes.yaml 里 affix_caps 段的分类 key，
        # 裸中文的游戏配置数据，从不过 tr()，这里也不能过 tr()（同
        # equip_parser/dingyin_parser.py 的 _DINGYIN_CATEGORIES）。
        groups = gc.get_alias_groups_for_category("指定技能增效")
        if not groups:
            return []
        group_keys = list(groups.keys())

        # 过滤掉当前部位无合法词条的分组
        part_name = self._combo_part.currentData()
        if part_name:
            group_key_for_part = _PART_TO_GROUP.get(part_name, "")
            if group_key_for_part:
                part_display = gc.get_group_to_part().get(group_key_for_part, "")
                if part_display:
                    group_keys = [
                        g for g in group_keys
                        if any(
                            part_display in gc.get_affix_parts(a)
                            for a in groups[g]
                        )
                    ]

        # 根据 default_school 排序
        if self._default_school:
            # 将包含流派名的分组提到最前
            def sort_key(g: str) -> int:
                return 0 if self._default_school in g else 1
            group_keys.sort(key=sort_key)

        return group_keys

    def _get_dingyin_affixes_by_group(self, group_key: str) -> list[str]:
        """获取指定分组的定音词条列表（按当前部位过滤）"""
        from ....config import get_game_config
        gc = get_game_config()
        # "指定技能增效" 是 attributes.yaml 里 affix_caps 段的分类 key，
        # 裸中文的游戏配置数据，从不过 tr()，这里也不能过 tr()（同
        # equip_parser/dingyin_parser.py 的 _DINGYIN_CATEGORIES）。
        groups = gc.get_alias_groups_for_category("指定技能增效")
        affixes = groups.get(group_key, [])
        # 按当前部位过滤
        part_name = self._combo_part.currentData()
        if not part_name:
            return affixes
        group_key_for_part = _PART_TO_GROUP.get(part_name, "")
        if not group_key_for_part:
            return affixes
        part_display = gc.get_group_to_part().get(group_key_for_part, "")
        if not part_display:
            return affixes
        return [
            a for a in affixes
            if part_display in gc.get_affix_parts(a)
        ]

    def _get_dingyin_affixes_filtered(self) -> list[str]:
        """获取当前部位的定音词条列表（按部位过滤，平铺）"""
        from ....config import get_game_config
        gc = get_game_config()
        all_dingyin = gc.get_dingyin_affix_names()
        part_name = self._combo_part.currentData()
        if not part_name:
            return all_dingyin
        group_key = _PART_TO_GROUP.get(part_name, "")
        if not group_key:
            return all_dingyin
        part_display = gc.get_group_to_part().get(group_key, "")
        if not part_display:
            return all_dingyin
        return [
            name for name in all_dingyin
            if part_display in gc.get_affix_parts(name)
        ]

    def _refresh_dingyin_button(self):
        """刷高级联菜单按钮文本"""
        if hasattr(self, '_btn_dingyin'):
            self._btn_dingyin.setText(self._dingyin_selected or tr("（无）"))

    def _show_dingyin_cascade_menu(self):
        """显示定音词条级联菜单（左三件平铺，右四件按流派分组）"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: palette(base);
                border: 1px solid palette(mid);
                padding: 4px 0px;
            }
            QMenu::item {
                padding: 6px 32px 6px 20px;
                min-width: 120px;
            }
            QMenu::item:selected {
                background-color: #0078D7;
                color: white;
            }
            QMenu::separator {
                height: 1px;
                background: palette(midlight);
                margin: 4px 8px;
            }
        """)
        if self._is_right_side_part():
            # 右四件：按流派分组（指定技能增效）
            groups = self._get_dingyin_groups()
            for group_name in groups:
                affixes = self._get_dingyin_affixes_by_group(group_name)
                if not affixes:
                    continue
                submenu = menu.addMenu(group_name)
                for affix_name in affixes:
                    action = submenu.addAction(affix_name)
                    action.setData(affix_name)
                    action.triggered.connect(
                        lambda checked, n=affix_name: self._on_dingyin_affix_selected(n))
        else:
            # 左三件：平铺显示
            affixes = self._get_dingyin_affixes_filtered()
            for affix_name in affixes:
                action = menu.addAction(affix_name)
                action.setData(affix_name)
                action.triggered.connect(
                    lambda checked, n=affix_name: self._on_dingyin_affix_selected(n))
        # 添加“无”选项
        menu.addSeparator()
        none_action = menu.addAction(tr("（无）"))
        none_action.setData("")
        none_action.triggered.connect(lambda: self._on_dingyin_affix_selected(""))
        menu.exec(self._btn_dingyin.mapToGlobal(self._btn_dingyin.rect().bottomLeft()))

    def _on_dingyin_affix_selected(self, affix_name: str):
        """级联菜单选中词条"""
        self._dingyin_selected = affix_name
        self._refresh_dingyin_button()
        self._update_dingyin_pct()

    def _load_data(self):
        """加载已有数据到对话框"""
        data = self._equip_data
        # 部位
        part_name = data.get("type", "")
        from ....config import get_game_config
        gc = get_game_config()
        type_to_group = gc.get_type_to_group()
        # 根据 type 找到对应的对话框部位选择
        group = type_to_group.get(part_name, "")
        if group == "weapon":
            # 武器具体名称 → 对话框选"武器"，并选中具体武器类型
            idx = self._combo_part.findData(tr("武器"))
            if idx >= 0:
                self._combo_part.setCurrentIndex(idx)
            idx = self._combo_weapon_type.findText(part_name)
            if idx >= 0:
                self._combo_weapon_type.setCurrentIndex(idx)
        elif group:
            # 非武器部位
            dialog_part = gc.get_group_to_part().get(group, "")
            idx = self._combo_part.findData(dialog_part)
            if idx >= 0:
                self._combo_part.setCurrentIndex(idx)
        # 根据武器类型刷新词条下拉
        self._update_affix_rows()

        # 名称
        self._edit_name.setText(data.get("name", ""))

        # 等级
        level = data.get("level", 110)
        idx = self._combo_level.findData(level)
        if idx >= 0:
            self._combo_level.setCurrentIndex(idx)

        # 品质
        quality = data.get("quality", "gold")
        idx = self._combo_quality.findData(quality)
        if idx >= 0:
            self._combo_quality.setCurrentIndex(idx)

        # 承音
        self._check_chengyin.setChecked(bool(data.get("is_chengyin")))

        # 词条
        for i, row in enumerate(self._affix_rows, 1):
            row.set_data(data.get(f"affix_{i}"))

        # 定音
        dingyin = data.get("dingyin")
        if dingyin and dingyin.get("name"):
            dingyin_name = dingyin["name"]
            self._dingyin_selected = dingyin_name
            self._refresh_dingyin_button()
            self._spin_dingyin.setValue(dingyin.get("value", 0.0))
            self._update_dingyin_pct()

    def _update_dingyin_pct(self):
        """更新定音词条的满值和百分比标签"""
        name = self._dingyin_selected
        if not name:
            self._lbl_dingyin_cap_val.setText("")
            self._lbl_dingyin_pct.setText("")
            self._dingyin_cap = 0.0
            return
        from ....config import get_game_config
        gc = get_game_config()
        level = self._get_level()
        caps_info = gc.get_affix_caps(level, name)
        if caps_info and caps_info.get("cap"):
            self._dingyin_cap = caps_info["cap"]
            self._lbl_dingyin_cap_val.setText(str(self._dingyin_cap))
            value = self._spin_dingyin.value()
            self._lbl_dingyin_pct.setText(f"{value / self._dingyin_cap * 100:.1f}%")
        else:
            self._dingyin_cap = 0.0
            self._lbl_dingyin_cap_val.setText("")
            self._lbl_dingyin_pct.setText("")

    def _refresh_affix_warning(self, *_args):
        """词条名一变化就重新校验并把结果实时显示在按钮左侧（红字，不阻塞填写）。

        跟 _on_accept 共用同一份 _validate_affix_rules，保证「保存前提示的」
        和「保存时真正拦截的」永远是同一条规则、同一句话，不会两边不一致。
        """
        if not hasattr(self, "_lbl_affix_warning"):
            return  # 初始化阶段词条行尚未创建完，信号还没接好前不会走到这里
        result = self._build_equip_data()
        error = self._validate_affix_rules(result) if result else None
        self._lbl_affix_warning.setText(error or "")
        self._lbl_affix_warning.setVisible(bool(error))

    def _on_accept(self):
        """确认按钮处理

        填写过程中不做任何约束（下拉候选不互相排除、不限制数量），只在点击
        确认保存时统一校验拦截；期间已经通过 _refresh_affix_warning 实时把
        同样的校验结果显示在按钮左侧，这里再拦一次是保存动作本身的最终把关。
        """
        name = self._edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, tr("提示"), tr("请输入装备名称"))
            return

        # 构建装备数据
        result = self._build_equip_data()
        if result is None:
            return

        error = self._validate_affix_rules(result)
        if error:
            QMessageBox.warning(self, tr("提示"), error)
            return

        self._result_data = result
        self.accept()

    def _validate_affix_rules(self, result: dict) -> str | None:
        """校验模拟装备是否为游戏可产出的组合，不合法返回中文提示。

        判定本身不在这里实现，统一走全局的
        :mod:`lvjiang.apps.yysls.core.equip_validator`，保证「创建模拟装备时
        拦下的」和「扫描真实装备时标记的」永远是同一套规则、同一句话。

        **数值超上限（cap_overflow）不在此拦截**：它不是组合非法，而是数值
        异常，允许保存但会被标记进 ``_extra.illegal_equip``，由装备卡片上的
        「!」提醒用户校正。其余（词条重复、属攻超量、神力超量、神力为转律
        产出）一律拦下，不保存、不关闭对话框。
        """
        from ....core.equip_validator import validate_combination_dict

        blocking = validate_combination_dict(result)
        return blocking[0].message if blocking else None

    def _build_equip_data(self) -> dict | None:
        """构建装备数据字典"""
        from ....config import get_game_config
        gc = get_game_config()

        # 确定类型
        part = self._combo_part.currentData()
        if part == tr("武器"):
            equip_type = self._combo_weapon_type.currentData() or "剑"
        else:
            equip_type = part

        level = self._combo_level.currentData() or 110
        quality = self._combo_quality.currentData() or "gold"
        is_chengyin = self._check_chengyin.isChecked()

        result = {
            "type": equip_type,
            "name": self._edit_name.text().strip(),
            "level": level,
            "quality": quality,
            "is_chengyin": is_chengyin,
        }

        # 基础属性（使用全局映射，part 可能是 "武器" 或具体部位名）
        group_key = gc.get_type_to_group().get(part, "ring")
        min_val, max_val = gc.get_base_attr_values(group_key, level, quality)
        if min_val is not None:
            if group_key == "weapon":
                # 武器有区间
                result["base_attr"] = {"name": tr("外功攻击"), "value": [min_val, max_val]}
            else:
                # 其他部位是点值
                base_attr_name = self._get_base_attr_name(group_key)
                result["base_attr"] = {"name": base_attr_name, "value": min_val}
        result["base_attr_2"] = None

        # 词条
        mode = self._get_affix_mode()
        for i, row in enumerate(self._affix_rows, 1):
            affix_data = row.get_data()
            if affix_data:
                # 根据模式填充数值
                if mode == _MODE_MAX_CY and "cap_pct" not in affix_data:
                    caps_info = gc.get_affix_caps(level, affix_data["name"])
                    if caps_info:
                        affix_data["value"] = caps_info["chengyin"]
                        affix_data["cap_pct"] = 94.0
                elif mode == _MODE_MAX_VAL and "cap_pct" not in affix_data:
                    caps_info = gc.get_affix_caps(level, affix_data["name"])
                    if caps_info:
                        affix_data["value"] = caps_info["cap"]
                        affix_data["cap_pct"] = 100.0
                result[f"affix_{i}"] = affix_data

        # 定音
        dingyin_name = self._dingyin_selected
        if dingyin_name:
            dingyin_value = round(self._spin_dingyin.value(), 1)
            dingyin_data = {
                "name": dingyin_name,
                "value": dingyin_value,
            }
            # 计算定音 cap_pct
            caps_info = gc.get_affix_caps(level, dingyin_name)
            if caps_info and caps_info.get("cap"):
                dingyin_data["cap_pct"] = round(
                    dingyin_value / caps_info["cap"] * 100, 1)
            result["dingyin"] = dingyin_data
        else:
            result["dingyin"] = None

        # _extra
        result["_extra"] = {"is_mock": True, "affix_count": sum(1 for i in range(1, 6) if f"affix_{i}" in result)}

        # 数值超上限只标记不拦截（组合类违规由 _on_accept 拦下，走不到这里保存）
        from ....core.equip_validator import ILLEGAL_KEY, validate_equipment_dict
        reasons = [r.message for r in validate_equipment_dict(result)]
        if reasons:
            result["_extra"][ILLEGAL_KEY] = reasons

        # _fp：模拟装备指纹自动添加 mock_ 前缀
        from ....core.equip_parser.models import make_fingerprint
        result["_fp"] = make_fingerprint(result, is_mock=True)

        return result

    def _get_affix_mode(self) -> int:
        """获取当前词条数值模式"""
        if self._radio_mode_max_val.isChecked():
            return _MODE_MAX_VAL
        elif self._radio_mode_max_cy.isChecked():
            return _MODE_MAX_CY
        return _MODE_CUSTOM

    def _on_affix_mode_changed(self, checked: bool):
        """词条数值模式切换时，立即更新已有词条的数值"""
        if not checked:
            return
        if not hasattr(self, '_affix_rows'):
            return
        mode = self._get_affix_mode()
        if mode == _MODE_CUSTOM:
            return
        from ....config import get_game_config
        gc = get_game_config()
        level = self._get_level()
        for row in self._affix_rows:
            name = row._combo_name.currentData()
            if not name:
                continue
            caps_info = gc.get_affix_caps(level, name)
            if not caps_info:
                continue
            if mode == _MODE_MAX_VAL:
                row._spin_value.setValue(caps_info["cap"])
            elif mode == _MODE_MAX_CY:
                row._spin_value.setValue(caps_info["chengyin"])
        # 定音词条也同步更新
        dingyin_name = self._dingyin_selected
        if dingyin_name:
            caps_info = gc.get_affix_caps(level, dingyin_name)
            if caps_info and caps_info.get("cap"):
                if mode == _MODE_MAX_VAL:
                    self._spin_dingyin.setValue(caps_info["cap"])
                elif mode == _MODE_MAX_CY:
                    self._spin_dingyin.setValue(caps_info["chengyin"])

    def _get_base_attr_name(self, group_key: str) -> str:
        """根据部位获取基础属性名"""
        name_map = {
            "ring": tr("最小外功攻击"),
            "pendant": tr("最大外功攻击"),
            "head": tr("气血最大值"),
            "chest": tr("气血最大值"),
            "leg": tr("气血最大值"),
            "wrist": tr("气血最大值"),
        }
        return name_map.get(group_key, tr("气血最大值"))

    def get_result(self) -> dict | None:
        """获取结果数据"""
        return self._result_data
