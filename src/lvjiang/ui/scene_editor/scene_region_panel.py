"""区域面板混入类 - 区域列表构建、刷新、CRUD、编辑弹窗"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.key_names import normalize_key
from ...core.layout_manager import rename_item_key_across_all_layouts
from ...core.scene_definition import VALID_REGION_TYPES, RegionDef
from ...core.scene_definition_models import SceneRefDef
from ...core.scene_registry import (
    get_region_def,
    get_registry,
    get_scene_name,
    is_view_visible,
    sync_scene_cache,
)
from ...i18n import tr
from ..button_styles import apply_button_style, apply_dialog_button_box_style
from ..widgets import centered_cell_widget, strip_focus_rect
from .entity_edit_form import (
    add_activation_key_row,
    add_attribute_row,
    add_definition_separator,
    add_dialog_action_row,
    validate_activation_key_edit,
)
from .scene_select import (
    SceneAreaReferencePicker,
    add_scene_combo_row,
    add_transition_row,
    add_views_checklist_row,
    checklist_views_value,
    connect_scene_views_sync,
)


class RegionPanelMixin:
    """区域面板混入类

    依赖主类提供:
        _scene_key, _canvas, _region_table,
        _btn_del_region, _refresh_lists(), on_item_migrated
    """

    # ─── 面板构建 ────────────────────────────────────────

    def _build_region_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._region_table = QTableWidget()
        self._region_table.setColumnCount(9)
        self._region_table.setHorizontalHeaderLabels([
            tr("名称"), "Key", tr("类型"), tr("含文本"), tr("可点击"),
            tr("按键"), tr("禁用"), tr("跳转"), tr("来源"),
        ])
        # 列宽：名称/Key/类型/按键自适应内容，布尔状态列固定窄宽
        header = self._region_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for col in (3, 4, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, 50)
        self._region_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._region_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._region_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        strip_focus_rect(self._region_table)
        vheader = self._region_table.verticalHeader()
        assert vheader is not None
        vheader.setVisible(False)
        self._region_table.currentCellChanged.connect(self._on_region_table_selection)
        self._region_table.cellDoubleClicked.connect(self._on_edit_region_from_table)
        self._region_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._region_table.customContextMenuRequested.connect(self._on_region_table_context_menu)
        layout.addWidget(self._region_table)

        btn_row = QHBoxLayout()
        self._btn_new_region = QPushButton(tr("+ 创建区域"))
        self._btn_new_region.clicked.connect(self._on_new_region)
        btn_row.addWidget(self._btn_new_region)
        self._btn_del_region = QPushButton(tr("删除区域"))
        self._btn_del_region.clicked.connect(self._on_delete_region)
        self._btn_del_region.setEnabled(False)
        btn_row.addWidget(self._btn_del_region)
        # 跨场景引用：只能新增/移除，坐标属于源场景，本场景改不了
        self._btn_add_ref = QPushButton(tr("+ 引用区域"))
        self._btn_add_ref.clicked.connect(self._on_add_scene_reference)
        btn_row.addWidget(self._btn_add_ref)
        apply_button_style(self._btn_new_region)
        apply_button_style(self._btn_del_region, variant="danger")
        apply_button_style(self._btn_add_ref, variant="neutral")
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
        assigned_by_key = {r.key: r for r in assigned}
        assigned_keys = set(assigned_by_key)
        for region_def in scene.regions:
            if not is_view_visible(region_def.views, self._current_view):
                continue
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
            # 当前布局绑定的激活按键；空值代表使用默认坐标点击
            assigned_region = assigned_by_key.get(region_def.key)
            activation_key = (
                assigned_region.activation_key if assigned_region else ""
            )
            activation_item = QTableWidgetItem(activation_key)
            activation_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._region_table.setItem(row, 5, activation_item)
            # 禁用复选框
            disabled_keys = self._canvas.get_disabled_keys("region")
            cb = QCheckBox()
            cb.setChecked(region_def.key in disabled_keys)
            cb.stateChanged.connect(
                lambda state, k=region_def.key: self._on_toggle_disabled(k, "region", state)
            )
            self._region_table.setCellWidget(row, 6, centered_cell_widget(cb))
            # 跳转（页面切换契约）
            self._region_table.setItem(
                row, 7, QTableWidgetItem(region_def.to or ""))
            # 来源：原生留空
            self._region_table.setItem(row, 8, QTableWidgetItem(""))

        self._append_reference_rows(scene, assigned_by_key)
        self._region_table.blockSignals(False)

    def _append_reference_rows(self, scene, assigned_by_key) -> None:
        """追加跨场景引用行。

        引用项属于源场景，**只读**：不显示禁用复选框、双击不进编辑弹窗，
        坐标要改得去源场景改。这里只让它在本场景的列表和画布里看得见。
        """
        for ref in getattr(scene, "references", ()):
            if not is_view_visible(ref.views, self._current_view):
                continue
            source_def = get_region_def(ref.scene, ref.entity)
            if source_def is None:
                continue
            row = self._region_table.rowCount()
            self._region_table.insertRow(row)
            placed = ref.entity in assigned_by_key
            status = "\u2713" if placed else "\u25cb"
            cells = [
                f"{status} {source_def.name}",
                ref.entity, source_def.type,
                "\u2713" if source_def.is_text else "",
                "\u2713" if source_def.is_clickable else "",
                (assigned_by_key[ref.entity].activation_key
                 if placed else ""),
                "", source_def.to or "", get_scene_name(ref.scene),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # 整行置灰，和本场景原生定义一眼区分开
                item.setForeground(Qt.GlobalColor.gray)
                self._region_table.setItem(row, col, item)
            # 「来源」列显示场景名，源 key 只作 tooltip 与行数据——
            # _selected_reference() 靠这份数据回查引用项，不能拿显示名去匹配。
            source_item = self._region_table.item(row, 8)
            assert source_item is not None
            source_item.setData(Qt.ItemDataRole.UserRole, ref.scene)
            source_item.setToolTip(ref.scene)

    def _on_toggle_disabled(self, key: str, kind: str, state: int):
        """切换某 key 的禁用状态，标记场景 dirty"""
        self._canvas.set_item_disabled(kind, key, bool(state))

    # ─── 事件处理 ────────────────────────────────────────

    def _on_region_table_selection(self, row, col, prev_row, prev_col):
        """表格行选中时更新删除按钮状态"""
        self._btn_del_region.setEnabled(row >= 0)
        # 同步画布选中（表格已按视图过滤，row 不再对应 scene.regions 索引，改按 key 查）
        if row < 0:
            return
        key_item = self._region_table.item(row, 1)
        if key_item is None:
            return
        key = key_item.text()
        # 在画布可见区域中查找索引（select_region 按 _regions 可见列表定位）
        for i, r in enumerate(self._canvas.get_visible_regions()):
            if r.key == key:
                self._canvas.select_region(i)
                return

    def _on_edit_region_from_table(self, row, col):
        """双击表格行编辑区域（场景变更时跨场景迁移）"""
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        key_item = self._region_table.item(row, 1)
        if not scene or key_item is None:
            return
        old_def = next((r for r in scene.regions if r.key == key_item.text()), None)
        if old_def is None:
            return
        result = self._show_region_edit_dialog(old_def)
        if result is None:
            return
        new_def, target_scene, activation_key = result
        old_key = old_def.key
        new_key = new_def.key
        key_changed = new_key != old_key

        if target_scene == self._scene_key:
            try:
                if key_changed:
                    # key 变更：更新场景定义 + 所有布局
                    registry.rename_region_key(self._scene_key, old_key, new_key)
                    rename_item_key_across_all_layouts(self._scene_key, "region", old_key, new_key)
                    # 更新其他属性
                    registry.update_region_in_scene(self._scene_key, new_key, new_def)
                    # 同步画布数据中的 key
                    regions = self._canvas.get_regions()
                    for r in regions:
                        if r.key == old_key:
                            r.key = new_key
                    self._canvas.set_regions(regions)
                else:
                    # key 不变：只更新其他属性
                    registry.update_region_in_scene(self._scene_key, old_key, new_def)
            except ValueError as e:
                QMessageBox.warning(self, tr("更新失败"), str(e))
                return
            sync_scene_cache(self._scene_key)
            self._canvas.set_item_activation_key(
                "region", new_key, activation_key)
            self._refresh_lists()
            return
        # 跨场景迁移：目标场景视图体系不同，归属视图重置为基底
        new_def.view = ""
        # 先加到目标场景（key 冲突则中止，YAML 未动），再从当前场景移除
        try:
            registry.add_region_to_scene(target_scene, new_def)
        except ValueError as e:
            QMessageBox.warning(self, tr("迁移失败"), str(e))
            return
        if key_changed:
            rename_item_key_across_all_layouts(
                self._scene_key, "region", old_key, new_key)
            regions = self._canvas.get_regions()
            for region in regions:
                if region.key == old_key:
                    region.key = new_key
            self._canvas.set_regions(regions)
        self._canvas.set_item_activation_key(
            "region", new_key, activation_key)
        registry.remove_region_from_scene(self._scene_key, old_key)
        sync_scene_cache(self._scene_key)
        sync_scene_cache(target_scene)
        if self.on_item_migrated:
            self.on_item_migrated("region", new_key, self._scene_key, target_scene)
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
        result = self._show_region_edit_dialog(None)
        if result is None:
            return
        region_def, _, _ = result
        registry = get_registry()
        try:
            registry.add_region_to_scene(self._scene_key, region_def)
        except ValueError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
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
        key_item = self._region_table.item(row, 1)
        if not scene or key_item is None:
            return
        region_def = next((r for r in scene.regions if r.key == key_item.text()), None)
        if region_def is None:
            return
        reply = QMessageBox.question(
            self, tr("确认删除"),
            f"确定要从场景定义中删除区域「{region_def.name}」({region_def.key}) 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            registry.remove_region_from_scene(self._scene_key, region_def.key)
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    # ─── 编辑弹窗 ────────────────────────────────────────

    def _show_region_edit_dialog(
        self, region_def: RegionDef | None,
    ) -> tuple[RegionDef, str, str] | None:
        """弹窗编辑区域属性，返回 (新 RegionDef, 目标场景 key) 或 None（取消）

        仅编辑模式提供场景下拉框；新建时目标场景恒为当前场景。
        """
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(tr("新建区域") if region_def is None else tr("编辑区域"))
        form = QFormLayout(dialog)

        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("英文，如 my_region"))
        if region_def:
            key_edit.setText(region_def.key)
        # 允许编辑 key，但需要校验唯一性
        form.addRow("Key:", key_edit)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("中文名称"))
        if region_def:
            name_edit.setText(region_def.name)
        form.addRow(tr("名称:"), name_edit)

        type_combo = QComboBox()
        type_combo.addItems(sorted(VALID_REGION_TYPES))
        if region_def:
            type_combo.setCurrentText(region_def.type)
        form.addRow(tr("类型:"), type_combo)

        is_text_check = QCheckBox(tr("含文本"))
        if region_def:
            is_text_check.setChecked(region_def.is_text)
        else:
            is_text_check.setChecked(True)
        is_clickable_check = QCheckBox(tr("可点击"))
        if region_def:
            is_clickable_check.setChecked(region_def.is_clickable)
        add_attribute_row(form, is_text_check, is_clickable_check)

        placed = bool(
            region_def
            and any(r.key == region_def.key for r in self._canvas.get_regions())
        )
        activation_edit = add_activation_key_row(
            form,
            self._canvas.get_item_activation_key(
                "region", region_def.key) if region_def else "",
            enabled=placed,
        )
        add_definition_separator(form)

        # 仅编辑模式可选择归属场景（跨场景迁移）
        scene_combo = None
        if region_def is not None:
            scene_combo = add_scene_combo_row(form, self._scene_key)

        # 多视图场景可勾选多个归属视图；新建默认落在当前视图
        view_list = add_views_checklist_row(
            form, self._scene_key,
            list(region_def.views) if region_def else [self._current_view],
        )
        # 点击后到达的场景/视图——页面切换契约，只声明不驱动执行
        transition = add_transition_row(
            form, self._scene_key, region_def.to if region_def else "")
        transition.set_transition_enabled(is_clickable_check.isChecked())
        is_clickable_check.toggled.connect(transition.set_transition_enabled)

        # 场景切换时同步更新视图清单
        if scene_combo is not None:
            connect_scene_views_sync(scene_combo, view_list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        apply_dialog_button_box_style(buttons)
        error_label = add_dialog_action_row(
            form, buttons, leading_button=transition._local_views_button)

        # key/name 实时校验；按键只在点击确定时校验，
        # 避免输入 ESC 时对 E / ES 这类中间态报错。
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        scene = get_registry().get_scene(self._scene_key)
        # region/point/panel 共享命名空间
        existing = set()
        if scene:
            existing = {r.key for r in scene.regions} | {p.key for p in scene.points} | {p.key for p in scene.panels}
        # 编辑模式下，排除自身的 key（允许保持不变）
        old_key = region_def.key if region_def else None

        def _validate():
            k = key_edit.text().strip()
            # 检查 key 是否被占用（新建时检查全部，编辑时排除自身）
            if k in existing and k != old_key:
                ok_btn.setEnabled(False)
                error_label.setText(f"key 已被使用: {k}")
                return
            error_label.clear()
            ok_btn.setEnabled(bool(k and name_edit.text().strip()))

        key_edit.textChanged.connect(_validate)
        name_edit.textChanged.connect(_validate)
        activation_edit.textChanged.connect(_validate)
        _validate()

        def _accept():
            if validate_activation_key_edit(activation_edit, error_label):
                dialog.accept()

        buttons.accepted.connect(_accept)
        buttons.rejected.connect(dialog.reject)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None

        target_scene = (
            scene_combo.currentData() if scene_combo is not None else self._scene_key
        )
        activation_key = activation_edit.text().strip()
        return RegionDef(
            key=key_edit.text().strip(),
            name=name_edit.text().strip(),
            type=type_combo.currentText(),
            is_text=is_text_check.isChecked(),
            is_clickable=is_clickable_check.isChecked(),
            views=checklist_views_value(view_list, self._current_view),
            to=transition.value() if is_clickable_check.isChecked() else "",
        ), target_scene, normalize_key(activation_key) if activation_key else ""

    # ─── 跨场景引用 ──────────────────────────────────────

    def _selected_reference(self):
        """当前选中行若是引用，返回它，否则 None。"""
        row = self._region_table.currentRow()
        if row < 0:
            return None
        source_item = self._region_table.item(row, 8)
        key_item = self._region_table.item(row, 1)
        if source_item is None or key_item is None:
            return None
        source_key = source_item.data(Qt.ItemDataRole.UserRole)
        if not source_key:
            return None
        scene = get_registry().get_scene(self._scene_key)
        if not scene:
            return None
        return next((r for r in scene.references
                     if r.entity == key_item.text()
                     and r.scene == source_key), None)

    def _on_add_scene_reference(self):
        """引用另一个一级场景的区域。

        只能引用一级场景：其坐标同属画布归一化，原样可用、零变换。子场景的
        坐标相对外框，搬过来要做变换，是另一回事（见 subscene_refs）。
        """
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if scene is None:
            return
        taken = ({r.key for r in scene.regions} | {p.key for p in scene.points}
                 | {p.key for p in scene.panels}
                 | {r.key for r in scene.subscene_refs}
                 | {r.key for r in scene.references})

        dialog = QDialog(self)
        dialog.setWindowTitle(tr("引用区域"))
        form = QFormLayout(dialog)
        source = SceneAreaReferencePicker(self._scene_key, taken)
        if not source.has_candidates:
            QMessageBox.information(
                self, tr("引用区域"),
                tr("没有可引用的区域：一级场景中同名的 key 已被本场景占用。"))
            return
        form.addRow(tr("来源:"), source)
        view_list = add_views_checklist_row(
            form, self._scene_key, [self._current_view])
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        apply_dialog_button_box_style(buttons)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected = source.value()
        if selected is None:
            return
        scene_key, entity = selected
        try:
            registry.add_scene_reference(self._scene_key, SceneRefDef(
                scene=scene_key, entity=entity,
                views=checklist_views_value(view_list, self._current_view)))
        except ValueError as exc:
            QMessageBox.warning(self, tr("引用区域"), str(exc))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_remove_scene_reference(self):
        ref = self._selected_reference()
        if ref is None:
            return
        if QMessageBox.question(
            self, tr("移除引用"),
            tr("确定移除对 {scene}.{entity} 的引用吗？源场景的定义不受影响。")
                .format(scene=ref.scene, entity=ref.entity),
        ) != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        registry.remove_scene_reference(self._scene_key, ref.scene, ref.entity)
        sync_scene_cache(self._scene_key)
        self._refresh_lists()
