"""基础属性规则面板（装备配置）

管理装备基础属性规则（用于品阶推断）与武器类型注册表。
左侧固定七个装备类型，右侧为属性跟随配置 + 基础属性说明 + 等级×品阶表格。
属性跟随：勾选后该部位复用目标部位的数值（YAML 中记为 _follow），
表格只读展示目标部位数据，避免重复配置（胫甲/腕甲跟随冠胄）。
基础属性说明：部位数值对应的属性名由 YAML 的 _attr 声明，
区间型部位（武器）由 _range: true 声明，品阶单元格改用
最小/最大双数字输入框，不再手写 a~b 文本。
武器类型：仅武器部位展示，维护 attributes.yaml 顶层 weapon_types
注册表（识别层为启动快照，新增武器需重启后方可参与识别），
每个武器可绑定一种武学增效词条，被流派配置引用的武器不允许删除。
自动保存，覆盖已有数值时确认。
"""

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.config import BASE_ATTR_PARTS, EQUIP_PART_NAMES, WUXUE_CATEGORY
from lvjiang.ui.button_styles import apply_button_style

from .....i18n import tr
from ..layout_helpers import configure_navigation_list
from .factory_guard import deletable, factory_list_values
from .level_combo import LevelCombo

# 配置文件（聚合键值，经 resolver 读合并视图、按模式写回）
_ATTRS_REL = "yysls/game_config.yaml"

# 部位显示名称（顺序由 BASE_ATTR_PARTS 决定）
_PART_NAMES = {
    "weapon": tr("武器"),
    "ring": tr("环"),
    "pendant": tr("佩"),
    "head": tr("冠胄"),
    "chest": tr("胸甲"),
    "leg": tr("胫甲"),
    "wrist": tr("腕甲"),
}

# 品阶显示名称
_QUALITY_NAMES = {
    "gold": tr("金装"),
    "purple": tr("紫装"),
    "blue": tr("蓝装"),
}

# 部位 key → 显示名称（BASE_ATTR_PARTS 与 EQUIP_PART_NAMES 同序映射，
# 用于 affix_parts 过滤时按当前部位 key 查对应中文名）
_PART_TO_NAME = dict(zip(BASE_ATTR_PARTS, EQUIP_PART_NAMES, strict=True))


class _RangeCell(QWidget):
    """区间值单元格（最小/最大双数字输入框，0 显示为空白表示未配置）

    使用禁滚轮输入框，避免滑动表格时误改数值。
    """

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)

        self._min = QSpinBox()
        self._max = QSpinBox()
        for sb in (self._min, self._max):
            sb.setRange(0, 999999)
            sb.setSpecialValueText(" ")  # 空串会退回显示 0，用空格实现空白
            sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._min.setToolTip(tr("最小值"))
        self._max.setToolTip(tr("最大值"))

        layout.addWidget(self._min, 1)
        layout.addWidget(QLabel("~"))
        layout.addWidget(self._max, 1)

        self._min.valueChanged.connect(on_change)
        self._max.valueChanged.connect(on_change)

    def set_value(self, q_data):
        """填入数据（阻断信号），None 表示未配置"""
        for sb in (self._min, self._max):
            sb.blockSignals(True)
        if isinstance(q_data, dict):
            self._min.setValue(q_data.get("min") or 0)
            self._max.setValue(q_data.get("max") or 0)
        elif q_data is not None:
            # 历史单值兼容展示：min=max
            self._min.setValue(int(q_data))
            self._max.setValue(int(q_data))
        else:
            self._min.setValue(0)
            self._max.setValue(0)
        for sb in (self._min, self._max):
            sb.blockSignals(False)

    def get_value(self) -> dict | None:
        """读取数据，两端均未填返回 None"""
        min_v, max_v = self._min.value(), self._max.value()
        if min_v == 0 and max_v == 0:
            return None
        return {"min": min_v, "max": max_v}


class BaseAttrPanel(QWidget):
    """基础属性规则面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}  # 完整配置数据
        self._current_part: str | None = None
        self._saving = False  # 防止递归保存
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧：部位列表（固定七个部位）
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(tr("装备类型")))

        self._part_list = QListWidget()
        configure_navigation_list(self._part_list, minimum_width=200)
        for part in BASE_ATTR_PARTS:
            self._part_list.addItem(_PART_NAMES.get(part, part))
        self._part_list.currentRowChanged.connect(self._on_part_changed)
        left_layout.addWidget(self._part_list)

        splitter.addWidget(left_widget)

        # 右侧：属性跟随 + 表格 + 按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # 属性跟随控件（合入基础属性行，仅控制基础属性表格）
        self._check_follow = QCheckBox(tr("属性跟随"))
        self._check_follow.toggled.connect(self._on_follow_toggled)
        self._combo_follow = QComboBox()
        self._combo_follow.setMinimumWidth(120)
        self._combo_follow.currentIndexChanged.connect(self._on_follow_target_changed)
        self._follow_hint = QLabel("")
        self._follow_hint.setStyleSheet("color: palette(mid);")

        # ── 首词条（基础属性上方，每个部位可限定首词条候选）──
        self._first_affix_frame = QFrame()
        self._first_affix_frame.setObjectName("firstAffixFrame")
        self._first_affix_frame.setStyleSheet(
            "QFrame#firstAffixFrame { background-color: palette(alternate-base); "
            "border-radius: 4px; padding: 4px; }"
        )
        first_affix_layout = QHBoxLayout(self._first_affix_frame)
        first_affix_layout.setContentsMargins(8, 4, 8, 4)
        first_affix_layout.addWidget(QLabel(tr("首词条")))
        self._first_affix_btn = QPushButton(tr("（点击选择首词条）"))
        self._first_affix_btn.clicked.connect(self._pick_first_affixes)
        apply_button_style(self._first_affix_btn, variant="neutral")
        first_affix_layout.addWidget(self._first_affix_btn, 1)
        self._first_affix_hint = QLabel("")
        self._first_affix_hint.setStyleSheet("color: palette(mid);")
        first_affix_layout.addWidget(self._first_affix_hint)
        self._first_affix_frame.setVisible(False)
        right_layout.addWidget(self._first_affix_frame)

        # ── 武器类型（仅主武器部位；维护 weapon_types 注册表）──
        self._weapon_frame = QFrame()
        self._weapon_frame.setObjectName("weaponFrame")
        # 用 objectName 限定，避免样式级联到 QListWidget（其继承自 QFrame）
        self._weapon_frame.setStyleSheet(
            "QFrame#weaponFrame { background-color: palette(alternate-base); "
            "border-radius: 4px; padding: 4px; }"
        )
        weapon_layout = QVBoxLayout(self._weapon_frame)
        weapon_layout.setContentsMargins(8, 4, 8, 4)

        weapon_header = QHBoxLayout()
        weapon_header.addWidget(QLabel(tr("武器类型")))
        hint = QLabel(tr("新增武器重启后方可参与识别；被流派配置引用的武器不可删除"))
        hint.setStyleSheet("color: palette(mid);")
        weapon_header.addWidget(hint)
        weapon_header.addStretch()
        self._btn_add_weapon = QPushButton(tr("添加"))
        self._btn_add_weapon.clicked.connect(self._on_add_weapon)
        weapon_header.addWidget(self._btn_add_weapon)
        self._btn_del_weapon = QPushButton(tr("删除"))
        self._btn_del_weapon.clicked.connect(self._on_del_weapon)
        weapon_header.addWidget(self._btn_del_weapon)
        apply_button_style(self._btn_add_weapon)
        apply_button_style(self._btn_del_weapon, variant="danger")
        weapon_layout.addLayout(weapon_header)

        self._weapon_list = QListWidget()
        self._weapon_list.setMaximumHeight(300)
        self._weapon_list.currentRowChanged.connect(self._on_weapon_selected)
        weapon_layout.addWidget(self._weapon_list)

        # 武学增效编辑区（选中武器后展示）
        wuxue_layout = QHBoxLayout()
        wuxue_layout.setContentsMargins(0, 4, 0, 0)
        wuxue_layout.addWidget(QLabel(tr("武学增效")))
        self._combo_wuxue_affix = QComboBox()
        self._combo_wuxue_affix.currentTextChanged.connect(self._on_wuxue_affix_changed)
        wuxue_layout.addWidget(self._combo_wuxue_affix, 1)
        weapon_layout.addLayout(wuxue_layout)

        self._weapon_frame.setVisible(False)
        right_layout.addWidget(self._weapon_frame)

        # ── 基础属性说明 + 属性跟随（仅控制基础属性表格）──
        attr_frame = QFrame()
        attr_frame.setStyleSheet(
            "QFrame { background-color: palette(alternate-base); border-radius: 4px; padding: 4px; }"
        )
        attr_layout = QHBoxLayout(attr_frame)
        attr_layout.setContentsMargins(8, 4, 8, 4)
        self._attr_label = QLabel("")
        attr_layout.addWidget(self._attr_label)
        attr_layout.addStretch()
        attr_layout.addWidget(self._check_follow)
        attr_layout.addWidget(self._combo_follow)
        attr_layout.addWidget(self._follow_hint)
        right_layout.addWidget(attr_frame)

        self._table = QTableWidget()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.cellChanged.connect(self._on_cell_changed)
        right_layout.addWidget(self._table)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._btn_add_level = QPushButton(tr("添加等级"))
        self._btn_add_level.clicked.connect(self._add_level)
        btn_layout.addWidget(self._btn_add_level)
        apply_button_style(self._btn_add_level)

        right_layout.addLayout(btn_layout)
        splitter.addWidget(right_widget)

        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([220, 650])

    def _load_data(self):
        """从 YAML 加载数据"""
        from lvjiang.core.config.resolver import get_resolver
        try:
            self._data = get_resolver().load_merged(_ATTRS_REL)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            self._data = {"base_attrs": {}, "affix_caps": {}}
        if not self._data:
            self._data = {"base_attrs": {}, "affix_caps": {}}

        if self._part_list.count() > 0:
            self._part_list.setCurrentRow(0)
            # currentRowChanged 不触发首行时手动刷新
            if self._current_part is None:
                self._on_part_changed(0)

    # ── 部位切换 ──────────────────────────────────────────────

    def _on_part_changed(self, row: int):
        """切换部位时更新跟随配置与表格"""
        if row < 0 or row >= len(BASE_ATTR_PARTS):
            self._current_part = None
            self._table.setRowCount(0)
            return

        self._current_part = BASE_ATTR_PARTS[row]
        self._refresh_follow_controls()
        self._refresh_first_affixes()
        self._refresh_weapon_types()
        self._refresh_attr_label()
        self._refresh_table()

    # ── 武器类型（weapon_types 注册表）──────────────────

    def _weapon_types(self) -> list[str]:
        """武器名称列表（支持新旧两种格式）"""
        raw = self._data.get("weapon_types") or []
        return [
            str(t["name"]) if isinstance(t, dict) else str(t)
            for t in raw
        ]

    def _weapon_types_raw(self) -> list[dict]:
        """武器类型原始数据（dict 列表格式）"""
        raw = self._data.get("weapon_types") or []
        result = []
        for t in raw:
            if isinstance(t, dict):
                result.append(t)
            else:
                # 兼容旧格式：转换为 dict
                result.append({"name": str(t)})
        return result

    def _wuxue_affix_candidates(self) -> list[str]:
        """武学增效词条候选（指定武学增效 类别的 _aliases）"""
        category = (self._data.get("affix_caps") or {}).get(WUXUE_CATEGORY) or {}
        aliases = category.get("_aliases") or []
        if isinstance(aliases, dict):
            return [name for names in aliases.values() for name in names]
        return list(aliases)

    def _get_weapon_wuxue_affix(self, name: str) -> str:
        """获取指定武器的武学增效词条"""
        for t in self._weapon_types_raw():
            if t.get("name") == name:
                return t.get("wuxue_affix", "")
        return ""

    def _refresh_weapon_types(self):
        """刷新武器类型列表（仅武器部位可见）"""
        is_weapon = self._current_part == "weapon"
        self._weapon_frame.setVisible(is_weapon)
        if not is_weapon:
            return
        self._weapon_list.clear()
        for t in self._weapon_types_raw():
            name = t.get("name", "")
            affix = t.get("wuxue_affix", "")
            display = f"{name}（{affix}）" if affix else name
            self._weapon_list.addItem(display)
        self._refresh_wuxue_combo()

    def _refresh_wuxue_combo(self):
        """刷新武学增效下拉框（跟随当前选中的武器）"""
        item = self._weapon_list.currentItem()
        if item is None:
            self._combo_wuxue_affix.setEnabled(False)
            self._combo_wuxue_affix.clear()
            return
        # 从显示文本中提取武器名
        display_text = item.text()
        weapon_name = display_text.split("（")[0] if "（" in display_text else display_text
        candidates = self._wuxue_affix_candidates()
        current_affix = self._get_weapon_wuxue_affix(weapon_name)

        self._saving = True
        self._combo_wuxue_affix.setEnabled(True)
        self._combo_wuxue_affix.clear()
        self._combo_wuxue_affix.addItem("")  # 未配置占位
        self._combo_wuxue_affix.addItems(candidates)
        if current_affix and current_affix not in candidates:
            self._combo_wuxue_affix.addItem(current_affix)
        self._combo_wuxue_affix.setCurrentText(current_affix)
        self._saving = False

    def _on_weapon_selected(self, row: int):
        """选中武器变化时刷新武学增效下拉框与删除按钮可用性"""
        self._refresh_wuxue_combo()
        self._refresh_del_weapon_enabled()

    def _refresh_del_weapon_enabled(self):
        """出厂武器类型不允许用户删除，置灰并说明原因"""
        item = self._weapon_list.currentItem()
        name = None
        if item is not None:
            text = item.text()
            name = text.split("（")[0] if "（" in text else text
        ok, hint = deletable(
            name, factory_list_values(_ATTRS_REL, "weapon_types", field="name"))
        self._btn_del_weapon.setEnabled(item is not None and ok)
        self._btn_del_weapon.setToolTip(hint)

    def _on_wuxue_affix_changed(self, text: str):
        """武学增效下拉框变化时保存到数据"""
        if self._saving:
            return
        item = self._weapon_list.currentItem()
        if item is None:
            return
        display_text = item.text()
        weapon_name = display_text.split("（")[0] if "（" in display_text else display_text
        # 更新 weapon_types 中对应武器的 wuxue_affix
        raw = self._weapon_types_raw()
        for t in raw:
            if t.get("name") == weapon_name:
                if text:
                    t["wuxue_affix"] = text
                else:
                    t.pop("wuxue_affix", None)
                break
        self._data["weapon_types"] = raw
        self._save_data()
        # 刷新列表展示
        self._refresh_weapon_types()

    def _schools_using_weapon(self, weapon: str) -> list[str]:
        """引用指定武器的流派名列表（主/副武器均算引用）"""
        result = []
        for name, cfg in (self._data.get("schools") or {}).items():
            cfg = cfg or {}
            main = cfg.get("main") or {}
            sub = cfg.get("sub") or {}
            if weapon in (main.get("weapon"), sub.get("weapon")):
                result.append(name)
        return result

    def _on_add_weapon(self):
        """添加新武器类型"""
        name, ok = QInputDialog.getText(self, tr("添加武器类型"), tr("武器名称："))
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        types = self._weapon_types()
        if name in types:
            QMessageBox.warning(self, tr("无法添加"), tr("武器类型「{name}」已存在。").format(name=name))
            return
        raw = self._weapon_types_raw()
        raw.append({"name": name})
        self._data["weapon_types"] = raw
        self._save_data()
        self._refresh_weapon_types()

    def _on_del_weapon(self):
        """删除选中武器类型（被流派配置引用时拒绝）"""
        item = self._weapon_list.currentItem()
        if item is None:
            return
        display_text = item.text()
        name = display_text.split("（")[0] if "（" in display_text else display_text
        users = self._schools_using_weapon(name)
        if users:
            QMessageBox.warning(
                self, tr("无法删除"),
                tr("武器类型「{name}」正被流派 {users} 引用，请先在流派配置中解除绑定。").format(
                    name=name, users='、'.join(users)),
            )
            return
        ret = QMessageBox.question(self, tr("确认删除"), tr("确定删除武器类型「{name}」？").format(name=name))
        if ret != QMessageBox.StandardButton.Yes:
            return
        raw = [t for t in self._weapon_types_raw() if t.get("name") != name]
        self._data["weapon_types"] = raw
        self._save_data()
        self._refresh_weapon_types()

    # ── 首词条（每个部位限定首词条候选，数据存于 _first_affixes）──

    def _refresh_first_affixes(self):
        """刷新首词条展示（始终可编辑，不受属性跟随影响）"""
        if not self._current_part:
            self._first_affix_frame.setVisible(False)
            return
        self._first_affix_frame.setVisible(True)

        part_data = self._part_data(self._current_part)
        self._first = list(part_data.get("_first_affixes") or [])
        self._first_affix_btn.setEnabled(True)
        self._first_affix_btn.setText(
            "/".join(self._first) if self._first else tr("（点击选择首词条）")
        )
        self._first_affix_hint.setText("")

    def _pick_first_affixes(self):
        """打开词条选择对话框，候选为词组配置中当前部位可见的普通词条"""
        if not self._current_part:
            return

        part_name = _PART_TO_NAME.get(self._current_part, "")
        if not part_name:
            return

        # 候选：全部普通词条中，affix_parts 包含当前部位的
        from lvjiang.apps.yysls.config import get_game_config
        mgr = get_game_config()
        all_normal = mgr.get_normal_affix_names()
        candidates = [
            name for name in all_normal
            if part_name in mgr.get_affix_parts(name)
        ]

        from ..tune_settings.affix_picker import AffixSelectSortDialog
        dlg = AffixSelectSortDialog(
            candidates, list(self._first),
            tr("选择首词条"), self, flat=True,
        )
        if dlg.exec():
            self._first = dlg.selected()
            self._first_affix_btn.setText(
                "/".join(self._first) if self._first else tr("（点击选择首词条）")
            )
            self._save_first_affixes()

    def _save_first_affixes(self):
        """保存首词条到 YAML（_first_affixes 字段，属 meta 不被表格同步覆盖）"""
        if not self._current_part:
            return
        part_data = self._part_data(self._current_part)
        if self._first:
            part_data["_first_affixes"] = list(self._first)
        else:
            part_data.pop("_first_affixes", None)
        self._save_data()

    # ── 基础属性说明 ──────────────────────────────────

    def _effective_part(self) -> str | None:
        """当前部位的数据来源部位（跟随时为目标部位）"""
        if not self._current_part:
            return None
        return self._follow_target(self._current_part) or self._current_part

    def _is_range_part(self) -> bool:
        """当前部位（含跟随目标）是否为区间型数值"""
        part = self._effective_part()
        return bool(part and self._part_data(part).get("_range"))

    def _refresh_attr_label(self):
        """刷新基础属性说明（跟随时取目标部位的 _attr）"""
        part = self._effective_part()
        if not part:
            self._attr_label.setText("")
            return
        attr_name = self._part_data(part).get("_attr")
        if not attr_name:
            self._attr_label.setText(tr("基础属性：未声明（YAML 中通过 _attr 配置）"))
            return
        kind = tr("区间值（最小~最大）") if self._is_range_part() else tr("单值")
        self._attr_label.setText(f"基础属性：{attr_name}（{kind}）")

    # ── 属性跟随 ──────────────────────────────────────────────

    def _part_data(self, part: str) -> dict:
        return self._data.get("base_attrs", {}).get(part) or {}

    def _follow_target(self, part: str) -> str | None:
        return self._part_data(part).get("_follow")

    def _followers_of(self, part: str) -> list[str]:
        """跟随指定部位的其他部位"""
        return [p for p in BASE_ATTR_PARTS if self._follow_target(p) == part]

    def _follow_candidates(self) -> list[str]:
        """可作为跟随目标的部位（排除自身与已跟随他人的部位，防止链式跟随）"""
        return [
            p for p in BASE_ATTR_PARTS
            if p != self._current_part and self._follow_target(p) is None
        ]

    def _refresh_follow_controls(self):
        """刷新属性跟随勾选框与下拉框"""
        if not self._current_part:
            return

        self._saving = True
        target = self._follow_target(self._current_part)

        self._combo_follow.clear()
        candidates = self._follow_candidates()
        for p in candidates:
            self._combo_follow.addItem(_PART_NAMES.get(p, p), p)
        if target and target in candidates:
            self._combo_follow.setCurrentIndex(candidates.index(target))

        following = target is not None
        self._check_follow.setChecked(following)
        self._combo_follow.setEnabled(following)
        if following:
            self._follow_hint.setText(tr("数值复用目标部位，表格只读"))
        else:
            followers = self._followers_of(self._current_part)
            if followers:
                names = "、".join(_PART_NAMES.get(p, p) for p in followers)
                self._follow_hint.setText(f"被跟随：{names}")
            else:
                self._follow_hint.setText("")
        self._btn_add_level.setEnabled(not following)
        self._saving = False

    def _on_follow_toggled(self, checked: bool):
        """勾选/取消属性跟随"""
        if self._saving or not self._current_part:
            return
        part = self._current_part

        if checked:
            # 被其他部位跟随时不允许再跟随（防止链式/循环）
            followers = self._followers_of(part)
            if followers:
                names = "、".join(_PART_NAMES.get(p, p) for p in followers)
                QMessageBox.warning(
                    self, tr("无法跟随"),
                    f"「{_PART_NAMES.get(part, part)}」正被 {names} 跟随，不能再跟随其他部位。",
                )
                self._revert_follow_check(False)
                return

            candidates = self._follow_candidates()
            if not candidates:
                QMessageBox.warning(self, tr("无法跟随"), tr("没有可跟随的目标部位。"))
                self._revert_follow_check(False)
                return

            # 已有等级数值时确认覆盖
            own_levels = {
                k: v for k, v in self._part_data(part).items()
                if not str(k).startswith("_")
            }
            if own_levels:
                ret = QMessageBox.question(
                    self, tr("确认跟随"),
                    f"「{_PART_NAMES.get(part, part)}」已配置等级数值，"
                    "启用跟随将清除自身数值，是否继续？",
                )
                if ret != QMessageBox.StandardButton.Yes:
                    self._revert_follow_check(False)
                    return

            target = self._combo_follow.currentData() or candidates[0]
            self._set_part_data(part, {"_follow": target}, keep_meta=True)
        else:
            # 取消跟随：保留元信息，数值由用户自行填写
            self._set_part_data(part, {}, keep_meta=True)

        self._save_data()
        self._refresh_follow_controls()
        self._refresh_attr_label()
        self._refresh_table()

    def _revert_follow_check(self, checked: bool):
        """回退勾选框状态（不触发信号）"""
        self._saving = True
        self._check_follow.setChecked(checked)
        self._saving = False

    def _on_follow_target_changed(self, index: int):
        """切换跟随目标"""
        if self._saving or not self._current_part or index < 0:
            return
        if not self._check_follow.isChecked():
            return
        target = self._combo_follow.currentData()
        if not target:
            return
        self._set_part_data(self._current_part, {"_follow": target}, keep_meta=True)
        self._save_data()
        self._refresh_attr_label()
        self._refresh_table()

    def _set_part_data(self, part: str, new_data: dict, keep_meta: bool = False):
        """写入部位数据，keep_meta 时保留原有 _attr/_range 等元信息"""
        base_attrs = self._data.setdefault("base_attrs", {})
        if keep_meta:
            old = base_attrs.get(part) or {}
            meta = {
                k: v for k, v in old.items()
                if str(k).startswith("_") and k != "_follow" and k not in new_data
            }
            new_data = {**meta, **new_data}
        base_attrs[part] = new_data

    # ── 表格 ──────────────────────────────────────────────────

    def _refresh_table(self):
        """刷新表格内容（跟随部位只读展示目标数据）"""
        if not self._current_part:
            return

        target = self._follow_target(self._current_part)
        following = target is not None
        # 跟随时展示目标部位数据
        cat_data = self._part_data(target if following else self._current_part)
        cat_data = {k: v for k, v in cat_data.items() if not str(k).startswith("_")}

        self._table.setEnabled(not following)

        if not cat_data:
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            return

        # 列：等级 + 各品阶
        qualities = ["gold", "purple", "blue"]
        columns = [tr("等级")] + [_QUALITY_NAMES.get(q, q) for q in qualities]

        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)

        # 行：每个等级一行（先清空，避免部位切换后残留旧单元格控件）
        levels = sorted(cat_data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0, reverse=True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(levels))

        # 阻止 cellChanged 信号触发保存
        self._saving = True
        is_range = self._is_range_part()

        for row, level in enumerate(levels):
            level_data = cat_data[level]

            # 等级列（下拉选择）
            level_combo = LevelCombo(allow_empty=False)
            level_combo.set_level(int(level) if str(level).isdigit() else None)
            level_combo.currentIndexChanged.connect(lambda _v, r=row: self._on_cell_changed(r, 0))
            self._table.setCellWidget(row, 0, level_combo)

            # 品阶列：区间型用双输入框控件，单值型用文本单元格
            for col, quality in enumerate(qualities, start=1):
                q_data = level_data.get(quality)
                if is_range:
                    cell = _RangeCell(self._on_range_cell_changed)
                    cell.set_value(q_data)
                    self._table.setCellWidget(row, col, cell)
                else:
                    if q_data is None:
                        text = ""
                    elif isinstance(q_data, dict):
                        # 历史区间值兼容展示
                        text = f"{q_data.get('min', '')}~{q_data.get('max', '')}"
                    else:
                        # 单值
                        text = str(q_data)

                    item = QTableWidgetItem(text)
                    self._table.setItem(row, col, item)

        self._saving = False

    def _on_cell_changed(self, row: int, col: int):
        """单元格改变时自动保存"""
        if self._saving:
            return
        self._sync_table_to_data()
        self._save_data()

    def _on_range_cell_changed(self):
        """区间输入框改变时自动保存"""
        if self._saving:
            return
        self._sync_table_to_data()
        self._save_data()

    def _add_level(self):
        """添加新空行"""
        if not self._current_part:
            return
        if self._follow_target(self._current_part):
            return  # 跟随部位不可编辑

        # 首次添加时初始化列
        if self._table.columnCount() == 0:
            qualities = ["gold", "purple", "blue"]
            columns = [tr("等级")] + [_QUALITY_NAMES.get(q, q) for q in qualities]
            self._table.setColumnCount(len(columns))
            self._table.setHorizontalHeaderLabels(columns)

        # 直接插入空行，让用户选择等级
        self._saving = True
        row = self._table.rowCount()
        self._table.insertRow(row)
        level_combo = LevelCombo(allow_empty=True)
        level_combo.currentIndexChanged.connect(lambda _v: self._on_cell_changed(row, 0))
        self._table.setCellWidget(row, 0, level_combo)
        for col in range(1, self._table.columnCount()):
            if self._is_range_part():
                cell = _RangeCell(self._on_range_cell_changed)
                cell.set_value(None)
                self._table.setCellWidget(row, col, cell)
            else:
                self._table.setItem(row, col, QTableWidgetItem(""))
        self._saving = False

    def _sync_table_to_data(self):
        """将表格数据同步回 _data（保留 _attr/_range 等元信息）"""
        if not self._current_part:
            return
        if self._follow_target(self._current_part):
            return  # 跟随部位表格为只读展示，不回写

        is_range = self._is_range_part()

        # 从表格读取数据
        new_cat_data = {}
        for row in range(self._table.rowCount()):
            level_combo = self._table.cellWidget(row, 0)
            if not isinstance(level_combo, LevelCombo):
                continue
            level = level_combo.get_level()
            if level is None:
                continue  # 跳过未选择等级的行

            level_data = {}
            for col, quality in enumerate(["gold", "purple", "blue"], start=1):
                if is_range:
                    cell = self._table.cellWidget(row, col)
                    if isinstance(cell, _RangeCell):
                        value = cell.get_value()
                        if value is not None:
                            level_data[quality] = value
                    continue

                item = self._table.item(row, col)
                if not item:
                    continue
                text = item.text().strip()
                if not text:
                    continue

                # 解析值：范围值 or 单值
                if "~" in text:
                    parts = text.split("~")
                    try:
                        level_data[quality] = {
                            "min": int(parts[0]),
                            "max": int(parts[1])
                        }
                    except ValueError:
                        pass
                else:
                    try:
                        level_data[quality] = int(text)
                    except ValueError:
                        try:
                            level_data[quality] = float(text)
                        except ValueError:
                            pass

            if level_data:
                new_cat_data[level] = level_data

        self._set_part_data(self._current_part, new_cat_data, keep_meta=True)

    def _save_data(self):
        """保存数据到 YAML"""
        from lvjiang.core.config.resolver import get_resolver
        try:
            get_resolver().save_merged(_ATTRS_REL, self._data)
            logger.debug(f"配置已保存: {_ATTRS_REL}")
            # 刷新 GameConfigManager 单例
            from lvjiang.apps.yysls.config import get_game_config
            manager = get_game_config()
            manager._load()
        except Exception as e:
            logger.error(f"保存失败: {e}")

    def refresh_level_combos(self):
        """刷新表格中的等级下拉列表（等级配置变更后调用）"""
        for row in range(self._table.rowCount()):
            combo = self._table.cellWidget(row, 0)
            if isinstance(combo, LevelCombo):
                combo.refresh()
