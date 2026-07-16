"""单个场景的编辑 Tab：左侧画布 + 右侧三 Tab（区域 / 坐标 / 方向）"""

import re

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QSplitter, QTabWidget, QPushButton, QInputDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from ...core.region_config import (
    Region, Point, Arrow, CanvasConfig,
    get_scene_regions, get_scene_point_pairs, get_point_def,
)
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
        """画布顶部工具栏：创建坐标 / 创建方向"""
        bar = QHBoxLayout()
        self._btn_new_point = QPushButton("\u2795 创建坐标")
        self._btn_new_point.setToolTip(
            "从 YAML 定义中选择一个未放置的坐标点，然后在画布上单击放置"
        )
        self._btn_new_point.clicked.connect(self._on_new_point)
        bar.addWidget(self._btn_new_point)

        self._btn_new_arrow = QPushButton("\u2192 创建方向")
        self._btn_new_arrow.setToolTip(
            "先选中一个已放置的坐标点，再从它拉出一条方向箭头"
        )
        self._btn_new_arrow.clicked.connect(self._on_new_arrow)
        bar.addWidget(self._btn_new_arrow)

        bar.addStretch()
        return bar

    def _build_region_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("区域列表："))
        self._region_list = QListWidget()
        self._region_list.currentRowChanged.connect(self._on_region_selection)
        layout.addWidget(self._region_list)
        return panel

    def _build_point_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("坐标列表（来自 YAML 定义，✓ 已放置 / ○ 未放置）："))
        self._point_list = QListWidget()
        self._point_list.currentRowChanged.connect(self._on_point_selection)
        layout.addWidget(self._point_list)

        btn_row = QHBoxLayout()
        self._btn_del_point = QPushButton("删除坐标")
        self._btn_del_point.clicked.connect(self._on_delete_point)
        btn_row.addWidget(self._btn_del_point)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        return panel

    def _build_arrow_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("方向列表（选中坐标点后点击其 + 号创建）："))
        self._arrow_list = QListWidget()
        self._arrow_list.currentRowChanged.connect(self._on_arrow_selection)
        layout.addWidget(self._arrow_list)

        btn_row = QHBoxLayout()
        self._btn_rename_arrow = QPushButton("重命名")
        self._btn_rename_arrow.clicked.connect(self._on_rename_arrow)
        btn_row.addWidget(self._btn_rename_arrow)
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
        """刷新区域列表，显示已绑定/未绑定状态"""
        self._region_list.blockSignals(True)
        self._region_list.clear()
        regions = get_scene_regions(self._scene_key)
        assigned = self._canvas.get_regions()
        assigned_keys = {r.key for r in assigned}

        for key, name in regions:
            if key in assigned_keys:
                region = next(r for r in assigned if r.key == key)
                item = QListWidgetItem(f"\u2713 {name}")
                item.setToolTip(
                    f"区域: ({region.x_ratio:.1%}, {region.y_ratio:.1%}) "
                    f"大小: ({region.w_ratio:.1%} x {region.h_ratio:.1%})"
                )
            else:
                item = QListWidgetItem(f"\u25cb {name}")
                item.setForeground(Qt.GlobalColor.gray)
            self._region_list.addItem(item)
        self._region_list.blockSignals(False)

    def _refresh_point_list(self):
        """刷新坐标列表，显示已放置/未放置状态"""
        self._point_list.blockSignals(True)
        self._point_list.clear()
        pairs = get_scene_point_pairs(self._scene_key)
        placed = {p.key for p in self._canvas.get_points()}
        for key, name in pairs:
            if key in placed:
                item = QListWidgetItem(f"\u2713 {name} ({key})")
            else:
                item = QListWidgetItem(f"\u25cb {name} ({key})")
                item.setForeground(Qt.GlobalColor.gray)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self._point_list.addItem(item)
        self._point_list.blockSignals(False)

    def _refresh_arrow_list(self):
        """刷新方向列表"""
        self._arrow_list.blockSignals(True)
        self._arrow_list.clear()
        for a in self._canvas.get_arrows():
            if a.to_key is not None:
                desc = f"\u2192 {a.key}  ({a.from_key} \u2192 {a.to_key})"
            else:
                cx = a.to_cx_ratio if a.to_cx_ratio is not None else 0.0
                cy = a.to_cy_ratio if a.to_cy_ratio is not None else 0.0
                desc = f"\u2192 {a.key}  ({a.from_key} \u2192 {cx:.2f}, {cy:.2f})"
            item = QListWidgetItem(desc)
            item.setData(Qt.ItemDataRole.UserRole, a.key)
            self._arrow_list.addItem(item)
        self._arrow_list.blockSignals(False)

    def _on_poi_changed(self):
        """画布 point/arrow 数据变化时刷新两个列表"""
        self._refresh_point_list()
        self._refresh_arrow_list()

    # ─── region 列表选择 ─────────────────────────────────

    def _on_region_selection(self, row: int):
        """列表选中项变化时同步到画布"""
        if row < 0:
            self._canvas.clear_field_selection()
            return
        regions = get_scene_regions(self._scene_key)
        if row >= len(regions):
            return
        key = regions[row][0]
        assigned = self._canvas.get_regions()
        for i, r in enumerate(assigned):
            if r.key == key:
                self._canvas.select_region(i)
                return

    # ─── point 列表选择 / 删除 ───────────────────────────

    def _on_point_selection(self, row: int):
        """选中已放置点 → 高亮；选中未放置点 → 进入落点模式"""
        if row < 0:
            self._canvas.clear_poi_selection()
            return
        item = self._point_list.item(row)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
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

    def _on_delete_point(self):
        row = self._point_list.currentRow()
        if row < 0:
            return
        item = self._point_list.item(row)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        placed = {p.key for p in self._canvas.get_points()}
        if key not in placed:
            return
        self._canvas.delete_point_by_key(key)

    # ─── arrow 列表选择 / 重命名 / 删除 ──────────────────

    def _on_arrow_selection(self, row: int):
        if row < 0:
            self._canvas.clear_poi_selection()
            return
        item = self._arrow_list.item(row)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        self._canvas.select_arrow_by_key(key)

    def _selected_arrow_key(self) -> str | None:
        row = self._arrow_list.currentRow()
        if row < 0:
            return None
        item = self._arrow_list.item(row)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_delete_arrow(self):
        key = self._selected_arrow_key()
        if key is None:
            return
        self._canvas.delete_arrow_by_key(key)

    def _on_rename_arrow(self):
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
