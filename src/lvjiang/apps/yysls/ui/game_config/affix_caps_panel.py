"""词组配置面板

管理各词组在不同等级的最大值。
左侧词组列表，右侧等级-上限表格。
右侧顶部为词组分类（普通词组 / 定音词组）+ 词组单位（空 / %）+ 词条分组（不分组 / 分组）+ 词条名称区域。
定音词组不受承音限制，表格隐藏承音列。
普通词组的每个词条可配置归属与词条部位（可出现的装备部位，
顶层 affix_parts，全选展示「全部」且不落盘）。
词条名称支持分组（_aliases 为 dict 形态），分组后按 Tab 页展示，
专为 指定技能增效 这类包含大量词条的类别设计（按十大流派分组）。
双击词组类别 / 分组页签 / 词条名标签可打开对话框重命名。
自动保存，删除时确认。
"""

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.apps.yysls.game_config import AFFIX_CATEGORY_NAMES, EQUIP_PART_NAMES

# 配置文件（聚合键值，经 resolver 读合并视图、按模式写回）
_ATTRS_REL = "yysls/attributes.yaml"

# 承音比例（默认 94%）
_CHENGYIN_RATIO = 0.94

# 词条类型（_pool 字段；缺省为普通词条）
_POOL_DINGYIN = "dingyin"


class _AliasTag(QWidget):
    """词条名标签（双击触发重命名）"""

    def __init__(self, alias: str, on_double_click, parent=None):
        super().__init__(parent)
        self._alias = alias
        self._on_double_click = on_double_click

    def mouseDoubleClickEvent(self, event):
        self._on_double_click(self._alias)


class _PartsDialog(QDialog):
    """词条部位多选对话框（七个装备部位，全选 = 不限部位）"""

    def __init__(self, selected: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择词条部位")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("勾选该词条可出现的装备部位（全选 = 全部）"))

        self._checks: list[QCheckBox] = []
        grid = QGridLayout()
        grid.setSpacing(6)
        for i, part in enumerate(EQUIP_PART_NAMES):
            cb = QCheckBox(part)
            cb.setChecked(part in selected)
            grid.addWidget(cb, i // 4, i % 4)
            self._checks.append(cb)
        layout.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_all = QPushButton("全选")
        btn_all.setFixedWidth(60)
        btn_all.clicked.connect(
            lambda: [cb.setChecked(True) for cb in self._checks])
        btn_row.addWidget(btn_all)
        btn_invert = QPushButton("反选")
        btn_invert.setFixedWidth(60)
        btn_invert.clicked.connect(
            lambda: [cb.setChecked(not cb.isChecked()) for cb in self._checks])
        btn_row.addWidget(btn_invert)
        btn_row.addStretch()
        btn_row_widget = QWidget()
        btn_row_widget.setLayout(btn_row)
        layout.addWidget(btn_row_widget)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> list[str]:
        """已勾选部位（按 EQUIP_PART_NAMES 定序）"""
        return [cb.text() for cb in self._checks if cb.isChecked()]


class AffixCapsPanel(QWidget):
    """词条属性上限面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}  # 完整配置数据
        self._current_affix: str | None = None
        self._saving = False  # 防止递归保存
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧：词组列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("词组类型"))

        self._affix_list = QListWidget()
        self._affix_list.currentRowChanged.connect(self._on_affix_changed)
        self._affix_list.itemDoubleClicked.connect(self._rename_affix)
        left_layout.addWidget(self._affix_list)

        # 添加/删除词组按钮
        affix_btn_layout = QHBoxLayout()
        self._btn_add_affix = QPushButton("+ 词组")
        self._btn_add_affix.clicked.connect(self._add_affix)
        affix_btn_layout.addWidget(self._btn_add_affix)

        self._btn_del_affix = QPushButton("- 词组")
        self._btn_del_affix.clicked.connect(self._del_affix)
        affix_btn_layout.addWidget(self._btn_del_affix)

        left_layout.addLayout(affix_btn_layout)

        splitter.addWidget(left_widget)

        # 右侧：词组分类 + 词组单位 + 词条分组 + 词条名称 + 表格 + 按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # ── 词组分类（单选：普通词组 / 定音词组）──
        pool_frame = QFrame()
        pool_frame.setObjectName("poolFrame")
        pool_frame.setStyleSheet(
            "QFrame#poolFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }"
        )
        pool_layout = QHBoxLayout(pool_frame)
        pool_layout.setContentsMargins(8, 4, 8, 4)
        pool_layout.addWidget(QLabel("词组分类"))
        self._radio_pool_normal = QRadioButton("普通词组")
        self._radio_pool_dingyin = QRadioButton("定音词组")
        self._pool_group = QButtonGroup(self)
        self._pool_group.addButton(self._radio_pool_normal)
        self._pool_group.addButton(self._radio_pool_dingyin)
        self._radio_pool_normal.setChecked(True)
        self._radio_pool_normal.toggled.connect(self._on_pool_changed)
        pool_layout.addWidget(self._radio_pool_normal)
        pool_layout.addWidget(self._radio_pool_dingyin)
        pool_layout.addStretch()
        right_layout.addWidget(pool_frame)

        # ── 词组单位（下拉：空 / %）──
        unit_frame = QFrame()
        unit_frame.setObjectName("unitFrame")
        unit_frame.setStyleSheet(
            "QFrame#unitFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }"
        )
        unit_layout = QHBoxLayout(unit_frame)
        unit_layout.setContentsMargins(8, 4, 8, 4)
        unit_layout.addWidget(QLabel("词组单位"))
        self._unit_combo = QComboBox()
        self._unit_combo.addItems(["", "%"])
        self._unit_combo.currentTextChanged.connect(self._on_unit_combo_changed)
        unit_layout.addWidget(self._unit_combo)
        unit_layout.addStretch()
        right_layout.addWidget(unit_frame)

        # ── 词条分组（单选：不分组 / 分组）──
        group_frame = QFrame()
        group_frame.setObjectName("groupFrame")
        group_frame.setStyleSheet(
            "QFrame#groupFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }"
        )
        group_mode_layout = QHBoxLayout(group_frame)
        group_mode_layout.setContentsMargins(8, 4, 8, 4)
        group_mode_layout.addWidget(QLabel("词条分组"))
        self._radio_group_off = QRadioButton("不分组")
        self._radio_group_on = QRadioButton("分组")
        self._group_mode_group = QButtonGroup(self)
        self._group_mode_group.addButton(self._radio_group_off)
        self._group_mode_group.addButton(self._radio_group_on)
        self._radio_group_off.setChecked(True)
        self._radio_group_off.toggled.connect(self._on_group_mode_changed)
        group_mode_layout.addWidget(self._radio_group_off)
        group_mode_layout.addWidget(self._radio_group_on)
        group_mode_layout.addStretch()
        right_layout.addWidget(group_frame)

        # ── 词条名称区域 ──
        self._alias_frame = QFrame()
        self._alias_frame.setStyleSheet(
            "QFrame { background-color: #f5f5f5; border-radius: 4px; padding: 4px; }"
        )
        alias_layout = QVBoxLayout(self._alias_frame)
        alias_layout.setContentsMargins(8, 4, 8, 4)

        # 标题行
        alias_title_row = QHBoxLayout()
        alias_title_row.addWidget(QLabel("词条名称（原始词条名 → 当前类别）"))
        alias_title_row.addStretch()
        self._btn_add_group = QPushButton("+ 分组")
        self._btn_add_group.setFixedWidth(60)
        self._btn_add_group.clicked.connect(self._add_group)
        self._btn_add_group.setVisible(False)
        alias_title_row.addWidget(self._btn_add_group)
        self._btn_del_group = QPushButton("- 分组")
        self._btn_del_group.setFixedWidth(60)
        self._btn_del_group.clicked.connect(self._del_group)
        self._btn_del_group.setVisible(False)
        alias_title_row.addWidget(self._btn_del_group)
        self._btn_add_alias = QPushButton("+ 词条名")
        self._btn_add_alias.setFixedWidth(70)
        self._btn_add_alias.clicked.connect(self._add_alias)
        alias_title_row.addWidget(self._btn_add_alias)
        alias_layout.addLayout(alias_title_row)

        # 不分组：词条名逐行控件容器（每行：词条名 + 归属下拉 + 删除）
        self._alias_tags_widget = QWidget()
        self._alias_tags_layout = QVBoxLayout(self._alias_tags_widget)
        self._alias_tags_layout.setContentsMargins(0, 0, 0, 0)
        self._alias_tags_layout.setSpacing(2)
        alias_layout.addWidget(self._alias_tags_widget)

        # 分组：按组 Tab 页展示词条名（双击页签重命名分组）
        self._alias_group_tabs = QTabWidget()
        self._alias_group_tabs.setVisible(False)
        self._alias_group_tabs.tabBarDoubleClicked.connect(self._rename_group)
        alias_layout.addWidget(self._alias_group_tabs)

        right_layout.addWidget(self._alias_frame)

        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["等级", "上限", "承音"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.cellChanged.connect(self._on_cell_changed)
        right_layout.addWidget(self._table)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self._btn_add_level = QPushButton("添加等级")
        self._btn_add_level.clicked.connect(self._add_level)
        btn_layout.addWidget(self._btn_add_level)

        self._btn_del_level = QPushButton("删除等级")
        self._btn_del_level.clicked.connect(self._del_level)
        btn_layout.addWidget(self._btn_del_level)

        right_layout.addLayout(btn_layout)
        splitter.addWidget(right_widget)

        splitter.setSizes([100, 200])

    def _load_data(self):
        """从 YAML 加载数据"""
        from lvjiang.core.config_resolver import get_resolver
        try:
            self._data = get_resolver().load_merged(_ATTRS_REL)
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            self._data = {"base_attrs": {}, "affix_caps": {}}
        if not self._data:
            self._data = {"base_attrs": {}, "affix_caps": {}}

        # 填充词条列表
        self._affix_list.clear()
        affix_caps = self._data.get("affix_caps", {})
        for affix_name in affix_caps.keys():
            self._affix_list.addItem(affix_name)

        if self._affix_list.count() > 0:
            self._affix_list.setCurrentRow(0)

    def _on_affix_changed(self, row: int):
        """切换词组时更新表格和别名"""
        affix_caps = self._data.get("affix_caps", {})
        affix_names = list(affix_caps.keys())

        if row < 0 or row >= len(affix_names):
            self._current_affix = None
            self._table.setRowCount(0)
            self._refresh_pool_radios()
            self._refresh_unit_combo()
            self._refresh_group_radios()
            self._refresh_alias_tags()
            return

        self._current_affix = affix_names[row]
        self._refresh_pool_radios()
        self._refresh_unit_combo()
        self._refresh_group_radios()
        self._refresh_table()
        self._refresh_alias_tags()

    def _refresh_table(self):
        """刷新表格内容"""
        # 定音词组不受承音限制，直接隐藏承音列（无论是否有等级数据）
        self._table.setColumnHidden(2, self._is_dingyin())

        if not self._current_affix:
            self._table.setRowCount(0)
            return

        affix_caps = self._data.get("affix_caps", {})
        level_data = affix_caps.get(self._current_affix, {})

        if not level_data:
            self._table.setRowCount(0)
            return

        # 按等级排序（跳过 _aliases 等内部字段）
        levels = sorted(
            [k for k in level_data.keys() if not str(k).startswith("_") and str(k).isdigit()],
            key=lambda x: int(x),
            reverse=True
        )
        self._table.setRowCount(len(levels))

        # 阻止 cellChanged 信号触发保存
        self._saving = True

        for row, level in enumerate(levels):
            entry = level_data[level]

            # 兼容旧格式（直接是数值）
            if isinstance(entry, (int, float)):
                cap = entry
            else:
                cap = entry.get("cap", 0)

            # 计算承音值（94%）
            chengyin = round(cap * _CHENGYIN_RATIO, 2)

            # 等级（可编辑）
            level_item = QTableWidgetItem(str(level))
            self._table.setItem(row, 0, level_item)

            # 上限（可编辑）
            cap_item = QTableWidgetItem(str(cap))
            self._table.setItem(row, 1, cap_item)

            # 承音（只读）
            chengyin_item = QTableWidgetItem(str(chengyin))
            chengyin_item.setFlags(chengyin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            chengyin_item.setForeground(Qt.GlobalColor.gray)
            self._table.setItem(row, 2, chengyin_item)

        self._saving = False

    def _on_cell_changed(self, row: int, col: int):
        """单元格改变时自动保存（等级和上限列）"""
        if self._saving:
            return
        if col not in (0, 1):  # 只有等级和上限列可编辑
            return
        self._sync_table_to_data()
        self._save_data()
        # 更新承音列
        self._update_chengyin(row)

    def _update_chengyin(self, row: int):
        """更新承音列"""
        cap_item = self._table.item(row, 1)
        if not cap_item:
            return
        try:
            cap = float(cap_item.text())
            chengyin = round(cap * _CHENGYIN_RATIO, 2)
            chengyin_item = self._table.item(row, 2)
            if chengyin_item:
                chengyin_item.setText(str(chengyin))
        except ValueError:
            pass

    def _on_unit_combo_changed(self, text: str):
        """词组单位下拉改变时保存到 _unit 字段"""
        if self._saving or not self._current_affix:
            return
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.setdefault(self._current_affix, {})
        if text:
            category_data["_unit"] = text
        else:
            category_data.pop("_unit", None)
        self._save_data()

    def _add_affix(self):
        """添加新词组"""
        name, ok = QInputDialog.getText(self, "添加词组", "词组名称:")
        if not ok or not name:
            return

        name = name.strip()
        if not name:
            return

        affix_caps = self._data.setdefault("affix_caps", {})
        if name in affix_caps:
            QMessageBox.warning(self, "重复", f"词组 '{name}' 已存在")
            return

        affix_caps[name] = {}
        self._affix_list.addItem(name)
        self._affix_list.setCurrentRow(self._affix_list.count() - 1)
        self._save_data()

    def _rename_affix(self, item):
        """双击词组类别重命名（保留在 YAML 中的键顺序）"""
        old = item.text()
        name, ok = QInputDialog.getText(self, "重命名词组", "词组名称:", text=old)
        if not ok or not name:
            return
        name = name.strip()
        if not name or name == old:
            return

        affix_caps = self._data.get("affix_caps", {})
        if name in affix_caps:
            QMessageBox.warning(self, "重复", f"词组 '{name}' 已存在")
            return

        # 保序重建 dict，只替换键名
        self._data["affix_caps"] = {
            (name if k == old else k): v for k, v in affix_caps.items()
        }
        if self._current_affix == old:
            self._current_affix = name
        item.setText(name)
        self._save_data()

    def _del_affix(self):
        """删除当前词组"""
        if not self._current_affix:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除词组 '{self._current_affix}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        affix_caps = self._data.get("affix_caps", {})
        if self._current_affix in affix_caps:
            del affix_caps[self._current_affix]

        # 刷新列表
        current_row = self._affix_list.currentRow()
        self._affix_list.takeItem(current_row)
        self._current_affix = None
        self._table.setRowCount(0)
        self._save_data()

    def _add_level(self):
        """添加新空行"""
        if not self._current_affix:
            return

        # 直接插入空行
        row = self._table.rowCount()
        self._table.insertRow(row)

        # 所有列留空，让用户填写
        for col in range(self._table.columnCount()):
            item = QTableWidgetItem("")
            self._table.setItem(row, col, item)

    def _del_level(self):
        """删除当前选中的等级"""
        row = self._table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择要删除的等级")
            return

        level_item = self._table.item(row, 0)
        if not level_item:
            return

        level_text = level_item.text().strip()
        if not level_text:
            # 空行直接删除
            self._table.removeRow(row)
            return

        try:
            level = int(level_text)
        except ValueError:
            self._table.removeRow(row)
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除等级 {level} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        affix_caps = self._data.get("affix_caps", {})
        level_caps = affix_caps.get(self._current_affix, {})
        if level in level_caps:
            del level_caps[level]
            self._refresh_table()
            self._save_data()
        else:
            # 空行直接删除
            self._table.removeRow(row)

    def _sync_table_to_data(self):
        """将表格数据同步回 _data"""
        if not self._current_affix:
            return

        affix_caps = self._data.setdefault("affix_caps", {})
        level_caps = affix_caps.setdefault(self._current_affix, {})

        # 保留 _aliases / _pool / _unit 等内部字段，只清除等级数据
        internal = {k: v for k, v in level_caps.items() if str(k).startswith("_")}
        level_caps.clear()
        level_caps.update(internal)

        for row in range(self._table.rowCount()):
            level_item = self._table.item(row, 0)
            cap_item = self._table.item(row, 1)

            if not level_item or not cap_item:
                continue

            level_text = level_item.text().strip()
            if not level_text:
                continue  # 跳过空行

            try:
                level = int(level_text)
                cap = float(cap_item.text())
                # 如果是整数，存 int
                if cap == int(cap):
                    cap = int(cap)
                level_caps[level] = {"cap": cap}
            except ValueError:
                pass

    def _save_data(self):
        """保存数据到 YAML"""
        from lvjiang.core.config_resolver import get_resolver
        try:
            get_resolver().save_merged(_ATTRS_REL, self._data)
            logger.debug(f"配置已保存: {_ATTRS_REL}")
            # 刷新 GameConfigManager 单例
            from lvjiang.apps.yysls.game_config import get_game_config
            manager = get_game_config()
            manager._load()
        except Exception as e:
            logger.error(f"保存失败: {e}")

    # ── 别名管理 ──────────────────────────────────────────────

    def _is_dingyin(self) -> bool:
        """当前词组是否定音词组"""
        if not self._current_affix:
            return False
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        return isinstance(category_data, dict) and category_data.get("_pool") == _POOL_DINGYIN

    def _refresh_pool_radios(self):
        """刷新词组分类单选框状态"""
        self._saving = True
        is_dingyin = self._is_dingyin()
        self._radio_pool_dingyin.setChecked(is_dingyin)
        self._radio_pool_normal.setChecked(not is_dingyin)
        has_affix = self._current_affix is not None
        self._radio_pool_normal.setEnabled(has_affix)
        self._radio_pool_dingyin.setEnabled(has_affix)
        self._saving = False

    def _on_pool_changed(self):
        """词组分类切换时保存并刷新承音列显隐"""
        if self._saving or not self._current_affix:
            return
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.setdefault(self._current_affix, {})
        if self._radio_pool_dingyin.isChecked():
            category_data["_pool"] = _POOL_DINGYIN
        else:
            category_data.pop("_pool", None)  # 普通词组为缺省，不写字段
        self._refresh_table()
        self._save_data()

    def _refresh_unit_combo(self):
        """刷新词组单位下拉框状态"""
        self._saving = True
        if not self._current_affix:
            self._unit_combo.setCurrentText("")
            self._unit_combo.setEnabled(False)
        else:
            affix_caps = self._data.get("affix_caps", {})
            category_data = affix_caps.get(self._current_affix, {})
            unit = category_data.get("_unit", "") if isinstance(category_data, dict) else ""
            self._unit_combo.setCurrentText(unit)
            self._unit_combo.setEnabled(True)
        self._saving = False

    def _refresh_group_radios(self):
        """刷新词条分组单选框状态，并同步分组按钮可见性"""
        self._saving = True
        grouped = self._is_grouped()
        self._radio_group_on.setChecked(grouped)
        self._radio_group_off.setChecked(not grouped)
        has_affix = self._current_affix is not None
        self._radio_group_off.setEnabled(has_affix)
        self._radio_group_on.setEnabled(has_affix)
        self._btn_add_group.setVisible(grouped)
        self._btn_del_group.setVisible(grouped)
        self._saving = False

    def _on_group_mode_changed(self):
        """词条分组切换：_aliases 在 list（不分组）与 dict（分组）间互转，同步写 _group 字段"""
        if self._saving or not self._current_affix:
            return
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.setdefault(self._current_affix, {})
        raw = category_data.get("_aliases", [])
        if self._radio_group_on.isChecked():
            # 开启分组：已有词条名归入默认组
            if not isinstance(raw, dict):
                aliases = raw if isinstance(raw, list) else []
                category_data["_aliases"] = {"默认": aliases} if aliases else {}
            category_data["_group"] = True
        else:
            # 关闭分组：拍平所有组内词条名
            if isinstance(raw, dict):
                flat = [n for names in raw.values() if isinstance(names, list) for n in names]
                if flat:
                    category_data["_aliases"] = flat
                else:
                    category_data.pop("_aliases", None)
            category_data.pop("_group", None)
        self._refresh_group_radios()
        self._refresh_alias_tags()
        self._save_data()

    def _add_group(self):
        """添加词条分组"""
        if not self._current_affix or not self._is_grouped():
            return
        name, ok = QInputDialog.getText(self, "添加分组", "分组名称:")
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.setdefault(self._current_affix, {})
        groups = category_data.setdefault("_aliases", {})
        if not isinstance(groups, dict):
            return
        if name in groups:
            QMessageBox.warning(self, "重复", f"分组 '{name}' 已存在")
            return
        groups[name] = []
        self._refresh_alias_tags()
        self._alias_group_tabs.setCurrentIndex(self._alias_group_tabs.count() - 1)
        self._save_data()

    def _rename_group(self, index: int):
        """双击分组页签重命名（保序替换 _aliases dict 键名）"""
        if index < 0 or not self._current_affix:
            return
        old = self._alias_group_tabs.tabText(index)
        name, ok = QInputDialog.getText(self, "重命名分组", "分组名称:", text=old)
        if not ok or not name:
            return
        name = name.strip()
        if not name or name == old:
            return

        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        raw = category_data.get("_aliases", {})
        if not isinstance(raw, dict) or old not in raw:
            return
        if name in raw:
            QMessageBox.warning(self, "重复", f"分组 '{name}' 已存在")
            return

        category_data["_aliases"] = {
            (name if g == old else g): v for g, v in raw.items()
        }
        self._alias_group_tabs.setTabText(index, name)
        self._save_data()

    def _del_group(self):
        """删除当前分组（连同组内词条名）"""
        if not self._current_affix or not self._is_grouped():
            return
        index = self._alias_group_tabs.currentIndex()
        if index < 0:
            return
        group_name = self._alias_group_tabs.tabText(index)
        count = len(self._get_alias_groups().get(group_name, []))
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除分组 '{group_name}'（含 {count} 个词条名）吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        raw = category_data.get("_aliases", {})
        if isinstance(raw, dict) and group_name in raw:
            del raw[group_name]
            self._refresh_alias_tags()
            self._save_data()

    def _get_raw_aliases(self):
        """获取当前类别的 _aliases 原始字段（list=不分组 / dict=分组）"""
        if not self._current_affix:
            return []
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        return category_data.get("_aliases", []) if isinstance(category_data, dict) else []

    def _is_grouped(self) -> bool:
        """当前词组的词条名称是否分组（优先读 _group 字段，兼容旧数据用 isinstance 推断）"""
        if not self._current_affix:
            return False
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        if not isinstance(category_data, dict):
            return False
        group_flag = category_data.get("_group")
        if group_flag is not None:
            return bool(group_flag)
        # 兼容旧数据：无 _group 字段时按 _aliases 类型推断
        return isinstance(category_data.get("_aliases", []), dict)

    def _get_alias_groups(self) -> dict[str, list[str]]:
        """获取当前类别的词条分组（组名 → 词条名列表）"""
        raw = self._get_raw_aliases()
        if not isinstance(raw, dict):
            return {}
        return {g: names for g, names in raw.items() if isinstance(names, list)}

    def _get_aliases(self) -> list[str]:
        """获取当前类别的全部词条名（分组时拍平所有组）"""
        raw = self._get_raw_aliases()
        if isinstance(raw, dict):
            return [n for names in raw.values() if isinstance(names, list) for n in names]
        return raw if isinstance(raw, list) else []

    def _refresh_alias_tags(self):
        """刷新词条名称显示（不分组=流式标签 / 分组=Tab 页）"""
        grouped = self._is_grouped()
        self._alias_tags_widget.setVisible(not grouped)
        self._alias_group_tabs.setVisible(grouped)

        # 清空现有标签
        while self._alias_tags_layout.count():
            item = self._alias_tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if grouped:
            self._rebuild_group_tabs()
            return

        aliases = self._get_aliases()
        for alias in aliases:
            self._alias_tags_layout.addWidget(self._create_alias_row(alias))

        # 无词条名时显示提示
        if not aliases:
            hint = QLabel("（无词条名，点击 '+ 词条名' 添加）")
            hint.setStyleSheet("color: #888; font-size: 12px;")
            self._alias_tags_layout.addWidget(hint)

    def _rebuild_group_tabs(self):
        """重建分组 Tab 页（保留当前选中组）"""
        prev_index = self._alias_group_tabs.currentIndex()
        prev_name = self._alias_group_tabs.tabText(prev_index) if prev_index >= 0 else None

        while self._alias_group_tabs.count():
            page = self._alias_group_tabs.widget(0)
            self._alias_group_tabs.removeTab(0)
            page.deleteLater()

        for group_name, names in self._get_alias_groups().items():
            page = QWidget()
            rows = QVBoxLayout(page)
            rows.setContentsMargins(0, 0, 0, 0)
            rows.setSpacing(2)
            for alias in names:
                rows.addWidget(self._create_alias_row(alias))
            if not names:
                hint = QLabel("（无词条名，点击 '+ 词条名' 添加）")
                hint.setStyleSheet("color: #888; font-size: 12px;")
                rows.addWidget(hint)
            rows.addStretch()
            self._alias_group_tabs.addTab(page, group_name)

        if prev_name:
            for i in range(self._alias_group_tabs.count()):
                if self._alias_group_tabs.tabText(i) == prev_name:
                    self._alias_group_tabs.setCurrentIndex(i)
                    break

    def _create_alias_row(self, alias: str) -> QWidget:
        """创建单行词条名控件（词条名双击重命名 + 归属下拉 + 部位按钮 + 删除）"""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(2, 1, 2, 1)
        row_layout.setSpacing(6)

        # 词条名（双击重命名）
        name_widget = _AliasTag(alias, self._rename_alias)
        name_widget.setMinimumWidth(160)
        name_widget.setStyleSheet(
            "QWidget { background-color: #e0e0e0; border-radius: 3px; }"
        )
        name_layout = QHBoxLayout(name_widget)
        name_layout.setContentsMargins(6, 2, 6, 2)
        label = QLabel(alias)
        label.setStyleSheet("background: transparent; font-size: 12px;")
        name_layout.addWidget(label)
        name_layout.addStretch()
        row_layout.addWidget(name_widget)

        # 归属下拉（空串 + 5 类；定音词组禁用并置空）
        combo = QComboBox()
        combo.setFixedWidth(90)
        combo.addItem("")
        combo.addItems(list(AFFIX_CATEGORY_NAMES))
        if self._is_dingyin():
            combo.setCurrentText("")
            combo.setEnabled(False)
        else:
            combo.setCurrentText(self._get_affix_category(alias))
            combo.currentTextChanged.connect(
                lambda text, a=alias: self._on_category_changed(a, text))
        row_layout.addWidget(combo)

        # 词条部位（点击弹七部位多选；全选展示「全部」；仅普通词组启用）
        parts_btn = QPushButton(self._format_parts(self._get_affix_parts(alias)))
        parts_btn.setFixedWidth(130)
        if self._is_dingyin():
            parts_btn.setEnabled(False)
        else:
            parts_btn.clicked.connect(
                lambda _c, a=alias, b=parts_btn: self._pick_affix_parts(a, b))
        row_layout.addWidget(parts_btn)

        row_layout.addStretch()

        # 删除按钮
        del_btn = QPushButton("×")
        del_btn.setFixedSize(16, 16)
        del_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #666; "
            "font-weight: bold; font-size: 14px; padding: 0; }"
            "QPushButton:hover { color: #c00; }"
        )
        del_btn.clicked.connect(lambda: self._remove_alias(alias))
        row_layout.addWidget(del_btn)

        return row

    def _get_affix_category(self, alias: str) -> str:
        """从 self._data['affix_categories'] 反查词条归属（无归属返回空串）"""
        categories = self._data.get("affix_categories") or {}
        for cat, names in categories.items():
            if isinstance(names, list) and alias in names:
                return cat
        return ""

    def _get_affix_parts(self, alias: str) -> list[str]:
        """从 self._data['affix_parts'] 读词条部位（未配置 = 全部位）"""
        parts = (self._data.get("affix_parts") or {}).get(alias)
        if isinstance(parts, list):
            valid = [p for p in EQUIP_PART_NAMES if p in parts]
            if valid:
                return valid
        return list(EQUIP_PART_NAMES)

    @staticmethod
    def _format_parts(parts: list[str]) -> str:
        """部位按钮文本：全选展示「全部」，否则 / 拼接（如 环/佩）"""
        if len(parts) >= len(EQUIP_PART_NAMES):
            return "全部"
        return "/".join(parts)

    def _pick_affix_parts(self, alias: str, btn: QPushButton):
        """弹部位多选对话框；全选（或全不选）视为不限部位，不落盘"""
        dlg = _PartsDialog(self._get_affix_parts(alias), self)
        if not dlg.exec():
            return
        parts = dlg.selected()
        if not parts or len(parts) >= len(EQUIP_PART_NAMES):
            self._drop_affix_parts(alias)
            btn.setText("全部")
        else:
            self._data.setdefault("affix_parts", {})[alias] = parts
            btn.setText(self._format_parts(parts))
        self._save_data()

    def _drop_affix_parts(self, alias: str):
        """移除词条的部位配置（affix_parts 空时连顶层键一并移除）"""
        affix_parts = self._data.get("affix_parts")
        if affix_parts and alias in affix_parts:
            affix_parts.pop(alias)
            if not affix_parts:
                self._data.pop("affix_parts", None)

    def _on_category_changed(self, alias: str, category: str):
        """归属下拉切换：先从所有归属移除，再加入所选（空串=清除）"""
        if self._saving:
            return
        categories = self._data.setdefault("affix_categories", {})
        for names in categories.values():
            if isinstance(names, list) and alias in names:
                names.remove(alias)
        if category:
            categories.setdefault(category, []).append(alias)
        self._save_data()

    def _rename_alias(self, alias: str):
        """双击词条名标签重命名（原位替换，兼容分组/不分组形态）"""
        name, ok = QInputDialog.getText(self, "重命名词条名", "原始词条名称:", text=alias)
        if not ok or not name:
            return
        name = name.strip()
        if not name or name == alias:
            return

        # 重复 / 跨类别冲突检查
        if name in self._get_aliases():
            QMessageBox.warning(self, "重复", f"词条名 '{name}' 已存在")
            return
        if name in self._get_all_aliases():
            QMessageBox.warning(self, "冲突", f"词条名 '{name}' 已被其他类别使用")
            return

        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        raw = category_data.get("_aliases", [])
        if isinstance(raw, dict):
            for names in raw.values():
                if isinstance(names, list) and alias in names:
                    names[names.index(alias)] = name
                    break
            else:
                return
        elif isinstance(raw, list) and alias in raw:
            raw[raw.index(alias)] = name
        else:
            return

        # 部位配置随词条名同步迁移
        affix_parts = self._data.get("affix_parts") or {}
        if alias in affix_parts:
            affix_parts[name] = affix_parts.pop(alias)

        self._refresh_alias_tags()
        self._save_data()

    def _add_alias(self):
        """添加新词条名（分组模式下加入当前选中组）"""
        if not self._current_affix:
            QMessageBox.information(self, "提示", "请先选择词条类别")
            return

        name, ok = QInputDialog.getText(self, "添加词条名", "原始词条名称:")
        if not ok or not name:
            return

        name = name.strip()
        if not name:
            return

        # 检查是否已存在
        aliases = self._get_aliases()
        if name in aliases:
            QMessageBox.warning(self, "重复", f"词条名 '{name}' 已存在")
            return

        # 检查是否已被其他类别占用
        all_aliases = self._get_all_aliases()
        if name in all_aliases:
            QMessageBox.warning(self, "冲突", f"词条名 '{name}' 已被其他类别使用")
            return

        # 添加到数据
        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.setdefault(self._current_affix, {})
        if self._is_grouped():
            index = self._alias_group_tabs.currentIndex()
            if index < 0:
                QMessageBox.information(self, "提示", "请先点击 '+ 分组' 创建分组")
                return
            group_name = self._alias_group_tabs.tabText(index)
            category_data["_aliases"].setdefault(group_name, []).append(name)
        else:
            if "_aliases" not in category_data:
                category_data["_aliases"] = []
            category_data["_aliases"].append(name)

        self._refresh_alias_tags()
        self._save_data()

    def _remove_alias(self, alias: str):
        """移除词条名（分组模式下自动定位所在组）"""
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要移除词条名 '{alias}' 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        affix_caps = self._data.get("affix_caps", {})
        category_data = affix_caps.get(self._current_affix, {})
        raw = category_data.get("_aliases", [])
        if isinstance(raw, dict):
            for names in raw.values():
                if isinstance(names, list) and alias in names:
                    names.remove(alias)
                    self._drop_affix_parts(alias)
                    self._refresh_alias_tags()
                    self._save_data()
                    return
        elif isinstance(raw, list) and alias in raw:
            raw.remove(alias)
            self._drop_affix_parts(alias)
            self._refresh_alias_tags()
            self._save_data()

    def _get_all_aliases(self) -> dict[str, str]:
        """获取所有类别的词条名映射 {alias: category}（兼容分组形态）"""
        result = {}
        affix_caps = self._data.get("affix_caps", {})
        for category, data in affix_caps.items():
            if not isinstance(data, dict):
                continue
            raw = data.get("_aliases", [])
            if isinstance(raw, dict):
                for names in raw.values():
                    if isinstance(names, list):
                        for alias in names:
                            result[alias] = category
            elif isinstance(raw, list):
                for alias in raw:
                    result[alias] = category
        return result
