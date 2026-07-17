"""POI 面板混入类 - 坐标/方向列表构建、刷新、CRUD、编辑弹窗"""

import re

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QMessageBox, QInputDialog, QComboBox, QCheckBox,
)
from PyQt6.QtCore import Qt

from ...core.region_config import (
    get_scene_point_pairs, get_point_def,
    get_registry, sync_scene_cache,
)
from ...core.scene_loader import PointDef, VALID_REGION_TYPES


_RE_ARROW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

_LIGHT_DIALOG_QSS = (
    "QInputDialog { background-color: #f0f0f0; }"
    "QLabel { color: #333; }"
    "QComboBox { background-color: white; color: #333; padding: 4px; }"
    "QPushButton { padding: 4px 16px; }"
)


class PoiPanelMixin:
    """POI（坐标/方向）面板混入类

    依赖主类提供:
        _scene_key, _canvas, _right_tabs,
        _point_list, _arrow_list,
        _btn_del_point, _btn_del_arrow,
        _refresh_lists()
    """

    # ─── 面板构建 ────────────────────────────────────────

    def _build_point_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._point_list = QTableWidget()
        self._point_list.setColumnCount(5)
        self._point_list.setHorizontalHeaderLabels(["名称", "Key", "类型", "含文本", "可点击"])
        self._point_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._point_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._point_list.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._point_list.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._point_list.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._point_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._point_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._point_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._point_list.verticalHeader().setVisible(False)
        self._point_list.currentCellChanged.connect(lambda row, col, prev_row, prev_col: self._on_point_selection(row))
        self._point_list.cellDoubleClicked.connect(self._on_edit_point_from_table)
        self._point_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._point_list.customContextMenuRequested.connect(self._on_point_table_context_menu)
        layout.addWidget(self._point_list)

        btn_row = QHBoxLayout()
        self._btn_new_point_def = QPushButton("+ 创建坐标")
        self._btn_new_point_def.setToolTip("在场景 YAML 中新增坐标点定义（meta 数据）")
        self._btn_new_point_def.clicked.connect(self._on_new_point_def)
        btn_row.addWidget(self._btn_new_point_def)
        self._btn_del_point = QPushButton("删除坐标")
        self._btn_del_point.setToolTip("从场景 YAML 中删除坐标点定义（meta 数据）")
        self._btn_del_point.clicked.connect(self._on_delete_point_def)
        btn_row.addWidget(self._btn_del_point)
        self._btn_bind_point = QPushButton("绑定坐标")
        self._btn_bind_point.setToolTip("在画布上放置一个坐标点（绑定到 YAML 定义）")
        self._btn_bind_point.clicked.connect(self._on_new_point)
        btn_row.addWidget(self._btn_bind_point)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    def _build_arrow_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._arrow_list = QTableWidget()
        self._arrow_list.setColumnCount(2)
        self._arrow_list.setHorizontalHeaderLabels(["Key", "方向"])
        self._arrow_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._arrow_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._arrow_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._arrow_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._arrow_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._arrow_list.verticalHeader().setVisible(False)
        self._arrow_list.currentCellChanged.connect(lambda row, col, prev_row, prev_col: self._on_arrow_selection(row))
        self._arrow_list.cellDoubleClicked.connect(self._on_edit_arrow_from_table)
        self._arrow_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._arrow_list.customContextMenuRequested.connect(self._on_arrow_table_context_menu)
        layout.addWidget(self._arrow_list)

        btn_row = QHBoxLayout()
        self._btn_new_arrow = QPushButton("创建方向")
        self._btn_new_arrow.setToolTip("先选中一个已放置的坐标点，再从它拉出一条方向箭头")
        self._btn_new_arrow.clicked.connect(self._on_new_arrow)
        btn_row.addWidget(self._btn_new_arrow)
        self._btn_del_arrow = QPushButton("删除方向")
        self._btn_del_arrow.clicked.connect(self._on_delete_arrow)
        btn_row.addWidget(self._btn_del_arrow)
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
        self._point_list.blockSignals(False)

    def _refresh_arrow_list(self):
        """刷新方向列表"""
        self._arrow_list.blockSignals(True)
        self._arrow_list.setRowCount(0)
        for a in self._canvas.get_arrows():
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
        self._arrow_list.blockSignals(False)

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
            QMessageBox.information(self, "无可用坐标",
                                    "该场景所有 YAML 坐标点都已放置。")
            return
        items = [f"{n} ({k})" for k, n in available]
        dlg = QInputDialog(self)
        dlg.setWindowTitle("创建坐标")
        dlg.setLabelText("选择要放置的坐标点：")
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
                self, "请先选中坐标",
                "请先在画布上或坐标列表中选中一个已放置的坐标点，再创建方向。",
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
            self, "确认删除",
            f"确定要从场景定义中删除坐标点「{key}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        registry = get_registry()
        try:
            registry.remove_point_from_scene(self._scene_key, key)
        except ValueError as e:
            QMessageBox.warning(self, "删除失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_new_point_def(self):
        """创建新坐标点定义"""
        point_def = self._show_point_edit_dialog(None)
        if point_def is None:
            return
        registry = get_registry()
        try:
            registry.add_point_to_scene(self._scene_key, point_def)
        except ValueError as e:
            QMessageBox.warning(self, "创建失败", str(e))
            return
        sync_scene_cache(self._scene_key)
        self._refresh_lists()

    def _on_edit_point_from_table(self, row, col):
        """双击表格行编辑坐标点定义"""
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
        new_def = self._show_point_edit_dialog(old_def)
        if new_def is None:
            return
        try:
            registry.update_point_in_scene(self._scene_key, key, new_def)
        except ValueError as e:
            QMessageBox.warning(self, "更新失败", str(e))
            return
        sync_scene_cache(self._scene_key)
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
                self, "重命名方向", "输入新的方向 key（小写字母开头）：", text=old,
            )
            if not ok:
                return
            new = new.strip()
            if new == old:
                return
            if not _RE_ARROW_KEY.match(new):
                QMessageBox.warning(self, "命名非法",
                                    "key 必须以小写字母开头，仅含小写字母/数字/下划线。")
                continue
            if new in existing:
                QMessageBox.warning(self, "key 重复", f"方向 key「{new}」已存在。")
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

    def _show_point_edit_dialog(self, point_def: PointDef | None) -> PointDef | None:
        """弹窗编辑坐标点属性，返回新的 PointDef 或 None（取消）"""
        dialog = QDialog(self)
        dialog.setWindowTitle("新建坐标" if point_def is None else "编辑坐标")
        form = QFormLayout(dialog)

        key_edit = QLineEdit()
        key_edit.setPlaceholderText("英文，如 my_point")
        if point_def:
            key_edit.setText(point_def.key)
            key_edit.setReadOnly(True)
        form.addRow("Key:", key_edit)

        name_edit = QLineEdit()
        name_edit.setPlaceholderText("中文名称")
        if point_def:
            name_edit.setText(point_def.name)
        form.addRow("名称:", name_edit)

        type_combo = QComboBox()
        type_combo.addItems(sorted(VALID_REGION_TYPES))
        if point_def:
            type_combo.setCurrentText(point_def.type)
        form.addRow("类型:", type_combo)

        is_text_check = QCheckBox("含文本")
        if point_def:
            is_text_check.setChecked(point_def.is_text)
        form.addRow(is_text_check)

        is_clickable_check = QCheckBox("可点击")
        if point_def:
            is_clickable_check.setChecked(point_def.is_clickable)
        else:
            is_clickable_check.setChecked(True)
        form.addRow(is_clickable_check)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        # 实时校验
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

        return PointDef(
            key=key_edit.text().strip(),
            name=name_edit.text().strip(),
            type=type_combo.currentText(),
            is_text=is_text_check.isChecked(),
            is_clickable=is_clickable_check.isChecked(),
        )
