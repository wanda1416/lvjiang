"""基础属性规则面板

管理装备基础属性规则（用于品阶推断）。
左侧分类列表，右侧等级×品阶表格。
自动保存，删除时确认。
"""

from pathlib import Path

import yaml
from loguru import logger
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QHeaderView, QLabel,
)
from PyQt6.QtCore import Qt

# 配置文件路径
_ATTRS_PATH = Path("config/system/attributes.yaml")

# 分类显示名称
_CATEGORY_NAMES = {
    "weapon": "武器",
    "ring": "戒指",
    "pendant": "佩饰",
    "armor_other": "防具（非胸甲）",
    "chest": "胸甲",
}

# 品阶显示名称
_QUALITY_NAMES = {
    "gold": "金装",
    "purple": "紫装",
    "blue": "蓝装",
}


class BaseAttrPanel(QWidget):
    """基础属性规则面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}  # 完整配置数据
        self._current_category: str | None = None
        self._saving = False  # 防止递归保存
        self._init_ui()
        self._load_data()

    def _init_ui(self):
        layout = QHBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧：分类列表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("分类"))
        
        self._category_list = QListWidget()
        self._category_list.currentRowChanged.connect(self._on_category_changed)
        left_layout.addWidget(self._category_list)

        splitter.addWidget(left_widget)

        # 右侧：表格 + 按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

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

        # 填充分类列表
        self._category_list.clear()
        base_attrs = self._data.get("base_attrs", {})
        for cat_key in base_attrs.keys():
            display_name = _CATEGORY_NAMES.get(cat_key, cat_key)
            self._category_list.addItem(display_name)

        if self._category_list.count() > 0:
            self._category_list.setCurrentRow(0)

    def _on_category_changed(self, row: int):
        """切换分类时更新表格"""
        base_attrs = self._data.get("base_attrs", {})
        categories = list(base_attrs.keys())
        
        if row < 0 or row >= len(categories):
            self._current_category = None
            self._table.setRowCount(0)
            return

        self._current_category = categories[row]
        self._refresh_table()

    def _refresh_table(self):
        """刷新表格内容"""
        if not self._current_category:
            return

        base_attrs = self._data.get("base_attrs", {})
        cat_data = base_attrs.get(self._current_category, {})
        
        if not cat_data:
            self._table.setRowCount(0)
            self._table.setColumnCount(0)
            return

        # 列：等级 + 各品阶
        qualities = ["gold", "purple", "blue"]
        columns = ["等级"] + [_QUALITY_NAMES.get(q, q) for q in qualities]
        
        self._table.setColumnCount(len(columns))
        self._table.setHorizontalHeaderLabels(columns)

        # 行：每个等级一行
        levels = sorted(cat_data.keys(), key=lambda x: int(x) if str(x).isdigit() else 0, reverse=True)
        self._table.setRowCount(len(levels))

        # 阻止 cellChanged 信号触发保存
        self._saving = True
        
        for row, level in enumerate(levels):
            level_data = cat_data[level]
            
            # 等级列（可编辑）
            level_item = QTableWidgetItem(str(level))
            self._table.setItem(row, 0, level_item)

            # 品阶列（可编辑）
            for col, quality in enumerate(qualities, start=1):
                q_data = level_data.get(quality)
                if q_data is None:
                    text = ""
                elif isinstance(q_data, dict):
                    # 范围值 {min, max}
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

    def _add_level(self):
        """添加新空行"""
        if not self._current_category:
            return

        # 直接插入空行
        row = self._table.rowCount()
        self._table.insertRow(row)
        
        # 所有列留空，让用户填写
        for col in range(self._table.columnCount()):
            item = QTableWidgetItem("")
            self._table.setItem(row, col, item)

    def _sync_table_to_data(self):
        """将表格数据同步回 _data"""
        if not self._current_category:
            return

        base_attrs = self._data.setdefault("base_attrs", {})
        
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

        base_attrs[self._current_category] = new_cat_data

    def _save_data(self):
        """保存数据到 YAML"""
        try:
            with open(_ATTRS_PATH, "w", encoding="utf-8") as f:
                yaml.dump(self._data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            logger.debug(f"配置已保存: {_ATTRS_PATH}")
        except Exception as e:
            logger.error(f"保存失败: {e}")
