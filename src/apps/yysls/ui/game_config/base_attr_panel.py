"""基础属性规则面板（装备配置）

管理装备基础属性规则（用于品阶推断）与武器类型注册表。
左侧固定八个装备类型，右侧为属性跟随配置 + 基础属性说明 + 等级×品阶表格。
属性跟随：勾选后该部位复用目标部位的数值（YAML 中记为 _follow），
表格只读展示目标部位数据，避免重复配置（副武器跟随主武器、
胫甲/腕甲跟随冠胄）。
基础属性说明：部位数值对应的属性名由 YAML 的 _attr 声明，
区间型部位（武器）由 _range: true 声明，品阶单元格改用
最小/最大双数字输入框，不再手写 a~b 文本。
武器类型：仅主武器部位展示，维护 attributes.yaml 顶层 weapon_types
注册表（识别层为启动快照，新增武器需重启后方可参与识别），
每个武器可绑定一种武学增效词条，被流派配置引用的武器不允许删除。
自动保存，覆盖已有数值时确认。
"""

from pathlib import Path

import yaml
from loguru import logger
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QHeaderView, QLabel,
    QCheckBox, QComboBox, QFrame, QInputDialog,
)
from PyQt6.QtCore import Qt

from src.apps.yysls.game_config import BASE_ATTR_PARTS, WUXUE_CATEGORY
from src.ui.widgets import NoWheelSpinBox

# 配置文件路径
_ATTRS_PATH = Path("config/system/yysls/attributes.yaml")

# 部位显示名称（顺序由 BASE_ATTR_PARTS 决定）
_PART_NAMES = {
    "main_weapon": "主武器",
    "sub_weapon": "副武器",
    "ring": "环",
    "pendant": "佩",
    "head": "冠胄",
    "chest": "胸甲",
    "leg": "胫甲",
    "wrist": "腕甲",
}

# 品阶显示名称
_QUALITY_NAMES = {
    "gold": "金装",
    "purple": "紫装",
    "blue": "蓝装",
}


class _RangeCell(QWidget):
    """区间值单元格（最小/最大双数字输入框，0 显示为空白表示未配置）

    使用禁滚轮输入框，避免滑动表格时误改数值。
    """

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)

        self._min = NoWheelSpinBox()
        self._max = NoWheelSpinBox()
        for sb in (self._min, self._max):
            sb.setRange(0, 999999)
            sb.setSpecialValueText(" ")  # 空串会退回显示 0，用空格实现空白
            sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._min.setToolTip("最小值")
        self._max.setToolTip("最大值")

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

        # 左侧：部位列表（固定八个部位）
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("装备类型"))

        self._part_list = QListWidget()
        for part in BASE_ATTR_PARTS:
            self._part_list.addItem(_PART_NAMES.get(part, part))
        self._part_list.currentRowChanged.connect(self._on_part_changed)
        left_layout.addWidget(self._part_list)

        splitter.addWidget(left_widget)

        # 右侧：属性跟随 + 表格 + 按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # ── 属性跟随（勾选后下拉选择跟随的目标部位）──
        follow_frame = QFrame()
        follow_frame.setStyleSheet(
            "QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }"
        )
        follow_layout = QHBoxLayout(follow_frame)
        follow_layout.setContentsMargins(8, 4, 8, 4)
        self._check_follow = QCheckBox("属性跟随")
        self._check_follow.toggled.connect(self._on_follow_toggled)
        follow_layout.addWidget(self._check_follow)

        self._combo_follow = QComboBox()
        self._combo_follow.setMinimumWidth(120)
        self._combo_follow.currentIndexChanged.connect(self._on_follow_target_changed)
        follow_layout.addWidget(self._combo_follow)

        self._follow_hint = QLabel("")
        self._follow_hint.setStyleSheet("color: #888;")
        follow_layout.addWidget(self._follow_hint)
        follow_layout.addStretch()
        right_layout.addWidget(follow_frame)

        # ── 武器类型（仅主武器部位；维护 weapon_types 注册表）──
        self._weapon_frame = QFrame()
        self._weapon_frame.setObjectName("weaponFrame")
        # 用 objectName 限定，避免样式级联到 QListWidget（其继承自 QFrame）
        self._weapon_frame.setStyleSheet(
            "QFrame#weaponFrame { background-color: #f5f5f5; "
            "border-radius: 4px; padding: 4px; }"
        )
        weapon_layout = QVBoxLayout(self._weapon_frame)
        weapon_layout.setContentsMargins(8, 4, 8, 4)

        weapon_header = QHBoxLayout()
        weapon_header.addWidget(QLabel("武器类型"))
        hint = QLabel("新增武器重启后方可参与识别；被流派配置引用的武器不可删除")
        hint.setStyleSheet("color: #888;")
        weapon_header.addWidget(hint)
        weapon_header.addStretch()
        self._btn_add_weapon = QPushButton("添加")
        self._btn_add_weapon.clicked.connect(self._on_add_weapon)
        weapon_header.addWidget(self._btn_add_weapon)
        self._btn_del_weapon = QPushButton("删除")
        self._btn_del_weapon.clicked.connect(self._on_del_weapon)
        weapon_header.addWidget(self._btn_del_weapon)
        weapon_layout.addLayout(weapon_header)

        self._weapon_list = QListWidget()
        self._weapon_list.setMaximumHeight(300)
        self._weapon_list.currentRowChanged.connect(self._on_weapon_selected)
        weapon_layout.addWidget(self._weapon_list)

        # 武学增效编辑区（选中武器后展示）
        wuxue_layout = QHBoxLayout()
        wuxue_layout.setContentsMargins(0, 4, 0, 0)
        wuxue_layout.addWidget(QLabel("武学增效"))
        self._combo_wuxue_affix = QComboBox()
        self._combo_wuxue_affix.currentTextChanged.connect(self._on_wuxue_affix_changed)
        wuxue_layout.addWidget(self._combo_wuxue_affix, 1)
        weapon_layout.addLayout(wuxue_layout)

        self._weapon_frame.setVisible(False)
        right_layout.addWidget(self._weapon_frame)

        # ── 基础属性说明（部位数值对应的属性名，来自 YAML _attr）──
        attr_frame = QFrame()
        attr_frame.setStyleSheet(
            "QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }"
        )
        attr_layout = QHBoxLayout(attr_frame)
        attr_layout.setContentsMargins(8, 4, 8, 4)
        self._attr_label = QLabel("")
        attr_layout.addWidget(self._attr_label)
        attr_layout.addStretch()
        right_layout.addWidget(attr_frame)

        self._table = QTableWidget()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.cellChanged.connect(self._on_cell_changed)
        right_layout.addWidget(self._table)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._btn_add_level = QPushButton("添加等级")
        self._btn_add_level.clicked.connect(self._add_level)
        btn_layout.addWidget(self._btn_add_level)

        right_layout.addLayout(btn_layout)
        splitter.addWidget(right_widget)

        splitter.setSizes([150, 400])

    def _load_data(self):
        """从 YAML 加载数据"""
        if not _ATTRS_PATH.exists():
            logger.warning(f"配置文件不存在: {_ATTRS_PATH}")
            self._data = {"base_attrs": {}, "affix_caps": {}}
        else:
            try:
                with open(_ATTRS_PATH, "r", encoding="utf-8") as f:
                    self._data = yaml.safe_load(f) or {}
            except Exception as e:
                logger.error(f"加载配置失败: {e}")
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
        """刷新武器类型列表（仅主武器部位可见）"""
        is_main = self._current_part == "main_weapon"
        self._weapon_frame.setVisible(is_main)
        if not is_main:
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
        """选中武器变化时刷新武学增效下拉框"""
        self._refresh_wuxue_combo()

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
        name, ok = QInputDialog.getText(self, "添加武器类型", "武器名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        types = self._weapon_types()
        if name in types:
            QMessageBox.warning(self, "无法添加", f"武器类型「{name}」已存在。")
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
                self, "无法删除",
                f"武器类型「{name}」正被流派 {'、'.join(users)} 引用，"
                "请先在流派配置中解除绑定。",
            )
            return
        ret = QMessageBox.question(self, "确认删除", f"确定删除武器类型「{name}」？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        raw = [t for t in self._weapon_types_raw() if t.get("name") != name]
        self._data["weapon_types"] = raw
        self._save_data()
        self._refresh_weapon_types()

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
            self._attr_label.setText("基础属性：未声明（YAML 中通过 _attr 配置）")
            return
        kind = "区间值（最小~最大）" if self._is_range_part() else "单值"
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
            self._follow_hint.setText("数值复用目标部位，表格只读")
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
                    self, "无法跟随",
                    f"「{_PART_NAMES.get(part, part)}」正被 {names} 跟随，不能再跟随其他部位。",
                )
                self._revert_follow_check(False)
                return

            candidates = self._follow_candidates()
            if not candidates:
                QMessageBox.warning(self, "无法跟随", "没有可跟随的目标部位。")
                self._revert_follow_check(False)
                return

            # 已有等级数值时确认覆盖
            own_levels = {
                k: v for k, v in self._part_data(part).items()
                if not str(k).startswith("_")
            }
            if own_levels:
                ret = QMessageBox.question(
                    self, "确认跟随",
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
        columns = ["等级"] + [_QUALITY_NAMES.get(q, q) for q in qualities]

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

            # 等级列（可编辑）
            level_item = QTableWidgetItem(str(level))
            self._table.setItem(row, 0, level_item)

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
            columns = ["等级"] + [_QUALITY_NAMES.get(q, q) for q in qualities]
            self._table.setColumnCount(len(columns))
            self._table.setHorizontalHeaderLabels(columns)

        # 直接插入空行，让用户填写
        self._saving = True
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(""))
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
            level_item = self._table.item(row, 0)
            if not level_item:
                continue
            level_text = level_item.text().strip()
            if not level_text:
                continue  # 跳过空行

            try:
                level = int(level_text)
            except ValueError:
                continue  # 跳过无效等级

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
        try:
            with open(_ATTRS_PATH, "w", encoding="utf-8") as f:
                yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.debug(f"配置已保存: {_ATTRS_PATH}")
            # 刷新 GameConfigManager 单例
            from src.apps.yysls.game_config import get_game_config
            manager = get_game_config()
            manager._load()
        except Exception as e:
            logger.error(f"保存失败: {e}")
