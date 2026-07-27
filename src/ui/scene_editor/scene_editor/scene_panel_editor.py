"""面板编辑混入类 - Panel 列表构建、刷新、CRUD、编辑弹窗"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QMessageBox,
)
from PyQt6.QtCore import Qt

from ....core.scene_registry import (
    get_registry, sync_scene_cache, Panel,
)
from ....core.scene_loader import PanelDef


class PanelEditorMixin:
    """面板编辑混入类

    依赖主类提供:
        _scene_key, _canvas, _panel_table,
        _btn_del_panel, _refresh_lists()
    """

    # ─── 面板构建 ────────────────────────────────────────

    def _build_panel_panel(self) -> QWidget:
        """构建面板编辑 Tab 的 UI"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._panel_table = QTableWidget()
        self._panel_table.setColumnCount(5)
        self._panel_table.setHorizontalHeaderLabels(
            ["名称", "Key", "行数", "列数", "行可见比例"]
        )
        # 列宽：名称/Key 自适应内容，行数/列数/可见比例固定窄宽
        header = self._panel_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 50)
        header.resizeSection(3, 50)
        header.resizeSection(4, 80)
        self._panel_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._panel_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self._panel_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self._panel_table.verticalHeader().setVisible(False)
        self._panel_table.currentCellChanged.connect(self._on_panel_table_selection)
        self._panel_table.cellDoubleClicked.connect(self._on_edit_panel_from_table)
        self._panel_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._panel_table.customContextMenuRequested.connect(
            self._on_panel_table_context_menu
        )
        layout.addWidget(self._panel_table)

        btn_row = QHBoxLayout()
        self._btn_new_panel = QPushButton("+ 创建面板")
        self._btn_new_panel.setToolTip("在场景 YAML 中新增 Panel 定义（声明式网格）")
        self._btn_new_panel.clicked.connect(self._on_new_panel_def)
        btn_row.addWidget(self._btn_new_panel)
        self._btn_del_panel = QPushButton("删除面板")
        self._btn_del_panel.setToolTip("从场景 YAML 中删除 Panel 定义")
        self._btn_del_panel.clicked.connect(self._on_delete_panel_def)
        self._btn_del_panel.setEnabled(False)
        btn_row.addWidget(self._btn_del_panel)
        self._btn_bind_panel = QPushButton("绑定面板")
        self._btn_bind_panel.setToolTip(
            "在画布上框选一个矩形区域，绑定到选中的 Panel 定义"
        )
        self._btn_bind_panel.clicked.connect(self._on_bind_panel)
        btn_row.addWidget(self._btn_bind_panel)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    # ─── 列表刷新 ────────────────────────────────────────

    def _refresh_panel_list(self):
        """刷新面板列表，显示 YAML 定义 + 布局绑定状态"""
        self._panel_table.blockSignals(True)
        self._panel_table.setRowCount(0)
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene:
            self._panel_table.blockSignals(False)
            return
        bound_keys = {p.key for p in self._canvas.get_panels()}
        for panel_def in scene.panels:
            row = self._panel_table.rowCount()
            self._panel_table.insertRow(row)
            # 名称 + 绑定状态
            status = "\u2713" if panel_def.key in bound_keys else "\u25cb"
            name_item = QTableWidgetItem(f"{status} {panel_def.name}")
            if panel_def.key not in bound_keys:
                name_item.setForeground(Qt.GlobalColor.gray)
            self._panel_table.setItem(row, 0, name_item)
            # Key
            key_item = QTableWidgetItem(panel_def.key)
            if panel_def.key not in bound_keys:
                key_item.setForeground(Qt.GlobalColor.gray)
            self._panel_table.setItem(row, 1, key_item)
            # 行数
            rows_item = QTableWidgetItem(str(panel_def.rows))
            rows_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_table.setItem(row, 2, rows_item)
            # 列数
            cols_item = QTableWidgetItem(str(panel_def.cols))
            cols_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_table.setItem(row, 3, cols_item)
            # 行最小可见比例
            vis_item = QTableWidgetItem(f"{panel_def.min_visible:.2f}")
            vis_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_table.setItem(row, 4, vis_item)
        self._panel_table.blockSignals(False)

    # ─── 事件处理 ────────────────────────────────────────

    def _on_panel_table_selection(self, row, col, prev_row, prev_col):
        """表格行选中时更新删除按钮状态 + 同步画布选中"""
        self._btn_del_panel.setEnabled(row >= 0)
        if row < 0:
            return
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or row >= len(scene.panels):
            return
        key = scene.panels[row].key
        self._canvas.select_panel_by_key(key)

    def _on_edit_panel_from_table(self, row, col):
        """双击表格行编辑面板属性"""
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or row >= len(scene.panels):
            return
        old_def = scene.panels[row]
        new_def = self._show_panel_edit_dialog(old_def)
        if new_def is None:
            return
        try:
            registry.update_panel_in_scene(self._scene_key, old_def.key, new_def)
        except ValueError as e:
            QMessageBox.warning(self, "更新失败", str(e))
            return
        # 同步网格参数到已绑定的布局 Panel（几何不变，仅 cols/rows/min_visible），
        # 否则弹窗改动只写入场景 YAML，运行时读的布局 Panel 不会生效
        panels = self._canvas.get_panels()
        changed = False
        for p in panels:
            if p.key == old_def.key:
                p.cols, p.rows = new_def.cols, new_def.rows
                p.min_visible = new_def.min_visible
                changed = True
        if changed:
            self._canvas.set_panels(panels)
            # 布局 Panel 数据已变，通知上层标记 dirty（需保存布局才落盘）
            self._canvas._notify_panel_changed()
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_panel_table_context_menu(self, pos):
        """面板表格右击菜单：Key 列右击自动复制"""
        item = self._panel_table.itemAt(pos)
        if item is None:
            return
        row = item.row()
        col = item.column()
        if col != 1:
            return
        key_item = self._panel_table.item(row, 1)
        if key_item and key_item.text():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(key_item.text())

    # ─── Panel CRUD ──────────────────────────────────────

    def _on_new_panel_def(self):
        """创建新面板定义"""
        panel_def = self._show_panel_edit_dialog(None)
        if panel_def is None:
            return
        registry = get_registry()
        try:
            registry.add_panel_to_scene(self._scene_key, panel_def)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_delete_panel_def(self):
        """删除面板定义"""
        row = self._panel_table.currentRow()
        if row < 0:
            return
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or row >= len(scene.panels):
            return
        panel_def = scene.panels[row]
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要从场景定义中删除面板「{panel_def.name}」({panel_def.key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            registry.remove_panel_from_scene(self._scene_key, panel_def.key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_bind_panel(self):
        """绑定面板：选中一个未绑定的 Panel 定义，进入画布框选模式"""
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or not scene.panels:
            QMessageBox.information(
                self, "无可用面板", "该场景尚未定义任何 Panel，请先创建面板定义。"
            )
            return
        bound_keys = {p.key for p in self._canvas.get_panels()}
        available = [p for p in scene.panels if p.key not in bound_keys]
        if not available:
            QMessageBox.information(
                self, "无可用面板", "该场景所有 Panel 定义都已绑定。"
            )
            return
        # 弹出选择对话框
        items = [f"{p.name} ({p.key})" for p in available]
        from PyQt6.QtWidgets import QInputDialog
        dlg = QInputDialog(self)
        dlg.setWindowTitle("绑定面板")
        dlg.setLabelText("选择要绑定的 Panel：")
        dlg.setComboBoxItems(items)
        if not dlg.exec() or not dlg.textValue():
            return
        idx = items.index(dlg.textValue())
        selected_def = available[idx]
        # 进入画布框选模式
        self._canvas.begin_place_panel(selected_def)

    # ─── 编辑弹窗 ────────────────────────────────────────

    def _show_panel_edit_dialog(
        self, panel_def: PanelDef | None
    ) -> PanelDef | None:
        """弹窗编辑面板属性，返回新的 PanelDef 或 None（取消）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("新建面板" if panel_def is None else "编辑面板")
        form = QFormLayout(dialog)

        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 bag_grid")
        if panel_def:
            key_edit.setText(panel_def.key)
            key_edit.setReadOnly(True)
        form.addRow("Key:", key_edit)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称，如 背包网格")
        if panel_def:
            name_edit.setText(panel_def.name)
        form.addRow("名称:", name_edit)

        cols_spin = QSpinBox()
        cols_spin.setRange(1, 20)
        cols_spin.setValue(panel_def.cols if panel_def else 6)
        form.addRow("列数:", cols_spin)

        rows_spin = QSpinBox()
        rows_spin.setRange(1, 20)
        rows_spin.setValue(panel_def.rows if panel_def else 3)
        form.addRow("行数:", rows_spin)

        vis_spin = QDoubleSpinBox()
        vis_spin.setRange(0.50, 1.00)
        vis_spin.setSingleStep(0.05)
        vis_spin.setDecimals(2)
        vis_spin.setValue(panel_def.min_visible if panel_def else 0.95)
        vis_spin.setToolTip(
            "滚动时半截行计入有效行所需的最小可见比例：\n"
            "0.95 = 基本完整才计入；调低（如 0.55）可减少少检一行，\n"
            "但必须 > 0.5，否则行中心可能落在面板外导致点击脱靶"
        )
        form.addRow("行最小可见比例:", vis_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)

        def _validate():
            ok_btn.setEnabled(
                bool(key_edit.text().strip() and name_edit.text().strip())
            )

        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        return PanelDef(
            key=key_edit.text().strip(),
            name=name_edit.text().strip(),
            cols=cols_spin.value(),
            rows=rows_spin.value(),
            min_visible=round(vis_spin.value(), 2),
        )
