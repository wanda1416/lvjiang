"""单个场景的编辑 Tab：左侧画布 + 右侧三 Tab（区域 / 坐标 / 方向）"""

import re

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSplitter, QTabWidget, QPushButton, QInputDialog, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QComboBox, QCheckBox, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt6.QtCore import Qt

from ...core.region_config import (
    Region, Point, Arrow, CanvasConfig,
    get_scene_regions, get_scene_point_pairs, get_point_def,
    get_registry, sync_scene_cache,
)
from ...core.scene_loader import RegionDef, PointDef, VALID_REGION_TYPES
from .canvas import RegionCanvas, EditMode


_RE_ARROW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")

_LIGHT_DIALOG_QSS = (
    "QInputDialog { background-color: #f0f0f0; }"
    "QLabel { color: #333; }"
    "QComboBox { background-color: white; color: #333; padding: 4px; }"
    "QPushButton { padding: 4px 16px; }"
)


class SceneTab(QWidget):
    """单个场景的编辑 Tab：左侧画布 + 右侧三 Tab（区域列表 / 坐标列表 / 方向列表）"""

    def __init__(self, scene_key: str, image: np.ndarray | None = None, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：画布顶部工具栏 + 画布
        self._canvas = RegionCanvas()
        if image is not None:
            self._canvas.set_image(image)
        self._canvas.set_current_regions(get_scene_regions(scene_key))
        self._canvas.set_current_points(get_scene_point_pairs(scene_key))

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(2)
        left_layout.addLayout(self._build_canvas_toolbar())
        left_layout.addWidget(self._canvas)
        splitter.addWidget(left)

        # 右侧三 Tab
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(self._build_region_panel(), "区域列表")
        self._right_tabs.addTab(self._build_point_panel(), "坐标列表")
        self._right_tabs.addTab(self._build_arrow_panel(), "方向列表")
        splitter.addWidget(self._right_tabs)
        splitter.setSizes([650, 250])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_arrow_list()
        self._canvas.on_region_changed = self._refresh_region_list
        self._canvas.on_poi_changed = self._on_poi_changed

    # ─── 面板构建 ────────────────────────────────────────

    def _build_canvas_toolbar(self) -> QHBoxLayout:
        """画布顶部工具栏（功能已移至各 Tab）"""
        bar = QHBoxLayout()
        bar.addStretch()
        return bar

    def _build_region_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._region_table = QTableWidget()
        self._region_table.setColumnCount(5)
        self._region_table.setHorizontalHeaderLabels(["名称", "Key", "类型", "含文本", "可点击"])
        self._region_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._region_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._region_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._region_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._region_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._region_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._region_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._region_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._region_table.verticalHeader().setVisible(False)
        self._region_table.currentCellChanged.connect(self._on_region_table_selection)
        self._region_table.cellDoubleClicked.connect(self._on_edit_region_from_table)
        # Key 列右击复制
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

    def _build_point_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self._point_list = QTableWidget()
        self._point_list.setColumnCount(2)
        self._point_list.setHorizontalHeaderLabels(["名称", "Key"])
        self._point_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._point_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._point_list.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._point_list.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._point_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._point_list.verticalHeader().setVisible(False)
        self._point_list.currentCellChanged.connect(lambda row, col, prev_row, prev_col: self._on_point_selection(row))
        self._point_list.cellDoubleClicked.connect(self._on_edit_point_from_table)
        # Key 列右击复制
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
        # Key 列右击复制
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

    # ─── 属性 ────────────────────────────────────────────

    @property
    def canvas(self) -> RegionCanvas:
        return self._canvas

    @property
    def scene_key(self) -> str:
        return self._scene_key

    @property
    def edit_mode(self) -> EditMode:
        return self._canvas.edit_mode

    # ─── region 数据 ─────────────────────────────────────

    def get_regions(self) -> list[Region]:
        return self._canvas.get_regions()

    def set_regions(self, regions: list[Region]):
        self._canvas.set_regions(regions)
        self._refresh_region_list()

    # ─── point / arrow 数据 ──────────────────────────────

    def get_points(self) -> list[Point]:
        return self._canvas.get_points()

    def set_points(self, points: list[Point]):
        self._canvas.set_points(points)
        self._refresh_point_list()

    def get_arrows(self) -> list[Arrow]:
        return self._canvas.get_arrows()

    def set_arrows(self, arrows: list[Arrow]):
        self._canvas.set_arrows(arrows)
        self._refresh_arrow_list()

    # ─── 画布配置 / 模式 ─────────────────────────────────

    def set_canvas_config(self, config: CanvasConfig):
        self._canvas.set_canvas_config(config)

    def get_canvas_config(self) -> CanvasConfig:
        return self._canvas.get_canvas_config()

    def set_canvas_mode(self):
        self._canvas.set_canvas_mode()

    def set_region_mode(self):
        self._canvas.set_region_mode()

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

    def _refresh_point_list(self):
        """刷新坐标列表，显示已放置/未放置状态"""
        self._point_list.blockSignals(True)
        self._point_list.setRowCount(0)
        pairs = get_scene_point_pairs(self._scene_key)
        placed = {p.key for p in self._canvas.get_points()}
        for key, name in pairs:
            row = self._point_list.rowCount()
            self._point_list.insertRow(row)
            # 名称
            status = "\u2713" if key in placed else "\u25cb"
            name_item = QTableWidgetItem(f"{status} {name}")
            if key not in placed:
                name_item.setForeground(Qt.GlobalColor.gray)
            self._point_list.setItem(row, 0, name_item)
            # Key
            key_item = QTableWidgetItem(key)
            if key not in placed:
                key_item.setForeground(Qt.GlobalColor.gray)
            self._point_list.setItem(row, 1, key_item)
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

        return PointDef(key=key_edit.text().strip(), name=name_edit.text().strip())

    def _refresh_lists(self):
        """刷新区域和坐标列表（场景定义变化后调用）"""
        self._canvas.set_current_regions(get_scene_regions(self._scene_key))
        self._canvas.set_current_points(get_scene_point_pairs(self._scene_key))
        self._refresh_region_list()
        self._refresh_point_list()

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
