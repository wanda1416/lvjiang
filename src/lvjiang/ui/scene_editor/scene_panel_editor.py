"""网格编辑混入类 - Panel（兼容存储名）列表构建、刷新、CRUD、编辑弹窗"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.layout_manager import rename_item_key_across_all_layouts
from ...core.scene_definition import PanelDef
from ...core.scene_registry import (
    get_registry,
    is_view_visible,
    sync_scene_cache,
)
from ...i18n import tr
from ..button_styles import apply_button_style, apply_dialog_button_box_style
from ..widgets import centered_cell_widget, strip_focus_rect
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
        self._panel_table = QTableWidget()
        self._panel_table.setColumnCount(6)
        self._panel_table.setHorizontalHeaderLabels(
            [tr("名称"), "Key", tr("比例"), tr("校准模式"), tr("滚动方向"), tr("禁用")]
        )
        # 列宽：名称/Key 自适应内容，其余固定窄宽
        header = self._panel_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for col in (2, 3, 4, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 40)   # 行可见比例
        header.resizeSection(3, 60)   # 校准模式
        header.resizeSection(4, 60)   # 滚动方向
        header.resizeSection(5, 50)   # 禁用
        self._panel_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self._panel_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self._panel_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
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
        self._btn_new_panel = QPushButton(tr("+ 创建网格"))
        self._btn_new_panel.setToolTip(tr("在场景 YAML 中新增 Panel 定义（声明式网格）"))
        self._btn_new_panel.clicked.connect(self._on_new_panel_def)
        btn_row.addWidget(self._btn_new_panel)
        self._btn_del_panel = QPushButton(tr("删除网格"))
        self._btn_del_panel.setToolTip(tr("从场景 YAML 中删除 Panel 定义"))
        self._btn_del_panel.clicked.connect(self._on_delete_panel_def)
        self._btn_del_panel.setEnabled(False)
        btn_row.addWidget(self._btn_del_panel)
        self._btn_bind_panel = QPushButton(tr("绑定网格"))
        self._btn_bind_panel.setToolTip(
            tr("在画布上框选一个矩形区域，绑定到选中的 Panel 定义")
        )
        self._btn_bind_panel.clicked.connect(self._on_bind_panel)
        btn_row.addWidget(self._btn_bind_panel)
        apply_button_style(self._btn_new_panel)
        apply_button_style(self._btn_bind_panel, variant="neutral")
        apply_button_style(self._btn_del_panel, variant="danger")
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
            # Key
            key_item = QTableWidgetItem(panel_def.key)
            if panel_def.key not in bound_keys:
                key_item.setForeground(Qt.GlobalColor.gray)
            self._panel_table.setItem(row, 1, key_item)
            # 行最小可见比例
            vis_item = QTableWidgetItem(f"{panel_def.min_visible:.2f}")
            vis_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_table.setItem(row, 2, vis_item)
            # 校准模式
            cal_item = QTableWidgetItem(self._CALIBRATION_LABELS.get(panel_def.calibration, panel_def.calibration))
            cal_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_table.setItem(row, 3, cal_item)
            # 滚动方向
            sd_item = QTableWidgetItem(self._SCROLL_LABELS.get(panel_def.scroll_direction, panel_def.scroll_direction))
            sd_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._panel_table.setItem(row, 4, sd_item)
            # 禁用复选框
            disabled_keys = self._canvas.get_disabled_keys("panel")
            cb = QCheckBox()
            cb.setChecked(panel_def.key in disabled_keys)
            cb.stateChanged.connect(
                lambda state, k=panel_def.key: self._on_toggle_panel_disabled(k, "panel", state)
            )
            self._panel_table.setCellWidget(row, 5, centered_cell_widget(cb))
        self._panel_table.blockSignals(False)

    def _on_toggle_panel_disabled(self, key: str, kind: str, state: int):
        """切换某 key 的禁用状态，通过画布回调通知 dialog 标记 dirty"""
        self._canvas.set_item_disabled(kind, key, bool(state))

    # ─── 事件处理 ────────────────────────────────────────

    def _on_panel_table_selection(self, row, col, prev_row, prev_col):
        """表格行选中时更新删除按钮状态 + 同步画布选中"""
        self._btn_del_panel.setEnabled(row >= 0)
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
        """双击表格行编辑面板属性（场景变更时跨场景迁移）"""
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        key_item = self._panel_table.item(row, 1)
        if not scene or key_item is None:
            return
        old_def = next((p for p in scene.panels if p.key == key_item.text()), None)
        if old_def is None:
            return
        result = self._show_panel_edit_dialog(old_def)
        if result is None:
            return
        new_def, target_scene, new_rows, new_cols = result
        old_key = old_def.key
        new_key = new_def.key
        key_changed = new_key != old_key

        if target_scene != self._scene_key:
            # 跨场景迁移：目标场景视图体系不同，归属视图重置为基底
            new_def.view = ""
            # 先加到目标场景（key 冲突则中止，YAML 未动），再从当前场景移除
            try:
                registry.add_panel_to_scene(target_scene, new_def)
            except ValueError as e:
                QMessageBox.warning(self, tr("迁移失败"), str(e))
                return
            registry.remove_panel_from_scene(self._scene_key, old_key)
            sync_scene_cache(self._scene_key)
            sync_scene_cache(target_scene)
            if self.on_item_migrated:
                self.on_item_migrated("panel", new_key, self._scene_key, target_scene)
            # 更新已绑定 Panel 的 rows/cols（布局级配置）
            panels = self._canvas.get_panels()
            for p in panels:
                if p.key == old_key:
                    p.key = new_key
                    p.rows, p.cols = new_rows, new_cols
            self._canvas.set_panels(panels)
            self._refresh_lists()
            return

        try:
            if key_changed:
                # key 变更：更新场景定义 + 所有布局
                registry.rename_panel_key(self._scene_key, old_key, new_key)
                rename_item_key_across_all_layouts(self._scene_key, "panel", old_key, new_key)
                # 更新其他属性
                registry.update_panel_in_scene(self._scene_key, new_key, new_def)
                # 同步画布数据中的 key 和布局级配置
                panels = self._canvas.get_panels()
                for p in panels:
                    if p.key == old_key:
                        p.key = new_key
                        p.rows, p.cols = new_rows, new_cols
                        p.min_visible = new_def.min_visible
                        p.calibration = new_def.calibration
                        p.scroll_direction = new_def.scroll_direction
                self._canvas.set_panels(panels)
                self._canvas._notify_panel_changed()
            else:
                # key 不变：更新其他属性 + 布局级 rows/cols
                registry.update_panel_in_scene(self._scene_key, old_key, new_def)
                # 同步到已绑定的布局 Panel
                panels = self._canvas.get_panels()
                changed = False
                for p in panels:
                    if p.key == old_key:
                        p.rows, p.cols = new_rows, new_cols
                        p.min_visible = new_def.min_visible
                        p.calibration = new_def.calibration
                        p.scroll_direction = new_def.scroll_direction
                        changed = True
                if changed:
                    self._canvas.set_panels(panels)
                    self._canvas._notify_panel_changed()
        except ValueError as e:
            QMessageBox.warning(self, tr("更新失败"), str(e))
            return
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
        result = self._show_panel_edit_dialog(None)
        if result is None:
            return
        panel_def, _ = result
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
            f"确定要从场景定义中删除面板「{panel_def.name}」({panel_def.key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            registry.remove_panel_from_scene(self._scene_key, panel_def.key)
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_bind_panel(self):
        """绑定面板：选中一个未绑定的 Panel 定义，进入画布框选模式"""
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene or not scene.panels:
            QMessageBox.information(
                self, tr("无可用面板"), tr("该场景尚未定义任何 Panel，请先创建面板定义。")
            )
            return
        bound_keys = {p.key for p in self._canvas.get_panels()}
        available = [p for p in scene.panels if p.key not in bound_keys]
        if not available:
            QMessageBox.information(
                self, tr("无可用面板"), tr("该场景所有 Panel 定义都已绑定。")
            )
            return
        # 弹出选择对话框
        items = [f"{p.name} ({p.key})" for p in available]
        from PyQt6.QtWidgets import QInputDialog
        dlg = QInputDialog(self)
        dlg.setWindowTitle(tr("绑定面板"))
        dlg.setLabelText(tr("选择要绑定的 Panel："))
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
    ) -> tuple[PanelDef, str, int, int] | None:
        """弹窗编辑面板属性，返回 (新 PanelDef, 目标场景 key, rows, cols) 或 None（取消）

        仅编辑模式提供场景下拉框；新建时目标场景恒为当前场景。
        rows/cols 属于布局级配置，从已绑定的 Panel 读取，保存时写回布局。
        """
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(tr("新建面板") if panel_def is None else tr("编辑面板"))
        form = QFormLayout(dialog)

        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("英文，如 bag_grid"))
        if panel_def:
            key_edit.setText(panel_def.key)
            # 允许编辑 key，但需要校验唯一性
        form.addRow("Key:", key_edit)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("中文名称，如 背包网格"))
        if panel_def:
            name_edit.setText(panel_def.name)
        form.addRow(tr("名称:"), name_edit)

        # 行数和列数（布局级配置，从已绑定的 Panel 读取）
        cols_spin = QSpinBox()
        cols_spin.setRange(1, 20)
        rows_spin = QSpinBox()
        rows_spin.setRange(1, 20)
        # 尝试从已绑定的 Panel 读取 rows/cols
        bound_panel = self._find_bound_panel(panel_def.key) if panel_def else None
        if bound_panel:
            cols_spin.setValue(bound_panel.cols)
            rows_spin.setValue(bound_panel.rows)
        else:
            cols_spin.setValue(6)
            rows_spin.setValue(3)
        form.addRow(tr("列数:"), cols_spin)
        form.addRow(tr("行数:"), rows_spin)

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
        form.addRow(tr("行最小可见比例:"), vis_spin)

        # 校准模式下拉
        calibration_combo = QComboBox()
        calibration_combo.addItem(tr("自动模式"), "auto")
        calibration_combo.addItem(tr("等分网格"), "even")
        calibration_combo.addItem(tr("图像检测"), "image")
        calibration_combo.setToolTip(
            "自动模式: 先图像检测，失败降级为等分\n"
            "等分网格: 跳过图像检测，直接按行列数等分\n"
            "图像检测: 仅图像检测，失败返回 None"
        )
        if panel_def:
            idx = calibration_combo.findData(panel_def.calibration)
            if idx >= 0:
                calibration_combo.setCurrentIndex(idx)
        form.addRow(tr("校准模式:"), calibration_combo)

        # 滚动方向下拉
        scroll_combo = QComboBox()
        scroll_combo.addItem(tr("纵向滚动"), "vertical")
        scroll_combo.addItem(tr("横向滚动"), "horizontal")
        scroll_combo.addItem(tr("双向滚动"), "both")
        scroll_combo.addItem(tr("固定网格"), "none")
        scroll_combo.setToolTip(
            "纵向滚动: rows 允许 expected-1\n"
            "横向滚动: cols 允许 expected-1\n"
            "双向滚动: rows/cols 都允许 expected-1\n"
            "固定网格: rows/cols 必须精确匹配\n\n"
            "约束：rows=1 时禁止纵向/双向，cols=1 时禁止横向/双向"
        )
        if panel_def:
            idx = scroll_combo.findData(panel_def.scroll_direction)
            if idx >= 0:
                scroll_combo.setCurrentIndex(idx)
        form.addRow(tr("滚动方向:"), scroll_combo)

        # 仅编辑模式可选择归属场景（跨场景迁移）
        scene_combo = None
        if panel_def is not None:
            scene_combo = add_scene_combo_row(form, self._scene_key)

        # 多视图场景可选择归属视图；新建默认落在当前视图
        view_list = add_views_checklist_row(
            form, self._scene_key,
            list(panel_def.views) if panel_def else [self._current_view],
        )

        # 场景切换时同步更新视图下拉框
        if scene_combo is not None:
            connect_scene_views_sync(scene_combo, view_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        apply_dialog_button_box_style(buttons)
        form.addRow(buttons)

        # 实时校验
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        scene = get_registry().get_scene(self._scene_key)
        # region/point/panel 共享命名空间
        existing = set()
        if scene:
            existing = {r.key for r in scene.regions} | {p.key for p in scene.points} | {p.key for p in scene.panels}
        # 编辑模式下，排除自身的 key（允许保持不变）
        old_key = panel_def.key if panel_def else None

        def _validate():
            k = key_edit.text().strip()
            # 检查 key 是否被占用（新建时检查全部，编辑时排除自身）
            if k in existing and k != old_key:
                ok_btn.setEnabled(False)
                return
            ok_btn.setEnabled(bool(k and name_edit.text().strip()))

        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        _validate()

        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        target_scene = (
            scene_combo.currentData() if scene_combo is not None else self._scene_key
        )
        return PanelDef(
            key=key_edit.text().strip(),
            name=name_edit.text().strip(),
            min_visible=round(vis_spin.value(), 2),
            views=checklist_views_value(view_list, self._current_view),
            calibration=calibration_combo.currentData(),
            scroll_direction=scroll_combo.currentData(),
        ), target_scene, rows_spin.value(), cols_spin.value()
