"""区域面板混入类 - 区域列表构建、刷新、CRUD、编辑弹窗"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox,
    QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt

from ...core.scene_registry import get_registry, sync_scene_cache
from ...core.scene_loader import RegionDef, VALID_REGION_TYPES


class RegionPanelMixin:
    """区域面板混入类

    依赖主类提供:
        _scene_key, _canvas, _region_table,
        _btn_del_region, _refresh_lists()
    """

    # ─── 面板构建 ────────────────────────────────────────

    def _build_region_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._region_table = QTableWidget()
        self._region_table.setColumnCount(5)
        self._region_table.setHorizontalHeaderLabels(["名称", "Key", "类型", "含文本", "可点击"])
        # 列宽：名称/Key 自适应内容，后三列固定窄宽
        header = self._region_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(3, 50)
        header.resizeSection(4, 50)
        self._region_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._region_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._region_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._region_table.verticalHeader().setVisible(False)
        self._region_table.currentCellChanged.connect(self._on_region_table_selection)
        self._region_table.cellDoubleClicked.connect(self._on_edit_region_from_table)
        self._region_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._region_table.customContextMenuRequested.connect(self._on_region_table_context_menu)
        layout.addWidget(self._region_table)

        btn_row = QHBoxLayout()
        self._btn_new_region = QPushButton("+ 创建区域")
        self._btn_new_region.clicked.connect(self._on_new_region)
        btn_row.addWidget(self._btn_new_region)
        self._btn_del_region = QPushButton("删除区域")
        self._btn_del_region.clicked.connect(self._on_delete_region)
        self._btn_del_region.setEnabled(False)
        btn_row.addWidget(self._btn_del_region)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    # ─── 列表刷新 ────────────────────────────────────────

    def _refresh_region_list(self):
        """刷新区域表格，显示 name(key)、类型、含文本、可点击"""
        self._region_table.blockSignals(True)
        self._region_table.setRowCount(0)
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene:
            self._region_table.blockSignals(False)
            return
        assigned = self._canvas.get_regions()
        assigned_keys = {r.key for r in assigned}
        for region_def in scene.regions:
            row = self._region_table.rowCount()
            self._region_table.insertRow(row)
            # 名称
            status = "\u2713" if region_def.key in assigned_keys else "\u25cb"
            name_item = QTableWidgetItem(f"{status} {region_def.name}")
            if region_def.key not in assigned_keys:
                name_item.setForeground(Qt.GlobalColor.gray)
            self._region_table.setItem(row, 0, name_item)
            # Key
            key_item = QTableWidgetItem(region_def.key)
            if region_def.key not in assigned_keys:
                key_item.setForeground(Qt.GlobalColor.gray)
            self._region_table.setItem(row, 1, key_item)
            # 类型
            self._region_table.setItem(row, 2, QTableWidgetItem(region_def.type))
            # 含文本
            text_item = QTableWidgetItem("\u2713" if region_def.is_text else "")
            text_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._region_table.setItem(row, 3, text_item)
            # 可点击
            click_item = QTableWidgetItem("\u2713" if region_def.is_clickable else "")
            click_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._region_table.setItem(row, 4, click_item)
        self._region_table.blockSignals(False)

    # ─── 事件处理 ────────────────────────────────────────

    def _on_region_table_selection(self, row, col, prev_row, prev_col):
        """表格行选中时更新删除按钮状态"""
        self._btn_del_region.setEnabled(row >= 0)
        # 同步画布选中
        if row < 0:
            return
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or row >= len(scene.regions):
            return
        key = scene.regions[row].key
        # 在画布的已绑定区域中查找索引
        canvas_regions = self._canvas.get_regions()
        for i, r in enumerate(canvas_regions):
            if r.key == key:
                self._canvas.select_region(i)
                return

    def _on_edit_region_from_table(self, row, col):
        """双击表格行编辑区域"""
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or row >= len(scene.regions):
            return
        old_def = scene.regions[row]
        new_def = self._show_region_edit_dialog(old_def)
        if new_def is None:
            return
        try:
            registry.update_region_in_scene(self._scene_key, old_def.key, new_def)
        except ValueError as e:
            QMessageBox.warning(self, "更新失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_region_table_context_menu(self, pos):
        """区域表格右击菜单：Key 列右击自动复制"""
        item = self._region_table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        col = item.column()
        # 只在 Key 列（列 1）触发复制
        if col != 1:
            return
        key_item = self._region_table.item(row, 1)
        if key_item and key_item.text():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(key_item.text())

    # ─── region CRUD ─────────────────────────────────────

    def _on_new_region(self):
        """创建新区域定义"""
        region_def = self._show_region_edit_dialog(None)
        if region_def is None:
            return
        registry = get_registry()
        try:
            registry.add_region_to_scene(self._scene_key, region_def)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_delete_region(self):
        """删除区域定义"""
        row = self._region_table.currentRow()
        if row < 0:
            return
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or row >= len(scene.regions):
            return
        region_def = scene.regions[row]
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要从场景定义中删除区域「{region_def.name}」({region_def.key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            registry.remove_region_from_scene(self._scene_key, region_def.key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    # ─── 编辑弹窗 ────────────────────────────────────────

    def _show_region_edit_dialog(self, region_def: RegionDef | None) -> RegionDef | None:
        """弹窗编辑区域属性，返回新的 RegionDef 或 None（取消）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("新建区域" if region_def is None else "编辑区域")
        form = QFormLayout(dialog)

        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 my_region")
        if region_def:
            key_edit.setText(region_def.key)
            key_edit.setReadOnly(True)
        form.addRow("Key:", key_edit)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称")
        if region_def:
            name_edit.setText(region_def.name)
        form.addRow("名称:", name_edit)

        type_combo = QComboBox()
        type_combo.addItems(sorted(VALID_REGION_TYPES))
        if region_def:
            type_combo.setCurrentText(region_def.type)
        form.addRow("类型:", type_combo)

        is_text_check = QCheckBox("含文本")
        if region_def:
            is_text_check.setChecked(region_def.is_text)
        else:
            is_text_check.setChecked(True)
        form.addRow(is_text_check)

        is_clickable_check = QCheckBox("可点击")
        if region_def:
            is_clickable_check.setChecked(region_def.is_clickable)
        form.addRow(is_clickable_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验：key 和 name 非空才启用 OK
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        def _validate():
            ok_btn.setEnabled(bool(key_edit.text().strip() and name_edit.text().strip()))
        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return RegionDef(
            key=key_edit.text().strip(),
            name=name_edit.text().strip(),
            type=type_combo.currentText(),
            is_text=is_text_check.isChecked(),
            is_clickable=is_clickable_check.isChecked(),
        )
