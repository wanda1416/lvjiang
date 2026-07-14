"""场景 Tab - 单个场景的编辑 Tab：左侧画布 + 右侧字段列表"""

import numpy as np
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QSplitter,
)
from PyQt6.QtCore import Qt

from ...core.region_config import Region, CanvasConfig, get_scene_fields
from .canvas import RegionCanvas, EditMode


class SceneTab(QWidget):
    """单个场景的编辑 Tab：左侧画布 + 右侧字段列表"""

    def __init__(self, scene_key: str, image: np.ndarray, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧画布
        self._canvas = RegionCanvas()
        self._canvas.set_image(image)
        self._canvas.set_current_fields(get_scene_fields(scene_key))
        splitter.addWidget(self._canvas)

        # 右侧字段列表
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("字段列表："))

        self._field_list = QListWidget()
        self._field_list.currentRowChanged.connect(self._on_list_selection)
        right_layout.addWidget(self._field_list)
        right_layout.addStretch()

        splitter.addWidget(right_panel)
        splitter.setSizes([650, 250])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._refresh_field_list()
        self._canvas.on_region_changed = self._refresh_field_list

    @property
    def canvas(self) -> RegionCanvas:
        return self._canvas

    @property
    def scene_key(self) -> str:
        return self._scene_key

    def get_regions(self) -> list[Region]:
        return self._canvas.get_regions()

    def set_regions(self, regions: list[Region]):
        self._canvas.set_regions(regions)
        self._refresh_field_list()

    def set_canvas_config(self, config: CanvasConfig):
        self._canvas.set_canvas_config(config)

    def get_canvas_config(self) -> CanvasConfig:
        return self._canvas.get_canvas_config()

    def set_canvas_mode(self):
        self._canvas.set_canvas_mode()

    def set_region_mode(self):
        self._canvas.set_region_mode()

    @property
    def edit_mode(self) -> EditMode:
        return self._canvas.edit_mode

    def _refresh_field_list(self):
        """刷新字段列表，显示已绑定/未绑定状态"""
        self._field_list.blockSignals(True)
        self._field_list.clear()
        fields = get_scene_fields(self._scene_key)
        assigned = self._canvas.get_regions()
        assigned_keys = {r.key for r in assigned}

        for key, name in fields:
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
            self._field_list.addItem(item)
        self._field_list.blockSignals(False)

    def _on_list_selection(self, row: int):
        """列表选中项变化时同步到画布"""
        if row < 0:
            # 取消选中：回到全局调整模式
            self._canvas.clear_field_selection()
            return
        fields = get_scene_fields(self._scene_key)
        if row >= len(fields):
            return
        key = fields[row][0]
        regions = self._canvas.get_regions()
        for i, r in enumerate(regions):
            if r.key == key:
                self._canvas.select_region(i)
                return
