"""词条属性上限面板

管理各词条在不同等级的最大值。
左侧词条列表，右侧等级-上限表格。
自动保存，删除时确认。
"""

from pathlib import Path

import yaml
from loguru import logger
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QHeaderView, QLabel,
    QInputDialog, QComboBox,
)
from PyQt6.QtCore import Qt

# 配置文件路径
_ATTRS_PATH = Path("config/system/attributes.yaml")

# 承音比例（默认 94%）
_CHENGYIN_RATIO = 0.94


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

        # 左侧：词条列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("词条"))
        
        self._affix_list = QListWidget()
        self._affix_list.currentRowChanged.connect(self._on_affix_changed)
        left_layout.addWidget(self._affix_list)

        # 添加/删除词条按钮
        affix_btn_layout = QHBoxLayout()
        self._btn_add_affix = QPushButton("+ 词条")
        self._btn_add_affix.clicked.connect(self._add_affix)
        affix_btn_layout.addWidget(self._btn_add_affix)

        self._btn_del_affix = QPushButton("- 词条")
        self._btn_del_affix.clicked.connect(self._del_affix)
        affix_btn_layout.addWidget(self._btn_del_affix)
        
        left_layout.addLayout(affix_btn_layout)

        splitter.addWidget(left_widget)

        # 右侧：表格 + 按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["等级", "上限", "单位", "承音"])
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

        # 填充词条列表
        self._affix_list.clear()
        affix_caps = self._data.get("affix_caps", {})
        for affix_name in affix_caps.keys():
            self._affix_list.addItem(affix_name)

        if self._affix_list.count() > 0:
            self._affix_list.setCurrentRow(0)

    def _on_affix_changed(self, row: int):
        """切换词条时更新表格"""
        affix_caps = self._data.get("affix_caps", {})
        affix_names = list(affix_caps.keys())
        
        if row < 0 or row >= len(affix_names):
            self._current_affix = None
            self._table.setRowCount(0)
            return

        self._current_affix = affix_names[row]
        self._refresh_table()

    def _refresh_table(self):
        """刷新表格内容"""
        if not self._current_affix:
            self._table.setRowCount(0)
            return

        affix_caps = self._data.get("affix_caps", {})
        level_data = affix_caps.get(self._current_affix, {})
        
        if not level_data:
            self._table.setRowCount(0)
            return

        # 按等级排序
        levels = sorted(level_data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0, reverse=True)
        self._table.setRowCount(len(levels))

        # 阻止 cellChanged 信号触发保存
        self._saving = True
        
        for row, level in enumerate(levels):
            entry = level_data[level]
            
            # 兼容旧格式（直接是数值）
            if isinstance(entry, (int, float)):
                cap = entry
                unit = ""
            else:
                cap = entry.get("cap", 0)
                unit = entry.get("unit", "")
            
            # 计算承音值（94%）
            chengyin = round(cap * _CHENGYIN_RATIO, 2)

            # 等级（可编辑）
            level_item = QTableWidgetItem(str(level))
            self._table.setItem(row, 0, level_item)

            # 上限（可编辑）
            cap_item = QTableWidgetItem(str(cap))
            self._table.setItem(row, 1, cap_item)

            # 单位（下拉选择）
            unit_combo = QComboBox()
            unit_combo.addItems(["", "%"])
            unit_combo.setCurrentText(unit)
            unit_combo.currentTextChanged.connect(lambda text, r=row: self._on_unit_changed(r, text))
            self._table.setCellWidget(row, 2, unit_combo)

            # 承音（只读）
            chengyin_item = QTableWidgetItem(str(chengyin))
            chengyin_item.setFlags(chengyin_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            chengyin_item.setForeground(Qt.GlobalColor.gray)
            self._table.setItem(row, 3, chengyin_item)

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
            chengyin_item = self._table.item(row, 3)
            if chengyin_item:
                chengyin_item.setText(str(chengyin))
        except ValueError:
            pass

    def _on_unit_changed(self, row: int, text: str):
        """单位改变时自动保存"""
        self._sync_table_to_data()
        self._save_data()

    def _add_affix(self):
        """添加新词条"""
        name, ok = QInputDialog.getText(self, "添加词条", "词条名称:")
        if not ok or not name:
            return
        
        name = name.strip()
        if not name:
            return

        affix_caps = self._data.setdefault("affix_caps", {})
        if name in affix_caps:
            QMessageBox.warning(self, "重复", f"词条 '{name}' 已存在")
            return

        affix_caps[name] = {}
        self._affix_list.addItem(name)
        self._affix_list.setCurrentRow(self._affix_list.count() - 1)
        self._save_data()

    def _del_affix(self):
        """删除当前词条"""
        if not self._current_affix:
            return

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除词条 '{self._current_affix}' 吗？",
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
        
        # 单位列用下拉框
        unit_combo = QComboBox()
        unit_combo.addItems(["", "%"])
        unit_combo.currentTextChanged.connect(lambda text, r=row: self._on_unit_changed(r, text))
        self._table.setCellWidget(row, 2, unit_combo)

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
        
        # 清空当前词条数据，重新从表格读取
        level_caps.clear()
        
        for row in range(self._table.rowCount()):
            level_item = self._table.item(row, 0)
            cap_item = self._table.item(row, 1)
            unit_combo = self._table.cellWidget(row, 2)
            
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
                unit = unit_combo.currentText() if unit_combo else ""
                level_caps[level] = {"cap": cap, "unit": unit}
            except ValueError:
                pass

    def _save_data(self):
        """保存数据到 YAML"""
        try:
            with open(_ATTRS_PATH, "w", encoding="utf-8") as f:
                yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.debug(f"配置已保存: {_ATTRS_PATH}")
        except Exception as e:
            logger.error(f"保存失败: {e}")
