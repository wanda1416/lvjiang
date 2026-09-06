"""POI 面板混入类 - 坐标/方向列表构建、刷新、CRUD、编辑弹窗"""

import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.key_names import normalize_key
from ...core.layout_manager import (
    delete_item_key_across_all_layouts,
    rename_item_key_across_all_layouts,
)
from ...core.scene_definition import VALID_REGION_TYPES, PointDef
from ...core.scene_definition_models import SceneRefDef
from ...core.scene_registry import (
    get_point_def,
    get_registry,
    get_scene_name,
    get_scene_point_pairs,
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
    add_scene_combo_row,
    add_transition_row,
    add_views_checklist_row,
    checklist_views_value,
    connect_scene_views_sync,
    prompt_scene_area_references,
)

_RE_ARROW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

_LIGHT_DIALOG_QSS = (
    "QInputDialog { background-color: palette(window); }"
    "QLabel { color: palette(text); }"
    "QComboBox { background-color: palette(base); color: palette(text); padding: 4px; }"
    "QPushButton { padding: 4px 16px; }"
)


class PoiPanelMixin:
    """POI（坐标/方向）面板混入类

    依赖主类提供:
        _scene_key, _canvas, _right_tabs,
        _point_list, _arrow_list,
        _btn_del_point, _btn_del_arrow,
        _refresh_lists(), on_item_migrated
    """

    # ─── 面板构建 ────────────────────────────────────────

    def _build_point_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._point_list = QTableWidget()
        # 列与区域表保持一致：坐标和区域本质都是 area，属性同构
        self._point_list.setColumnCount(9)
        self._point_list.setHorizontalHeaderLabels([
            tr("名称"), "Key", tr("类型"), tr("含文本"), tr("可点击"),
            tr("按键"), tr("禁用"), tr("跳转"), tr("来源"),
        ])
        # 列宽：名称/Key 自适应内容，布尔状态列固定窄宽
        header = self._point_list.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        for col in (3, 4, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(col, 50)
        self._point_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._point_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._point_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        strip_focus_rect(self._point_list)
        vheader = self._point_list.verticalHeader()
        assert vheader is not None
        vheader.setVisible(False)
        self._point_list.currentCellChanged.connect(lambda row, col, prev_row, prev_col: self._on_point_selection(row))
        self._point_list.cellDoubleClicked.connect(self._on_edit_point_from_table)
        self._point_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._point_list.customContextMenuRequested.connect(self._on_point_table_context_menu)
        layout.addWidget(self._point_list)

        btn_row = QHBoxLayout()
        self._btn_new_point_def = QPushButton(tr("+ 创建坐标"))
        self._btn_new_point_def.setToolTip(tr("在场景 YAML 中新增坐标点定义（meta 数据）"))
        self._btn_new_point_def.clicked.connect(self._on_new_point_def)
        btn_row.addWidget(self._btn_new_point_def)
        self._btn_add_point_ref = QPushButton(tr("+ 引用坐标"))
        self._btn_add_point_ref.setToolTip(
            tr("引用其他一级场景已定义的坐标；坐标属于源场景，本场景只读"))
        self._btn_add_point_ref.clicked.connect(self._on_add_point_reference)
        btn_row.addWidget(self._btn_add_point_ref)
        self._btn_del_point = QPushButton(tr("删除坐标"))
        self._btn_del_point.setToolTip(tr("从场景 YAML 中删除坐标点定义（meta 数据）"))
        self._btn_del_point.clicked.connect(self._on_delete_point_def)
        btn_row.addWidget(self._btn_del_point)
        self._btn_bind_point = QPushButton(tr("绑定坐标"))
        self._btn_bind_point.setToolTip(tr("在画布上放置一个坐标点（绑定到 YAML 定义）"))
        self._btn_bind_point.clicked.connect(self._on_new_point)
        btn_row.addWidget(self._btn_bind_point)
        apply_button_style(self._btn_new_point_def)
        apply_button_style(self._btn_add_point_ref, variant="neutral")
        apply_button_style(self._btn_bind_point, variant="neutral")
        apply_button_style(self._btn_del_point, variant="danger")
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    def _build_arrow_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._arrow_list = QTableWidget()
        self._arrow_list.setColumnCount(3)
        self._arrow_list.setHorizontalHeaderLabels(["Key", tr("方向"), tr("禁用")])
        header = self._arrow_list.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(2, 50)
        self._arrow_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._arrow_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._arrow_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        strip_focus_rect(self._arrow_list)
        vheader = self._arrow_list.verticalHeader()
        assert vheader is not None
        vheader.setVisible(False)
        self._arrow_list.currentCellChanged.connect(lambda row, col, prev_row, prev_col: self._on_arrow_selection(row))
        self._arrow_list.cellDoubleClicked.connect(self._on_edit_arrow_from_table)
        self._arrow_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._arrow_list.customContextMenuRequested.connect(self._on_arrow_table_context_menu)
        layout.addWidget(self._arrow_list)

        btn_row = QHBoxLayout()
        self._btn_new_arrow = QPushButton(tr("创建方向"))
        self._btn_new_arrow.setToolTip(tr("先选中一个已放置的坐标点，再从它拉出一条方向箭头"))
        self._btn_new_arrow.clicked.connect(self._on_new_arrow)
        btn_row.addWidget(self._btn_new_arrow)
        self._btn_del_arrow = QPushButton(tr("删除方向"))
        self._btn_del_arrow.clicked.connect(self._on_delete_arrow)
        btn_row.addWidget(self._btn_del_arrow)
        apply_button_style(self._btn_new_arrow)
        apply_button_style(self._btn_del_arrow, variant="danger")
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    # ─── 列表刷新 ────────────────────────────────────────

    def _refresh_point_list(self):
        """刷新坐标列表，显示已放置/未放置状态"""
        self._point_list.blockSignals(True)
        self._point_list.setRowCount(0)
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene:
            self._point_list.blockSignals(False)
            return
        placed = {p.key for p in self._canvas.get_points()}
        for point_def in scene.points:
            if not is_view_visible(point_def.views, self._current_view):
                continue
            row = self._point_list.rowCount()
            self._point_list.insertRow(row)
            # 名称
            status = "\u2713" if point_def.key in placed else "\u25cb"
            name_item = QTableWidgetItem(f"{status} {point_def.name}")
            if point_def.key not in placed:
                name_item.setForeground(Qt.GlobalColor.gray)
            self._point_list.setItem(row, 0, name_item)
            # Key
            key_item = QTableWidgetItem(point_def.key)
            if point_def.key not in placed:
                key_item.setForeground(Qt.GlobalColor.gray)
            self._point_list.setItem(row, 1, key_item)
            # 类型
            self._point_list.setItem(row, 2, QTableWidgetItem(point_def.type))
            # 含文本
            text_item = QTableWidgetItem("\u2713" if point_def.is_text else "")
            text_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._point_list.setItem(row, 3, text_item)
            # 可点击
            click_item = QTableWidgetItem("\u2713" if point_def.is_clickable else "")
            click_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._point_list.setItem(row, 4, click_item)
            # 禁用复选框
            disabled_keys = self._canvas.get_disabled_keys("point")
            cb = QCheckBox()
            cb.setChecked(point_def.key in disabled_keys)
            cb.stateChanged.connect(
                lambda state, k=point_def.key: self._on_toggle_poi_disabled(k, "point", state)
            )
            # 当前布局绑定的激活按键；空值代表使用默认坐标点击
            placed_by_key = {p.key: p for p in self._canvas.get_points()}
            assigned = placed_by_key.get(point_def.key)
            key_item = QTableWidgetItem(
                assigned.activation_key if assigned else "")
            key_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._point_list.setItem(row, 5, key_item)
            self._point_list.setCellWidget(row, 6, centered_cell_widget(cb))
            self._point_list.setItem(
                row, 7, QTableWidgetItem(point_def.to or ""))
            self._point_list.setItem(row, 8, QTableWidgetItem(""))

        self._append_point_reference_rows(scene, placed)
        self._point_list.blockSignals(False)

    def _append_point_reference_rows(self, scene, placed) -> None:
        """追加跨场景引用的坐标行。

        引用项属于源场景，**只读**：不显示禁用复选框、不进编辑弹窗，坐标要改
        得去源场景改。这里只让它在本场景的列表和画布里看得见。
        """
        placed_by_key = {p.key: p for p in self._canvas.get_points()}
        for ref in getattr(scene, "references", ()):
            if not is_view_visible(ref.views, self._current_view):
                continue
            source_def = get_point_def(ref.scene, ref.entity)
            if source_def is None:
                continue   # 该引用指向的是区域，由区域面板展示
            row = self._point_list.rowCount()
            self._point_list.insertRow(row)
            is_placed = ref.entity in placed
            assigned = placed_by_key.get(ref.entity)
            cells = [
                f"{'✓' if is_placed else '○'} {source_def.name}",
                ref.entity, source_def.type,
                "\u2713" if source_def.is_text else "",
                "\u2713" if source_def.is_clickable else "",
                assigned.activation_key if assigned else "",
                "", source_def.to or "", get_scene_name(ref.scene),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col in (3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setForeground(Qt.GlobalColor.gray)
                self._point_list.setItem(row, col, item)
            # 「来源」列显示场景名，源 key 退到 tooltip 与行数据
            source_item = self._point_list.item(row, 8)
            assert source_item is not None
            source_item.setData(Qt.ItemDataRole.UserRole, ref.scene)
            source_item.setToolTip(ref.scene)

    def _refresh_arrow_list(self):
        """刷新方向列表"""
        self._arrow_list.blockSignals(True)
        self._arrow_list.setRowCount(0)
        for a in self._canvas.get_visible_arrows():
            row = self._arrow_list.rowCount()
            self._arrow_list.insertRow(row)
            # Key
            self._arrow_list.setItem(row, 0, QTableWidgetItem(a.key))
            # 方向
            if a.to_key is not None:
                desc = f"{a.from_key} \u2192 {a.to_key}"
            else:
                cx = a.to_cx_ratio if a.to_cx_ratio is not None else 0.0
                cy = a.to_cy_ratio if a.to_cy_ratio is not None else 0.0
                desc = f"{a.from_key} \u2192 ({cx:.2f}, {cy:.2f})"
            self._arrow_list.setItem(row, 1, QTableWidgetItem(desc))
            # 禁用复选框
            disabled_keys = self._canvas.get_disabled_keys("arrow")
            cb = QCheckBox()
            cb.setChecked(a.key in disabled_keys)
            cb.stateChanged.connect(
                lambda state, k=a.key: self._on_toggle_poi_disabled(k, "arrow", state)
            )
            self._arrow_list.setCellWidget(row, 2, centered_cell_widget(cb))
        self._arrow_list.blockSignals(False)

    def _on_toggle_poi_disabled(self, key: str, kind: str, state: int):
        """切换某 key 的禁用状态，通过画布回调通知 dialog 标记 dirty"""
        self._canvas.set_item_disabled(kind, key, bool(state))

    def _on_poi_changed(self):
        """画布 point/arrow 数据变化时刷新两个列表"""
        self._refresh_point_list()
        self._refresh_arrow_list()

    # ─── point 列表选择 / 删除 ───────────────────────────

    def _on_point_selection(self, row: int):
        """选中已放置点 → 高亮；选中未放置点 → 进入落点模式"""
        if row < 0:
            self._canvas.clear_poi_selection()
            return
        key_item = self._point_list.item(row, 1)
        if key_item is None:
            return
        key = key_item.text()
        placed = {p.key for p in self._canvas.get_points()}
        if key in placed:
            self._canvas.select_point_by_key(key)
        else:
            pd = get_point_def(self._scene_key, key)
            name = pd.name if pd else key
            self._canvas.begin_place_point(key, name)

    def _on_new_point(self):
        """工具栏「创建坐标」：从未放置的 YAML 坐标点中选一个，进入落点模式"""
        pairs = get_scene_point_pairs(self._scene_key)
        placed = {p.key for p in self._canvas.get_points()}
        available = [(k, n) for k, n in pairs if k not in placed]
        if not available:
            QMessageBox.information(self, tr("无可用坐标"),
                                    tr("该场景所有 YAML 坐标点都已放置。"))
            return
        items = [f"{n} ({k})" for k, n in available]
        dlg = QInputDialog(self)
        dlg.setWindowTitle(tr("创建坐标"))
        dlg.setLabelText(tr("选择要放置的坐标点："))
        dlg.setComboBoxItems(items)
        dlg.setStyleSheet(_LIGHT_DIALOG_QSS)
        if not dlg.exec() or not dlg.textValue():
            return
        key, name = available[items.index(dlg.textValue())]
        self._canvas.begin_place_point(key, name)
        self._right_tabs.setCurrentIndex(1)

    def _on_new_arrow(self):
        """工具栏「创建方向」：从当前选中的已放置坐标点拉出箭头"""
        key = self._canvas.selected_point_key()
        if key is None:
            QMessageBox.information(
                self, tr("请先选中坐标"),
                tr("请先在画布上或坐标列表中选中一个已放置的坐标点，再创建方向。"),
            )
            return
        self._canvas.begin_draw_arrow(key)

    def _on_delete_point_def(self):
        """从 YAML 删除坐标点定义"""
        row = self._point_list.currentRow()
        if row < 0:
            return
        key_item = self._point_list.item(row, 1)
        if key_item is None:
            return
        key = key_item.text()
        reply = QMessageBox.question(
            self, tr("确认删除"),
            f"确定要从场景定义中删除坐标点「{key}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        try:
            registry.remove_point_from_scene(self._scene_key, key)
        except ValueError as e:
            QMessageBox.warning(self, tr("删除失败"), str(e))
            return
        # 连带以它为端点的 arrow：端点没了的 arrow 既画不出来也跑不了
        self._canvas.remove_item("point", key)
        delete_item_key_across_all_layouts(self._scene_key, "point", key)
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_new_point_def(self):
        """创建新坐标点定义"""
        result = self._show_point_edit_dialog(None)
        if result is None:
            return
        point_def, _, _ = result
        registry = get_registry()
        try:
            registry.add_point_to_scene(self._scene_key, point_def)
        except ValueError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_edit_point_from_table(self, row, col):
        """双击表格行编辑坐标点定义（场景变更时跨场景迁移）"""
        key_item = self._point_list.item(row, 1)
        if key_item is None:
            return
        key = key_item.text()
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if not scene:
            return
        old_def = next((p for p in scene.points if p.key == key), None)
        if not old_def:
            return
        result = self._show_point_edit_dialog(old_def)
        if result is None:
            return
        new_def, target_scene, activation_key = result
        old_key = old_def.key
        new_key = new_def.key
        key_changed = new_key != old_key

        if target_scene == self._scene_key:
            try:
                if key_changed:
                    # key 变更：更新场景定义 + 所有布局（含 Arrow 引用）
                    registry.rename_point_key(self._scene_key, old_key, new_key)
                    rename_item_key_across_all_layouts(self._scene_key, "point", old_key, new_key)
                    # 更新其他属性
                    registry.update_point_in_scene(self._scene_key, new_key, new_def)
                    # 同步画布数据中的 key（包括 point 和 arrow 引用）
                    points = self._canvas.get_points()
                    for p in points:
                        if p.key == old_key:
                            p.key = new_key
                    self._canvas.set_points(points)
                    # 更新 arrow 的 from_key/to_key 引用
                    arrows = self._canvas.get_arrows()
                    for a in arrows:
                        if a.from_key == old_key:
                            a.from_key = new_key
                        if a.to_key == old_key:
                            a.to_key = new_key
                    self._canvas.set_arrows(arrows)
                else:
                    # key 不变：只更新其他属性
                    registry.update_point_in_scene(self._scene_key, old_key, new_def)
            except ValueError as e:
                QMessageBox.warning(self, tr("更新失败"), str(e))
                return
            sync_scene_cache(self._scene_key)
            self._canvas.set_item_activation_key(
                "point", new_key, activation_key)
            self._refresh_lists()
            return
        # 跨场景迁移：目标场景视图体系不同，归属视图重置为基底
        new_def.view = ""
        # 先加到目标场景（key 冲突则中止，YAML 未动），再从当前场景移除
        try:
            registry.add_point_to_scene(target_scene, new_def)
        except ValueError as e:
            QMessageBox.warning(self, tr("迁移失败"), str(e))
            return
        if key_changed:
            rename_item_key_across_all_layouts(
                self._scene_key, "point", old_key, new_key)
            points = self._canvas.get_points()
            for point in points:
                if point.key == old_key:
                    point.key = new_key
            self._canvas.set_points(points)
            arrows = self._canvas.get_arrows()
            for arrow in arrows:
                if arrow.from_key == old_key:
                    arrow.from_key = new_key
                if arrow.to_key == old_key:
                    arrow.to_key = new_key
            self._canvas.set_arrows(arrows)
        self._canvas.set_item_activation_key(
            "point", new_key, activation_key)
        registry.retarget_references(
            self._scene_key, target_scene, old_key, new_key)
        registry.remove_point_from_scene(self._scene_key, old_key)
        sync_scene_cache(self._scene_key)
        sync_scene_cache(target_scene)
        if self.on_item_migrated:
            self.on_item_migrated("point", new_key, self._scene_key, target_scene)
        self._refresh_lists()

    def _on_point_table_context_menu(self, pos):
        """坐标表格右击菜单：Key 列右击自动复制"""
        item = self._point_list.itemAt(pos)
        if item is None:
            return
        row = item.row()
        col = item.column()
        # 只在 Key 列（列 1）触发复制
        if col != 1:
            return
        key_item = self._point_list.item(row, 1)
        if key_item and key_item.text():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(key_item.text())

    # ─── arrow 列表选择 / 重命名 / 删除 ──────────────────

    def _on_arrow_selection(self, row: int):
        if row < 0:
            self._canvas.clear_poi_selection()
            return
        key_item = self._arrow_list.item(row, 0)
        if key_item is None:
            return
        key = key_item.text()
        self._canvas.select_arrow_by_key(key)

    def _selected_arrow_key(self) -> str | None:
        row = self._arrow_list.currentRow()
        if row < 0:
            return None
        key_item = self._arrow_list.item(row, 0)
        return key_item.text() if key_item else None

    def _on_delete_arrow(self):
        key = self._selected_arrow_key()
        if key is None:
            return
        self._canvas.delete_arrow_by_key(key)

    def _on_edit_arrow_from_table(self, row, col):
        """双击重命名方向"""
        old = self._selected_arrow_key()
        if old is None:
            return
        existing = {a.key for a in self._canvas.get_arrows()} - {old}
        while True:
            new, ok = QInputDialog.getText(
                self, tr("重命名方向"), tr("输入新的方向 key（小写字母开头）："), text=old,
            )
            if not ok:
                return
            new = new.strip()
            if new == old:
                return
            if not _RE_ARROW_KEY.match(new):
                QMessageBox.warning(self, tr("命名非法"),
                                    tr("key 必须以小写字母开头，仅含小写字母/数字/下划线。"))
                continue
            if new in existing:
                QMessageBox.warning(self, tr("key 重复"), f"方向 key「{new}」已存在。")
                continue
            self._canvas.rename_arrow_by_key(old, new)
            return

    def _on_arrow_table_context_menu(self, pos):
        """方向表格右击菜单：Key 列右击自动复制"""
        item = self._arrow_list.itemAt(pos)
        if item is None:
            return
        row = item.row()
        col = item.column()
        # 只在 Key 列（列 0）触发复制
        if col != 0:
            return
        key_item = self._arrow_list.item(row, 0)
        if key_item and key_item.text():
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(key_item.text())

    # ─── 编辑弹窗 ────────────────────────────────────────

    def _show_point_edit_dialog(
        self, point_def: PointDef | None,
    ) -> tuple[PointDef, str, str] | None:
        """弹窗编辑坐标点属性，返回 (新 PointDef, 目标场景 key) 或 None（取消）

        仅编辑模式提供场景下拉框；新建时目标场景恒为当前场景。
        """
        dialog = QDialog(self)  # type: ignore[arg-type]
        dialog.setWindowTitle(tr("新建坐标") if point_def is None else tr("编辑坐标"))
        form = QFormLayout(dialog)

        key_edit = QLineEdit()
        key_edit.setPlaceholderText(tr("英文，如 my_point"))
        if point_def:
            key_edit.setText(point_def.key)
        # 允许编辑 key，但需要校验唯一性
        form.addRow("Key:", key_edit)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText(tr("中文名称"))
        if point_def:
            name_edit.setText(point_def.name)
        form.addRow(tr("名称:"), name_edit)

        type_combo = QComboBox()
        type_combo.addItems(sorted(VALID_REGION_TYPES))
        if point_def:
            type_combo.setCurrentText(point_def.type)
        form.addRow(tr("类型:"), type_combo)

        is_text_check = QCheckBox(tr("含文本"))
        if point_def:
            is_text_check.setChecked(point_def.is_text)
        is_clickable_check = QCheckBox(tr("可点击"))
        if point_def:
            is_clickable_check.setChecked(point_def.is_clickable)
        else:
            is_clickable_check.setChecked(True)
        add_attribute_row(form, is_text_check, is_clickable_check)

        placed = bool(
            point_def
            and any(p.key == point_def.key for p in self._canvas.get_points())
        )
        activation_edit = add_activation_key_row(
            form,
            self._canvas.get_item_activation_key(
                "point", point_def.key) if point_def else "",
            enabled=placed,
        )
        add_definition_separator(form)

        # 仅编辑模式可选择归属场景（跨场景迁移）
        scene_combo = None
        if point_def is not None:
            scene_combo = add_scene_combo_row(form, self._scene_key)

        # 多视图场景可选择归属视图；新建默认落在当前视图
        view_list = add_views_checklist_row(
            form, self._scene_key,
            list(point_def.views) if point_def else [self._current_view],
        )
        transition = add_transition_row(
            form, self._scene_key, point_def.to if point_def else "")
        transition.set_transition_enabled(is_clickable_check.isChecked())
        is_clickable_check.toggled.connect(transition.set_transition_enabled)

        # 场景切换时同步更新视图下拉框
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
        old_key = point_def.key if point_def else None

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
        return PointDef(
            key=key_edit.text().strip(),
            name=name_edit.text().strip(),
            type=type_combo.currentText(),
            is_text=is_text_check.isChecked(),
            is_clickable=is_clickable_check.isChecked(),
            views=checklist_views_value(view_list, self._current_view),
            to=transition.value() if is_clickable_check.isChecked() else "",
        ), target_scene, normalize_key(activation_key) if activation_key else ""

    # ─── 跨场景引用 ──────────────────────────────────────

    def _on_add_point_reference(self):
        """引用另一个一级场景的坐标。

        坐标和区域本质都是 area，都可以被引用；只能引用一级场景——其坐标同属
        画布归一化，原样可用、零变换。
        """
        registry = get_registry()
        scene = registry.get_scene(self._scene_key)
        if scene is None:
            return
        taken = ({r.key for r in scene.regions} | {p.key for p in scene.points}
                 | {p.key for p in scene.panels}
                 | {r.key for r in scene.subscene_refs}
                 | {r.key for r in scene.references})

        chosen = prompt_scene_area_references(
            self, self._scene_key, self._current_view, taken,
            title=tr("引用坐标"))
        if not chosen:
            return

        added: list[tuple[str, str]] = []
        failed: list[str] = []
        for source_scene, entity, views in chosen:
            try:
                registry.add_scene_reference(self._scene_key, SceneRefDef(
                    scene=source_scene, entity=entity, views=views))
            except ValueError as exc:
                failed.append(f"{source_scene}.{entity}: {exc}")
                continue
            added.append((source_scene, entity))
        if failed:
            QMessageBox.warning(
                self, tr("引用坐标"),
                tr("以下 {n} 条没有加上：\n{detail}").format(
                    n=len(failed), detail="\n".join(failed)))
        if not added:
            return
        sync_scene_cache(self._scene_key)
        callback = getattr(self, "on_scene_references_added", None)
        if callback:
            callback(self._scene_key, added)
        self._refresh_lists()
