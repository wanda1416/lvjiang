"""玩家信息元数据定义对话框

编辑 profile.yaml，定义 user.json 中可能存在的字段及其属性。
分组以顶部 Tab 形式展示，支持右键新建/删除/重命名分组。
字段表格支持新增/删除字段、修改 key、调整分组、调整排序。
"""
from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lvjiang.constants import SESSION_CONFIG_DIR
from lvjiang.core.config import load_yaml, save_yaml

_PROFILE_PATH = SESSION_CONFIG_DIR / "profile.yaml"


class _GroupTab(QWidget):
    """单个分组的字段编辑页"""

    def __init__(self, group_key: str, parent=None):
        super().__init__(parent)
        self._group_key = group_key
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.addStretch()

        btn_up = QPushButton("↑ 上移")
        btn_up.setFixedWidth(70)
        btn_up.setToolTip("上移选中字段")
        btn_up.clicked.connect(self._move_up)
        toolbar.addWidget(btn_up)

        btn_down = QPushButton("↓ 下移")
        btn_down.setFixedWidth(70)
        btn_down.setToolTip("下移选中字段")
        btn_down.clicked.connect(self._move_down)
        toolbar.addWidget(btn_down)

        btn_add = QPushButton("+ 新增")
        btn_add.setFixedWidth(70)
        btn_add.setToolTip("在当前分组新增一个字段")
        btn_add.clicked.connect(self._add_field)
        toolbar.addWidget(btn_add)

        btn_del = QPushButton("- 删除")
        btn_del.setFixedWidth(70)
        btn_del.setToolTip("删除选中的字段")
        btn_del.clicked.connect(self._delete_field)
        toolbar.addWidget(btn_del)

        layout.addLayout(toolbar)

        # 字段表格：字段 | 标签 | 分组 | 类型 | 数据源 | 只读
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(["字段 Key", "标签", "分组", "类型", "数据源", "只读"])
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        # 单元格编辑完成后实时保存
        self._table.cellChanged.connect(self._on_cell_changed)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 100)
        self._table.setColumnWidth(3, 80)
        self._table.setColumnWidth(5, 60)

        layout.addWidget(self._table)

    @property
    def table(self) -> QTableWidget:
        return self._table

    def _get_parent_dialog(self) -> "MetadataDialog | None":
        """获取父级 MetadataDialog"""
        parent = self.parent()
        while parent and not isinstance(parent, MetadataDialog):
            parent = parent.parent()
        return parent if isinstance(parent, MetadataDialog) else None

    def _add_field(self):
        """新增字段（通过父级对话框触发）"""
        dialog = self._get_parent_dialog()
        if dialog:
            dialog._add_field_to_group(self._group_key)

    def _delete_field(self):
        """删除选中的字段"""
        dialog = self._get_parent_dialog()
        if dialog:
            dialog._delete_field_from_group(self._group_key)

    def _move_up(self):
        """上移选中字段"""
        row = self._table.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self._table.setCurrentCell(row - 1, self._table.currentColumn())
        # 实时保存
        dialog = self._get_parent_dialog()
        if dialog:
            dialog._save_now()

    def _move_down(self):
        """下移选中字段"""
        row = self._table.currentRow()
        if row < 0 or row >= self._table.rowCount() - 1:
            return
        self._swap_rows(row, row + 1)
        self._table.setCurrentCell(row + 1, self._table.currentColumn())
        # 实时保存
        dialog = self._get_parent_dialog()
        if dialog:
            dialog._save_now()

    def _on_cell_changed(self, row: int, col: int):
        """单元格编辑完成后实时保存"""
        dialog = self._get_parent_dialog()
        if dialog:
            dialog._save_now()

    def _swap_rows(self, row1: int, row2: int):
        """交换两行数据（仅交换 QTableWidgetItem，不交换 cellWidget）"""
        for col in range(self._table.columnCount()):
            item1 = self._table.takeItem(row1, col)
            item2 = self._table.takeItem(row2, col)
            self._table.setItem(row1, col, item2 or QTableWidgetItem())
            self._table.setItem(row2, col, item1 or QTableWidgetItem())

    def _on_table_context_menu(self, pos):
        """表格右键菜单"""
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction("新增字段", self._add_field)
        row = self._table.rowAt(pos.y())
        if row >= 0:
            menu.addAction("删除此字段", self._delete_field)
            menu.addSeparator()
            menu.addAction("上移", self._move_up)
            menu.addAction("下移", self._move_down)
        menu.exec(self._table.mapToGlobal(pos))


class MetadataDialog(QDialog):
    """玩家信息元数据定义对话框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("玩家信息元数据定义")
        self.setMinimumSize(950, 650)
        self._data: dict = {}
        self._setup_ui()
        self._load_data()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # 说明
        info = QLabel("定义 user.json 中可能存在的字段及其属性。右键分组 Tab 可新建/删除/重命名分组。")
        info.setStyleSheet("color: #666666; margin-bottom: 10px;")
        layout.addWidget(info)

        # 分组 Tab
        self._tab_widget = QTabWidget()
        self._tab_widget.setTabsClosable(False)
        self._tab_widget.setMovable(True)
        self._tab_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tab_widget.customContextMenuRequested.connect(self._on_tab_context_menu)
        layout.addWidget(self._tab_widget, stretch=1)

        # 按钮行
        btn_row = QHBoxLayout()

        btn_add_group = QPushButton("+ 新建分组")
        btn_add_group.setFixedWidth(100)
        btn_add_group.clicked.connect(self._add_group)
        btn_row.addWidget(btn_add_group)

        btn_row.addStretch()

        btn_save = QPushButton("确定")
        btn_save.setFixedWidth(80)
        btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(btn_save)

        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedWidth(80)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        layout.addLayout(btn_row)

    def _load_data(self):
        """加载 profile.yaml"""
        if not _PROFILE_PATH.exists():
            return

        try:
            self._data = load_yaml(_PROFILE_PATH)
        except Exception as e:
            logger.error(f"加载 profile.yaml 失败: {e}")
            return

        self._rebuild_tabs()

    def _get_group_options(self) -> list[tuple[str, str]]:
        """获取所有分组选项 [(key, label), ...]"""
        groups = self._data.get("groups", {})
        return [(k, v.get("label", k)) for k, v in groups.items()]

    def _rebuild_tabs(self):
        """根据 self._data 重建所有分组 Tab"""
        while self._tab_widget.count() > 0:
            self._tab_widget.removeTab(0)

        groups = self._data.get("groups", {})
        sorted_groups = sorted(groups.items(), key=lambda x: x[1].get("order", 0))

        for group_key, group_info in sorted_groups:
            label = group_info.get("label", group_key)
            tab = _GroupTab(group_key)
            self._tab_widget.addTab(tab, label)
            self._populate_group_table(tab, group_key)

    def _populate_group_table(self, tab: _GroupTab, group_key: str):
        """用指定分组的数据填充表格"""
        fields = self._data.get("fields", [])
        group_fields = [f for f in fields if f.get("group") == group_key]

        # 暂时断开信号，避免填充时触发保存
        tab.table.blockSignals(True)
        tab.table.setRowCount(len(group_fields))
        group_options = self._get_group_options()

        for row, field_def in enumerate(group_fields):
            # 字段 key（可编辑）
            key_item = QTableWidgetItem(field_def.get("key", ""))
            tab.table.setItem(row, 0, key_item)

            # 标签（可编辑）
            label_item = QTableWidgetItem(field_def.get("label", ""))
            tab.table.setItem(row, 1, label_item)

            # 分组（下拉选择）
            group_combo = QComboBox()
            for gk, gl in group_options:
                group_combo.addItem(gl, gk)
            current_group = field_def.get("group", "")
            idx = group_combo.findData(current_group)
            if idx >= 0:
                group_combo.setCurrentIndex(idx)
            # 连接信号：实时保存
            group_combo.currentIndexChanged.connect(lambda: self._save_now())
            tab.table.setCellWidget(row, 2, group_combo)

            # 类型（下拉选择）
            type_combo = QComboBox()
            type_combo.addItems(["str", "int", "bool", "date", "computed", "duration"])
            current_type = field_def.get("type", "str")
            idx = type_combo.findText(current_type)
            if idx >= 0:
                type_combo.setCurrentIndex(idx)
            # 连接信号：实时保存
            type_combo.currentIndexChanged.connect(lambda: self._save_now())
            tab.table.setCellWidget(row, 3, type_combo)

            # 数据源（可编辑）
            source_item = QTableWidgetItem(field_def.get("source", ""))
            tab.table.setItem(row, 4, source_item)

            # 只读（复选框）
            readonly_cb = QCheckBox()
            readonly_cb.setChecked(field_def.get("readonly", False))
            # 连接信号：实时保存
            readonly_cb.stateChanged.connect(lambda: self._save_now())
            tab.table.setCellWidget(row, 5, readonly_cb)

        tab.table.blockSignals(False)

    def _sync_tab_to_data(self, tab: _GroupTab):
        """将指定 Tab 的表格数据回写到 self._data"""
        group_key = tab._group_key
        fields = self._data.get("fields", [])

        # 收集新的字段顺序和数据
        new_group_fields = []
        for local_row in range(tab.table.rowCount()):
            row_data: dict[str, str | bool] = {}

            # 字段 key
            key_item = tab.table.item(local_row, 0)
            if key_item:
                row_data["key"] = key_item.text()

            # 标签
            label_item = tab.table.item(local_row, 1)
            if label_item:
                row_data["label"] = label_item.text()

            # 分组
            group_combo = tab.table.cellWidget(local_row, 2)
            if isinstance(group_combo, QComboBox):
                row_data["group"] = group_combo.currentData()

            # 类型
            type_combo = tab.table.cellWidget(local_row, 3)
            if isinstance(type_combo, QComboBox):
                row_data["type"] = type_combo.currentText()

            # 数据源
            source_item = tab.table.item(local_row, 4)
            if source_item:
                row_data["source"] = source_item.text()

            # 只读
            readonly_cb = tab.table.cellWidget(local_row, 5)
            if isinstance(readonly_cb, QCheckBox):
                row_data["readonly"] = readonly_cb.isChecked()

            new_group_fields.append(row_data)

        # 移除原分组字段，插入新顺序
        fields = [f for f in fields if f.get("group") != group_key]
        fields.extend(new_group_fields)
        self._data["fields"] = fields

    def _sync_all_tabs(self):
        """同步所有 Tab 的数据"""
        for i in range(self._tab_widget.count()):
            tab = self._tab_widget.widget(i)
            if isinstance(tab, _GroupTab):
                self._sync_tab_to_data(tab)

    def _save_now(self):
        """立即同步并保存"""
        self._sync_all_tabs()
        try:
            save_yaml(_PROFILE_PATH, self._data)
            logger.debug("已实时保存 profile.yaml")
        except Exception as e:
            logger.error(f"实时保存失败: {e}")

    # ─── 分组操作 ────────────────────────────────────────────

    def _on_tab_context_menu(self, pos):
        """Tab 右键菜单"""
        from PyQt6.QtWidgets import QMenu
        tab_index = self._tab_widget.tabBar().tabAt(pos)
        if tab_index < 0:
            return

        menu = QMenu(self)
        menu.addAction("重命名分组", lambda: self._rename_group(tab_index))
        menu.addAction("删除分组", lambda: self._delete_group(tab_index))
        menu.exec(self._tab_widget.mapToGlobal(pos))

    def _add_group(self):
        """新建分组"""
        name, ok = QInputDialog.getText(self, "新建分组", "分组名称:")
        if not ok or not name:
            return

        key = name.strip().lower().replace(" ", "_")
        groups = self._data.get("groups", {})
        if key in groups:
            QMessageBox.warning(self, "重复", f"分组 '{key}' 已存在")
            return

        max_order = max((g.get("order", 0) for g in groups.values()), default=0)
        groups[key] = {"label": name.strip(), "order": max_order + 1}
        self._data["groups"] = groups

        tab = _GroupTab(key)
        self._tab_widget.addTab(tab, name.strip())
        self._tab_widget.setCurrentWidget(tab)

    def _rename_group(self, tab_index: int):
        """重命名分组"""
        tab = self._tab_widget.widget(tab_index)
        if not isinstance(tab, _GroupTab):
            return

        group_key = tab._group_key
        groups = self._data.get("groups", {})
        current_label = groups.get(group_key, {}).get("label", group_key)

        new_label, ok = QInputDialog.getText(
            self, "重命名分组", "新名称:", text=current_label
        )
        if not ok or not new_label:
            return

        groups[group_key]["label"] = new_label.strip()
        self._tab_widget.setTabText(tab_index, new_label.strip())

    def _delete_group(self, tab_index: int):
        """删除分组"""
        tab = self._tab_widget.widget(tab_index)
        if not isinstance(tab, _GroupTab):
            return

        group_key = tab._group_key
        groups = self._data.get("groups", {})
        label = groups.get(group_key, {}).get("label", group_key)

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除分组 '{label}' 及其所有字段吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        fields = self._data.get("fields", [])
        self._data["fields"] = [f for f in fields if f.get("group") != group_key]
        del groups[group_key]
        self._tab_widget.removeTab(tab_index)

    # ─── 字段操作 ────────────────────────────────────────────

    def _add_field_to_group(self, group_key: str):
        """向指定分组新增字段（单个对话框同时输入 key 和 label）"""
        from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit

        dialog = QDialog(self)
        dialog.setWindowTitle("新增字段")
        dialog.setMinimumWidth(300)
        layout = QFormLayout(dialog)

        key_input = QLineEdit()
        key_input.setPlaceholderText("英文，如 energy_value")
        layout.addRow("字段 Key:", key_input)

        label_input = QLineEdit()
        label_input.setPlaceholderText("中文，如 心力值")
        layout.addRow("字段标签:", label_input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = QPushButton("确定")
        btn_ok.setEnabled(False)
        btn_row.addWidget(btn_ok)
        btn_cancel = QPushButton("取消")
        btn_row.addWidget(btn_cancel)
        layout.addRow(btn_row)

        # 实时校验
        error_label = QLabel()
        error_label.setStyleSheet("color: red;")
        layout.addRow(error_label)

        def validate():
            key = key_input.text().strip()
            label = label_input.text().strip()
            fields = self._data.get("fields", [])

            if not key:
                error_label.setText("请输入字段 Key")
                btn_ok.setEnabled(False)
                return
            if not key.replace("_", "").isalnum():
                error_label.setText("Key 只能包含字母、数字和下划线")
                btn_ok.setEnabled(False)
                return
            if any(f.get("key") == key for f in fields):
                error_label.setText(f"Key '{key}' 已存在")
                btn_ok.setEnabled(False)
                return
            if not label:
                error_label.setText("请输入字段标签")
                btn_ok.setEnabled(False)
                return

            error_label.setText("")
            btn_ok.setEnabled(True)

        key_input.textChanged.connect(validate)
        label_input.textChanged.connect(validate)
        btn_ok.clicked.connect(dialog.accept)
        btn_cancel.clicked.connect(dialog.reject)

        if not dialog.exec():
            return

        key = key_input.text().strip()
        label = label_input.text().strip()

        new_field = {
            "key": key,
            "label": label,
            "group": group_key,
            "type": "str",
            "source": "",
            "readonly": False,
        }
        fields = self._data.get("fields", [])
        fields.append(new_field)

        for i in range(self._tab_widget.count()):
            tab = self._tab_widget.widget(i)
            if isinstance(tab, _GroupTab) and tab._group_key == group_key:
                self._populate_group_table(tab, group_key)
                break

        # 实时保存
        self._save_now()

    def _delete_field_from_group(self, group_key: str):
        """从指定分组删除选中的字段"""
        for i in range(self._tab_widget.count()):
            tab = self._tab_widget.widget(i)
            if isinstance(tab, _GroupTab) and tab._group_key == group_key:
                row = tab.table.currentRow()
                if row < 0:
                    QMessageBox.information(self, "提示", "请先选择要删除的字段")
                    return

                key_item = tab.table.item(row, 0)
                if not key_item:
                    return
                field_key = key_item.text()

                reply = QMessageBox.question(
                    self, "确认删除",
                    f"确定要删除字段 '{field_key}' 吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

                fields = self._data.get("fields", [])
                self._data["fields"] = [f for f in fields if f.get("key") != field_key]
                self._populate_group_table(tab, group_key)
                # 实时保存
                self._save_now()
                break

    # ─── 保存 ────────────────────────────────────────────────

    def _on_save(self):
        """保存配置"""
        self._sync_all_tabs()

        # 验证 key 唯一性
        fields = self._data.get("fields", [])
        keys = [f.get("key") for f in fields]
        duplicates = [k for k in keys if keys.count(k) > 1]
        if duplicates:
            QMessageBox.warning(self, "Key 重复", f"以下字段 key 重复: {', '.join(set(duplicates))}")
            return

        try:
            save_yaml(_PROFILE_PATH, self._data)
            logger.info("已保存 profile.yaml")
            self.accept()
        except Exception as e:
            logger.error(f"保存 profile.yaml 失败: {e}")
            QMessageBox.warning(self, "保存失败", f"保存 profile.yaml 失败:\n{e}")
