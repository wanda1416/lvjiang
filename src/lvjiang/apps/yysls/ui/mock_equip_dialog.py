"""模拟装备创建/编辑对话框

允许用户创建虚拟装备，支持承音/满值/自定义三种数值模式。
词条从该部位可用词条池中选择，数值根据模式自动填充。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ....i18n import tr

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

# 数值模式
_MODE_CHENGYIN = "chengyin"
_MODE_MAX = "max"
_MODE_CUSTOM = "custom"


class _AffixRow(QWidget):
    """单行词条编辑：词条名下拉 + 数值输入"""

    def __init__(self, index: int, affix_names: list[str], parent=None):
        super().__init__(parent)
        self._index = index
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 序号标签
        lbl_idx = QLabel(f"{index}.")
        lbl_idx.setFixedWidth(20)
        lbl_idx.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(lbl_idx)

        # 词条名下拉
        self._combo_name = QComboBox()
        self._combo_name.setFixedWidth(180)
        self._combo_name.addItem(tr("（空）"), "")
        for name in affix_names:
            self._combo_name.addItem(name, name)
        self._combo_name.currentIndexChanged.connect(self._on_name_changed)
        layout.addWidget(self._combo_name)

        # 数值输入
        self._spin_value = QDoubleSpinBox()
        self._spin_value.setFixedWidth(90)
        self._spin_value.setDecimals(1)
        self._spin_value.setRange(0.0, 9999.0)
        self._spin_value.setSingleStep(0.1)
        layout.addWidget(self._spin_value)

        # 单位标签
        self._lbl_unit = QLabel("")
        self._lbl_unit.setFixedWidth(20)
        self._lbl_unit.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self._lbl_unit)

        # cap_pct 标签
        self._lbl_pct = QLabel("")
        self._lbl_pct.setFixedWidth(50)
        self._lbl_pct.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self._lbl_pct)

        layout.addStretch()

        self._affix_names = affix_names
        self._cap = 0.0
        self._unit = ""

    def _on_name_changed(self, _index: int):
        """词条名改变时更新单位和上限，并根据模式预填数值"""
        self._refresh_cap_info()
        self._prefill_value()

    def _refresh_cap_info(self):
        """从 GameConfig 获取当前词条的上限和单位"""
        from ..config import get_game_config
        name = self._combo_name.currentData()
        if not name:
            self._lbl_unit.setText("")
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
            self._lbl_unit.setText(self._unit)
        else:
            self._cap = 0.0
            self._unit = ""
            self._lbl_unit.setText("")

    def _get_level(self) -> int:
        """获取父级对话框的等级设置"""
        dialog = self.window()
        if hasattr(dialog, "_get_level"):
            return dialog._get_level()
        return 110

    def _get_mode(self) -> str:
        """获取父级对话框的数值模式"""
        dialog = self.window()
        if hasattr(dialog, "_get_value_mode"):
            return dialog._get_value_mode()
        return _MODE_CUSTOM

    def _prefill_value(self):
        """根据数值模式预填数值"""
        name = self._combo_name.currentData()
        if not name or self._cap <= 0:
            return
        from ..config import get_game_config
        caps_info = get_game_config().get_affix_caps(self._get_level(), name)
        if not caps_info:
            return
        mode = self._get_mode()
        if mode == _MODE_CHENGYIN:
            self._spin_value.setValue(caps_info["chengyin"])
        elif mode == _MODE_MAX:
            self._spin_value.setValue(caps_info["cap"])

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

    def __init__(self, equip_data: dict | None = None, parent=None):
        super().__init__(parent)
        self._equip_data = equip_data or {}
        self._is_edit = bool(equip_data)
        self._result_data: dict | None = None

        self.setWindowTitle(tr("编辑模拟装备") if self._is_edit else tr("创建模拟装备"))
        self.setMinimumWidth(500)
        self._init_ui()
        if self._is_edit:
            self._load_data()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # ── 基本信息 ──
        basic_group = QGroupBox(tr("基本信息"))
        basic_layout = QFormLayout(basic_group)

        # 部位
        self._combo_part = QComboBox()
        for part_name in _PART_TO_GROUP.keys():
            self._combo_part.addItem(part_name, part_name)
        basic_layout.addRow(tr("部位:"), self._combo_part)

        # 武器类型（仅武器时显示，用容器包裹使 label + combo 整体显隐）
        self._weapon_type_container = QWidget()
        wt_layout = QHBoxLayout(self._weapon_type_container)
        wt_layout.setContentsMargins(0, 0, 0, 0)
        self._combo_weapon_type = QComboBox()
        self._refresh_weapon_types()
        wt_layout.addWidget(QLabel(tr("类型:")))
        wt_layout.addWidget(self._combo_weapon_type, stretch=1)
        basic_layout.addRow(self._weapon_type_container)
        self._combo_part.currentIndexChanged.connect(self._on_part_changed)
        # 初始化时同步可见性（默认部位是武器，需显示）
        self._on_part_changed(0)

        # 装备名称
        self._edit_name = QLineEdit()
        self._edit_name.setPlaceholderText(tr("如：踏雪云珑"))
        basic_layout.addRow(tr("名称:"), self._edit_name)

        # 等级
        self._combo_level = QComboBox()
        from ..config import get_game_config
        for lvl in sorted([c.level for c in get_game_config().get_level_configs()], reverse=True):
            self._combo_level.addItem(str(lvl), lvl)
        basic_layout.addRow(tr("等级:"), self._combo_level)

        # 品质
        self._combo_quality = QComboBox()
        for label, value in _QUALITY_OPTIONS:
            self._combo_quality.addItem(label, value)
        basic_layout.addRow(tr("品质:"), self._combo_quality)

        # 承音
        self._check_chengyin = QRadioButton(tr("承音"))
        self._check_max = QRadioButton(tr("满值"))
        self._check_custom = QRadioButton(tr("自定义"))
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._check_chengyin)
        self._mode_group.addButton(self._check_max)
        self._mode_group.addButton(self._check_custom)
        self._check_chengyin.setChecked(True)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(self._check_chengyin)
        mode_layout.addWidget(self._check_max)
        mode_layout.addWidget(self._check_custom)
        mode_layout.addStretch()
        basic_layout.addRow(tr("数值模式:"), mode_layout)

        layout.addWidget(basic_group)

        # ── 词条编辑 ──
        affix_group = QGroupBox(tr("词条"))
        affix_layout = QVBoxLayout(affix_group)

        self._affix_rows: list[_AffixRow] = []
        # 根据当前武器类型过滤词条列表
        filtered_affixes = self._get_filtered_affix_names()
        for i in range(1, 6):
            row = _AffixRow(i, filtered_affixes)
            self._affix_rows.append(row)
            affix_layout.addWidget(row)

        # 定音词条
        dingyin_layout = QHBoxLayout()
        dingyin_layout.addWidget(QLabel(tr("定音:")))
        self._combo_dingyin = QComboBox()
        self._combo_dingyin.setFixedWidth(200)
        self._combo_dingyin.addItem(tr("（无）"), "")
        dingyin_affixes = self._get_dingyin_affix_names()
        for name in dingyin_affixes:
            self._combo_dingyin.addItem(name, name)
        dingyin_layout.addWidget(self._combo_dingyin)

        self._spin_dingyin = QDoubleSpinBox()
        self._spin_dingyin.setFixedWidth(80)
        self._spin_dingyin.setDecimals(1)
        self._spin_dingyin.setRange(0.0, 999.0)
        dingyin_layout.addWidget(self._spin_dingyin)
        dingyin_layout.addStretch()
        affix_layout.addLayout(dingyin_layout)

        layout.addWidget(affix_group)

        # ── 按钮 ──
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 武器类型变化时过滤词条列表
        self._combo_weapon_type.currentIndexChanged.connect(
            self._on_weapon_type_changed)

    def _get_level(self) -> int:
        """获取当前选择的等级（供 _AffixRow 调用）"""
        return self._combo_level.currentData() or 110

    def _on_part_changed(self, _index: int):
        """部位改变时更新武器类型容器可见性"""
        part = self._combo_part.currentData()
        is_weapon = part == tr("武器")
        self._weapon_type_container.setVisible(is_weapon)

    def _on_weapon_type_changed(self, _index: int):
        """武器类型改变时更新词条下拉列表"""
        if hasattr(self, "_affix_rows") and self._affix_rows:
            self._update_affix_rows()

    def _refresh_weapon_types(self):
        """刷新武器类型下拉"""
        from ..config import get_game_config
        self._combo_weapon_type.clear()
        for wt in get_game_config().get_weapon_types():
            self._combo_weapon_type.addItem(wt, wt)

    def _get_filtered_affix_names(self) -> list[str]:
        """根据当前武器类型过滤普通词条列表

        武学增效词条只保留当前武器类型绑定的那条，其余普通词条全部保留。
        """
        from ..config import get_game_config
        gc = get_game_config()
        all_normal = gc.get_normal_affix_names()
        weapon_type = self._combo_weapon_type.currentData() or ""
        wuxue_affix = gc.get_weapon_wuxue_affix(weapon_type) if weapon_type else ""
        all_wuxue = set(gc.get_wuxue_affix_names())
        return [
            name for name in all_normal
            if name not in all_wuxue or name == wuxue_affix
        ]

    def _update_affix_rows(self):
        """武器类型变化时重建所有词条行的下拉列表"""
        filtered = self._get_filtered_affix_names()
        for row in self._affix_rows:
            current = row._combo_name.currentData()
            row._combo_name.blockSignals(True)
            row._combo_name.clear()
            row._combo_name.addItem(tr("（空）"), "")
            for name in filtered:
                row._combo_name.addItem(name, name)
            if current:
                idx = row._combo_name.findData(current)
                if idx >= 0:
                    row._combo_name.setCurrentIndex(idx)
                else:
                    row._combo_name.setCurrentIndex(0)
            row._combo_name.blockSignals(False)
            row._refresh_cap_info()

    def _get_normal_affix_names(self) -> list[str]:
        """获取普通词条列表"""
        from ..config import get_game_config
        return get_game_config().get_normal_affix_names()

    def _get_dingyin_affix_names(self) -> list[str]:
        """获取定音词条列表"""
        from ..config import get_game_config
        return get_game_config().get_dingyin_affix_names()

    def _load_data(self):
        """加载已有数据到对话框"""
        data = self._equip_data
        # 部位
        part_name = data.get("type", "")
        from ..config import get_game_config
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

        # 词条
        for i, row in enumerate(self._affix_rows, 1):
            row.set_data(data.get(f"affix_{i}"))

        # 定音
        dingyin = data.get("dingyin")
        if dingyin and dingyin.get("name"):
            idx = self._combo_dingyin.findData(dingyin["name"])
            if idx >= 0:
                self._combo_dingyin.setCurrentIndex(idx)
            self._spin_dingyin.setValue(dingyin.get("value", 0.0))

    def _on_accept(self):
        """确认按钮处理"""
        name = self._edit_name.text().strip()
        if not name:
            QMessageBox.warning(self, tr("提示"), tr("请输入装备名称"))
            return

        # 构建装备数据
        result = self._build_equip_data()
        if result is None:
            return

        self._result_data = result
        self.accept()

    def _build_equip_data(self) -> dict | None:
        """构建装备数据字典"""
        from ..config import get_game_config
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
        mode = self._get_value_mode()
        for i, row in enumerate(self._affix_rows, 1):
            affix_data = row.get_data()
            if affix_data:
                # 根据模式填充数值
                if mode == _MODE_CHENGYIN and "cap_pct" not in affix_data:
                    # 承音模式：如果没手动设置，填充承音值
                    caps_info = gc.get_affix_caps(level, affix_data["name"])
                    if caps_info:
                        affix_data["value"] = caps_info["chengyin"]
                        affix_data["cap_pct"] = 94.0
                elif mode == _MODE_MAX and "cap_pct" not in affix_data:
                    # 满值模式：填充满值
                    caps_info = gc.get_affix_caps(level, affix_data["name"])
                    if caps_info:
                        affix_data["value"] = caps_info["cap"]
                        affix_data["cap_pct"] = 100.0
                result[f"affix_{i}"] = affix_data

        # 定音
        dingyin_name = self._combo_dingyin.currentData()
        if dingyin_name:
            result["dingyin"] = {
                "name": dingyin_name,
                "value": self._spin_dingyin.value(),
            }
        else:
            result["dingyin"] = None

        # _extra
        result["_extra"] = {"is_mock": True, "affix_count": sum(1 for i in range(1, 6) if f"affix_{i}" in result)}

        # _fp：模拟装备指纹自动添加 mock_ 前缀
        from ..core.equip_parser.models import make_fingerprint
        result["_fp"] = make_fingerprint(result, is_mock=True)

        return result

    def _get_value_mode(self) -> str:
        """获取当前数值模式"""
        if self._check_chengyin.isChecked():
            return _MODE_CHENGYIN
        elif self._check_max.isChecked():
            return _MODE_MAX
        return _MODE_CUSTOM

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
