"""网格编辑混入类 - Panel（兼容存储名）列表构建、刷新、CRUD、编辑弹窗"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_manager import (
    delete_item_key_across_all_layouts,
    rename_item_key_across_all_layouts,
    shared_layout_bindings,
)
from ...core.scene_definition import PanelDef
from ...core.scene_registry import (
    get_registry,
    is_view_visible,
    sync_scene_cache,
)
from ...i18n import tr
from ..button_styles import apply_button_style, apply_dialog_button_box_style
from ..widgets import centered_cell_widget, strip_focus_rect
from .entity_order_table import EntityOrderTable
from .panel_binding_form import PanelBindingConfig, PanelBindingForm
from .scene_select import (
    add_scene_combo_row,
    add_views_checklist_row,
    checklist_views_value,
    connect_scene_views_sync,
)


class PanelEditorMixin:
    """面板编辑混入类

    依赖主类提供:
        _scene_key, _canvas, _panel_table,
        _btn_del_panel, _refresh_lists(), on_item_migrated
    """

    _CALIBRATION_LABELS = {"auto": tr("自动模式"), "even": tr("等分网格"), "image": tr("图像检测")}
    _SCROLL_LABELS = {"vertical": tr("纵向滚动"), "horizontal": tr("横向滚动"), "both": tr("双向滚动"), "none": tr("固定网格")}

    # ─── 面板构建 ────────────────────────────────────────

    def _build_panel_panel(self) -> QWidget:
        """构建面板编辑 Tab 的 UI"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._panel_table = EntityOrderTable()
        self._panel_table.setColumnCount(6)
        self._panel_table.setHorizontalHeaderLabels(
            [tr("场景定义"), "Key", tr("布局绑定"), tr("行 × 列"), tr("校准模式"), tr("布局禁用")]
        )
        # 列宽：名称/Key 自适应内容，其余固定窄宽
        header = self._panel_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 75)   # 当前布局绑定状态
        header.resizeSection(3, 65)   # 行列数
        header.resizeSection(4, 80)   # 校准模式
        header.resizeSection(5, 75)   # 布局禁用
        self._panel_table.setSelectionBehavior(
            EntityOrderTable.SelectionBehavior.SelectRows
        )
        self._panel_table.setSelectionMode(
            EntityOrderTable.SelectionMode.SingleSelection
        )
        self._panel_table.setEditTriggers(
            EntityOrderTable.EditTrigger.NoEditTriggers
        )
        self._panel_table.setToolTip(tr("拖拽名称可调整 YAML 定义顺序"))
        self._panel_table.entity_order_changed.connect(
            lambda keys, moved_key: self._on_entity_order_changed(
                "panels", keys, self._panel_table, moved_key))
        strip_focus_rect(self._panel_table)
        vheader = self._panel_table.verticalHeader()
        assert vheader is not None
        vheader.setVisible(False)
        self._panel_table.currentCellChanged.connect(self._on_panel_table_selection)
        self._panel_table.cellDoubleClicked.connect(self._on_edit_panel_from_table)
        self._panel_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._panel_table.customContextMenuRequested.connect(
            self._on_panel_table_context_menu
        )
        layout.addWidget(self._panel_table)

        btn_row = QHBoxLayout()
        self._btn_new_panel = QPushButton(tr("+ 创建网格定义"))
        self._btn_new_panel.setToolTip(tr("在场景 YAML 中新增 Panel 定义（声明式网格）"))
        self._btn_new_panel.clicked.connect(self._on_new_panel_def)
        btn_row.addWidget(self._btn_new_panel)
        self._btn_del_panel = QPushButton(tr("删除网格定义"))
        self._btn_del_panel.setToolTip(tr("从场景 YAML 中删除 Panel 定义"))
        self._btn_del_panel.clicked.connect(self._on_delete_panel_def)
        self._btn_del_panel.setEnabled(False)
        btn_row.addWidget(self._btn_del_panel)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        btn_row = QHBoxLayout()
        self._btn_bind_panel = QPushButton(tr("绑定到当前布局"))
        self._btn_bind_panel.setToolTip(
            tr("在画布上框选一个矩形区域，绑定到选中的 Panel 定义")
        )
        self._btn_bind_panel.clicked.connect(self._on_bind_panel)
        btn_row.addWidget(self._btn_bind_panel)
        apply_button_style(self._btn_new_panel)
        apply_button_style(self._btn_bind_panel, variant="neutral")
        apply_button_style(self._btn_del_panel, variant="danger")
        self._btn_unbind_panel = QPushButton(tr("解除当前布局绑定"))
        self._btn_unbind_panel.clicked.connect(self._on_unbind_panel)
        apply_button_style(self._btn_unbind_panel, variant="neutral")
        btn_row.addWidget(self._btn_unbind_panel)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    # ─── 列表刷新 ────────────────────────────────────────

    def _refresh_panel_list(self):
        """刷新面板列表，显示 YAML 定义 + 布局绑定状态"""
        current = self._panel_table.item(self._panel_table.currentRow(), 1)
        selected_key = self._canvas.selected_panel_key() or (
            current.text() if current is not None else None)
        self._panel_table.blockSignals(True)
        self._panel_table.setRowCount(0)
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene:
            self._panel_table.blockSignals(False)
            return
        bound = {p.key: p for p in self._canvas.get_panels()}
        bound_keys = {key for key, p in bound.items() if p.w_ratio > 0 and p.h_ratio > 0}
        for panel_def in scene.panels:
            if not is_view_visible(panel_def.views, self._current_view):
                continue
            row = self._panel_table.rowCount()
            self._panel_table.insertRow(row)
            # 名称 + 绑定状态
            status = "\u2713" if panel_def.key in bound_keys else "\u25cb"
            name_item = QTableWidgetItem(f"{status} {panel_def.name}")
            if panel_def.key not in bound_keys:
                name_item.setForeground(Qt.GlobalColor.gray)
            self._panel_table.setItem(row, 0, name_item)
            self._panel_table.set_entity_order_key(row, panel_def.key)
            # Key
            key_item = QTableWidgetItem(panel_def.key)
            if panel_def.key not in bound_keys:
                key_item.setForeground(Qt.GlobalColor.gray)
            self._panel_table.setItem(row, 1, key_item)
            panel = bound.get(panel_def.key)
            placed = panel_def.key in bound_keys
            self._panel_table.setItem(row, 2, QTableWidgetItem(
                tr("已绑定") if placed else tr("未绑定")))
            self._panel_table.setItem(row, 3, QTableWidgetItem(
                f"{panel.rows} × {panel.cols}" if placed else "—"))
            self._panel_table.setItem(row, 4, QTableWidgetItem(
                self._CALIBRATION_LABELS.get(panel.calibration, panel.calibration)
                if placed else "—"))
            # 禁用复选框
            disabled_keys = self._canvas.get_disabled_keys("panel")
            cb = QCheckBox()
            cb.setChecked(panel_def.key in disabled_keys)
            cb.stateChanged.connect(
                lambda state, k=panel_def.key: self._on_toggle_panel_disabled(k, "panel", state)
            )
            self._panel_table.setCellWidget(row, 5, centered_cell_widget(cb))
        for row in range(self._panel_table.rowCount()):
            if self._panel_table.item(row, 1).text() == selected_key:
                self._panel_table.selectRow(row)
                break
        self._panel_table.blockSignals(False)
        self._btn_del_panel.setEnabled(self._panel_table.currentRow() >= 0)
        self._btn_unbind_panel.setEnabled(selected_key in bound)

    def _on_toggle_panel_disabled(self, key: str, kind: str, state: int):
        """切换某 key 的禁用状态，通过画布回调通知 dialog 标记 dirty"""
        self._canvas.set_item_disabled(kind, key, bool(state))

    # ─── 事件处理 ────────────────────────────────────────

    def _on_panel_table_selection(self, row, col, prev_row, prev_col):
        """表格行选中时更新删除按钮状态 + 同步画布选中"""
        self._btn_del_panel.setEnabled(row >= 0)
        self._btn_unbind_panel.setEnabled(row >= 0)
        if row < 0:
            return
        # 表格已按视图过滤，row 不再对应 scene.panels 索引，改按 key
        key_item = self._panel_table.item(row, 1)
        if key_item is None:
            return
        self._canvas.select_panel_by_key(key_item.text())

    def _find_bound_panel(self, panel_key: str):
        """查找已绑定的 Panel（布局级）"""
        for p in self._canvas.get_panels():
            if p.key == panel_key:
                return p
        return None

    def _on_edit_panel_from_table(self, row, col):
        key_item = self._panel_table.item(row, 1)
        if key_item is not None:
            self._show_panel_properties(key_item.text(), layout_first=col >= 2)

    def _selected_panel_key(self) -> str | None:
        item = self._panel_table.item(self._panel_table.currentRow(), 1)
        return item.text() if item is not None else None

    def _save_panel_definition(self, old_def: PanelDef, new_def: PanelDef,
                               target_scene: str) -> None:
        """定义立即保存；绑定的物理参数始终原样保留。"""
        registry = get_registry()
        old_key = old_def.key
        new_key = new_def.key
        if target_scene != self._scene_key:
            # 先验证目标命名空间，再修改源定义。
            target = registry.get_scene(target_scene)
            if target is None:
                raise ValueError(tr("目标场景不存在"))
            registry._check_key_unique(target, new_key)
            registry.validate_reference_retarget(
                self._scene_key, target_scene, old_key, new_key)
        if new_key != old_key:
            registry.rename_panel_key(self._scene_key, old_key, new_key)
            rename_item_key_across_all_layouts(self._scene_key, "panel", old_key, new_key)
            panels = self._canvas.get_panels()
            for panel in panels:
                if panel.key == old_key:
                    panel.key = new_key
            self._canvas.set_panels(panels)
            self._canvas._notify_panel_changed()
        if target_scene != self._scene_key:
            registry.add_panel_to_scene(target_scene, new_def)
            registry.retarget_references(self._scene_key, target_scene, new_key, new_key)
            registry.remove_panel_from_scene(self._scene_key, new_key)
            sync_scene_cache(target_scene)
            if self.on_item_migrated:
                self.on_item_migrated("panel", new_key, self._scene_key, target_scene)
        else:
            registry.update_panel_in_scene(self._scene_key, new_key, new_def)
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _update_panel_binding(self, key: str, config: PanelBindingConfig) -> None:
        panels = self._canvas.get_panels()
        for index, panel in enumerate(panels):
            if panel.key == key:
                updated = config.create_panel(
                    key, panel.x_ratio, panel.y_ratio, panel.w_ratio, panel.h_ratio)
                if updated != panel:
                    panels[index] = updated
                    self._canvas.set_panels(panels)
                    self._canvas._notify_panel_changed()
                return
        raise ValueError(tr("当前布局尚未绑定此网格"))

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
        result = self._show_panel_definition_dialog(None)
        if result is None:
            return
        panel_def, _target_scene = result
        registry = get_registry()
        try:
            registry.add_panel_to_scene(self._scene_key, panel_def)
        except ValueError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
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
        key_item = self._panel_table.item(row, 1)
        if not scene or key_item is None:
            return
        panel_def = next((p for p in scene.panels if p.key == key_item.text()), None)
        if panel_def is None:
            return
        reply = QMessageBox.question(
            self,
            tr("确认删除"),
            f"删除网格定义「{panel_def.name}」({panel_def.key})？\n"
            "将立即删除定义及所有布局中的绑定，不能通过放弃布局修改撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            registry.remove_panel_from_scene(self._scene_key, panel_def.key)
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        self._canvas.remove_item("panel", panel_def.key)
        delete_item_key_across_all_layouts(
            self._scene_key, "panel", panel_def.key)
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_bind_panel(self):
        key = self._selected_panel_key()
        if key is None:
            QMessageBox.information(self, tr("绑定网格"), tr("请先选择一个网格定义。"))
            return
        self._bind_panel_key(key)

    def _bind_panel_key(self, key: str):
        scene = get_registry().get_scene(self._scene_key)
        definition = next((p for p in scene.panels if p.key == key), None) if scene else None
        if definition is None:
            return
        panel = self._find_bound_panel(key)
        if panel is not None and panel.w_ratio > 0 and panel.h_ratio > 0:
            self._show_panel_properties(key, layout_first=True)
            return
        if not self._layout_name:
            QMessageBox.information(self, tr("绑定网格"), tr("请先选择布局。"))  # type: ignore[arg-type]
            return
        if self._canvas.get_image() is None:
            QMessageBox.information(self, tr("绑定网格"), tr("请先导入或刷新当前场景截图。"))  # type: ignore[arg-type]
            return
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(tr("绑定到当前布局"))
        outer = QVBoxLayout(dialog)
        outer.addWidget(QLabel(self._panel_layout_scope()))
        outer.addWidget(QLabel(f"{definition.name} ({key})"))
        binding = PanelBindingForm(panel, dialog)
        outer.addWidget(binding)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        next_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert next_button is not None
        next_button.setText(tr("下一步：框选区域"))
        apply_dialog_button_box_style(buttons)
        buttons.accepted.connect(lambda: dialog.accept() if binding.validate() else None)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._canvas.begin_place_panel(definition, binding.value())

    def _on_unbind_panel(self):
        key = self._selected_panel_key()
        if key is not None:
            self._canvas.remove_item("panel", key)

    def _panel_layout_scope(self) -> str:
        scope = f"当前布局：{self._layout_name or '未选择'}；修改后需保存布局。"
        # 根布局和别名的两个编辑入口都要说明共用影响范围。
        shared = shared_layout_bindings(self._layout_name) if self._layout_name else []
        if len(shared) > 1:
            scope += "\n共用此绑定的布局：" + "、".join(shared)
        return scope

    def _definition_form(self, parent, panel_def):
        page = QWidget(parent)
        form = QFormLayout(page)
        form.addRow(QLabel(tr("场景定义：所有布局共用；保存后立即生效。")))
        key_edit = QLineEdit(panel_def.key if panel_def else "")
        name_edit = QLineEdit(panel_def.name if panel_def else "")
        form.addRow("Key:", key_edit)
        form.addRow(tr("名称:"), name_edit)
        scene_combo = add_scene_combo_row(form, self._scene_key) if panel_def else None
        views = add_views_checklist_row(
            form, self._scene_key,
            list(panel_def.views) if panel_def else [self._current_view])
        if scene_combo is not None:
            connect_scene_views_sync(scene_combo, views)
        error = QLabel()
        error.setStyleSheet("color: #c62828;")
        form.addRow(error)

        def result():
            import re
            key = key_edit.text().strip()
            name = name_edit.text().strip()
            target = scene_combo.currentData() if scene_combo else self._scene_key
            if not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not name:
                error.setText(tr("请填写名称；Key 以小写字母开头，仅含小写字母、数字、下划线。"))
                return None
            registry = get_registry()
            scene = registry.get_scene(target)
            try:
                if scene is None:
                    raise ValueError(tr("目标场景不存在"))
                if panel_def and target == self._scene_key:
                    registry._check_key_unique_excluding(scene, key, panel_def.key)
                else:
                    registry._check_key_unique(scene, key)
            except ValueError as exc:
                error.setText(str(exc))
                return None
            error.clear()
            return PanelDef(key=key, name=name,
                            views=checklist_views_value(views, "")), target

        return page, result

    def _show_panel_definition_dialog(self, panel_def):
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(tr("创建网格定义"))
        outer = QVBoxLayout(dialog)
        page, get_result = self._definition_form(dialog, panel_def)
        outer.addWidget(page)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("创建定义"))
        apply_dialog_button_box_style(buttons)
        result = None

        def accept():
            nonlocal result
            result = get_result()
            if result is not None:
                dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        return result if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _show_panel_properties(self, key: str, layout_first: bool = False):
        scene = get_registry().get_scene(self._scene_key)
        definition = next((p for p in scene.panels if p.key == key), None) if scene else None
        if definition is None:
            return
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(f"网格属性 — {definition.name}")
        outer = QVBoxLayout(dialog)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        page, get_definition = self._definition_form(dialog, definition)
        tabs.addTab(page, tr("场景定义 · 所有布局"))
        binding_page = QWidget()
        binding_layout = QVBoxLayout(binding_page)
        binding_layout.addWidget(QLabel(self._panel_layout_scope()))
        bound = self._find_bound_panel(key)
        placed = bound is not None and bound.w_ratio > 0 and bound.h_ratio > 0
        binding = PanelBindingForm(bound, dialog) if placed else None
        if binding is not None:
            binding_layout.addWidget(binding)
        else:
            binding_layout.addWidget(QLabel(tr("当前布局尚未绑定此网格。")))
        tabs.addTab(binding_page, tr("当前布局绑定"))
        tabs.setCurrentIndex(1 if layout_first else 0)
        note = QLabel(tr("每次只提交当前页。关闭窗口会放弃未提交的表单内容。"))
        outer.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        assert close_button is not None
        close_button.setText(tr("关闭"))
        apply_dialog_button_box_style(buttons)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)

        def update_label():
            save.setText(tr("保存场景定义") if tabs.currentIndex() == 0 else
                         tr("应用到当前布局") if placed else tr("绑定到当前布局"))
            save.setEnabled(tabs.currentIndex() == 0 or bool(self._layout_name))

        tabs.currentChanged.connect(update_label)
        update_label()

        def submit():
            try:
                if tabs.currentIndex() == 0:
                    result = get_definition()
                    if result is None:
                        return
                    new_def, target = result
                    if new_def != definition or target != self._scene_key:
                        self._save_panel_definition(definition, new_def, target)
                elif binding is not None:
                    if not binding.validate():
                        return
                    self._update_panel_binding(key, binding.value())
                else:
                    dialog.accept()
                    self._bind_panel_key(key)
                    return
            except ValueError as exc:
                QMessageBox.warning(dialog, tr("保存失败"), str(exc))
                return
            dialog.accept()

        buttons.accepted.connect(submit)
        buttons.rejected.connect(dialog.reject)
        outer.addWidget(buttons)
        dialog.exec()
