"""单个场景的编辑 Tab：左侧画布 + 右侧四 Tab（区域 / 坐标 / 方向 / 面板）"""

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSplitter, QTabWidget,
)
from PyQt6.QtCore import Qt

from ....core.scene_registry import (
    Region, Point, Arrow, Panel, CanvasConfig,
    get_scene_regions, get_scene_point_pairs,
)
from .canvas import RegionCanvas, EditMode
from .scene_region_panel import RegionPanelMixin
from .scene_poi_panel import PoiPanelMixin
from .scene_panel_editor import PanelEditorMixin


class SceneTab(RegionPanelMixin, PoiPanelMixin, PanelEditorMixin, QWidget):
    """单个场景的编辑 Tab：左侧画布 + 右侧四 Tab（区域列表 / 坐标列表 / 方向列表 / 面板列表）"""

    def __init__(self, scene_key: str, image: np.ndarray | None = None, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：画布顶部工具栏 + 画布
        self._canvas = RegionCanvas()
        self._canvas.set_scene_key(scene_key)
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

        # 右侧四 Tab
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(self._build_region_panel(), "区域列表")
        self._right_tabs.addTab(self._build_point_panel(), "坐标列表")
        self._right_tabs.addTab(self._build_arrow_panel(), "方向列表")
        self._right_tabs.addTab(self._build_panel_panel(), "面板列表")
        splitter.addWidget(self._right_tabs)
        splitter.setSizes([650, 250])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_arrow_list()
        self._refresh_panel_list()
        self._canvas.on_region_changed = self._refresh_region_list
        self._canvas.on_poi_changed = self._on_poi_changed
        self._canvas.on_panel_changed = self._on_panel_changed
        # 选中态变化只刷新列表高亮，不走 dialog 的 dirty 链路
        self._canvas.on_selection_changed = self._on_selection_changed

    # ─── 面板构建 ────────────────────────────────────────

    def _build_canvas_toolbar(self) -> QHBoxLayout:
        """画布顶部工具栏（功能已移至各 Tab）"""
        bar = QHBoxLayout()
        bar.addStretch()
        return bar

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

    # ─── panel 数据 ───────────────────────────────

    def get_panels(self) -> list[Panel]:
        return self._canvas.get_panels()

    def set_panels(self, panels: list[Panel]):
        self._canvas.set_panels(panels)
        self._refresh_panel_list()

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

    def _refresh_lists(self):
        """刷新区域和坐标列表（场景定义变化后调用）"""
        self._canvas.set_current_regions(get_scene_regions(self._scene_key))
        self._canvas.set_current_points(get_scene_point_pairs(self._scene_key))
        self._refresh_region_list()
        self._refresh_point_list()
        self._refresh_panel_list()

    def _on_panel_changed(self):
        """画布 panel 数据变化时刷新面板列表"""
        self._refresh_panel_list()

    def _on_selection_changed(self):
        """画布选中态变化（非数据修改）：仅刷新各列表显示"""
        self._refresh_region_list()
        self._on_poi_changed()
        self._refresh_panel_list()
