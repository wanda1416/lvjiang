"""画布组件 - 可交互的图片画布，支持框选/拖拽/缩放矩形"""

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget

from ...core.key_names import normalize_key
from ...core.layout_models import Arrow, CanvasConfig, Panel, Point, Region, SubsceneRef
from ...core.scene_registry import get_region_name, get_subscene_ref_def
from ...i18n import tr
from .canvas_interaction import (
    HANDLE_SIZE,
    CanvasInteractionMixin,
    DragMode,
    EditMode,
    HandlePos,
)
from .canvas_poi import CanvasPoiMixin

# ─── 颜色方案 ────────────────────────────────────────────

REGION_COLORS = [
    QColor(255, 87, 87, 60),    # 红
    QColor(87, 157, 255, 60),   # 蓝
    QColor(87, 255, 157, 60),   # 绿
    QColor(255, 197, 87, 60),   # 橙
    QColor(197, 87, 255, 60),   # 紫
    QColor(87, 255, 255, 60),   # 青
    QColor(255, 87, 197, 60),   # 粉
    QColor(157, 255, 87, 60),   # 黄绿
]


# ─── 画布组件 ────────────────────────────────────────────

class RegionCanvas(QWidget, CanvasInteractionMixin, CanvasPoiMixin):
    """可交互的图片画布，支持框选/拖拽/缩放矩形"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #1e1e1e;")

        # 当前场景 key（由外部设置，用于 DSL 引用复制）
        self._scene_key: str = ""

        # 图片
        self._pixmap: QPixmap | None = None
        self._original_image: np.ndarray | None = None  # 原始 numpy 数组，供 OCR
        self._img_w = 0  # 原始图片尺寸
        self._img_h = 0

        # 显示区域（图片在 widget 内的偏移和缩放）
        self._display_rect = QRectF()  # 图片显示区域（widget 坐标）
        self._base_scale = 1.0  # 适应窗口的基准缩放
        self._zoom = 1.0        # 用户缩放倍数

        # 区域列表（归一化坐标 0.0~1.0）
        self._regions: list[Region] = []
        self._selected_idx: int = -1  # 当前选中区域索引
        self._field_selected: bool = False  # 是否由右侧字段列表选中（单区域编辑模式）

        # 视图过滤：None = 不过滤；否则只显示 key 在集合内的定义
        # 被过滤掉的实例暂存在 _hidden_*，get_* 时仍会一并返回
        self._visible_keys: set[str] | None = None
        self._hidden_regions: list[Region] = []
        self._hidden_panels: list[Panel] = []
        self._hidden_subscene_refs: list[SubsceneRef] = []

        # 交互状态
        self._drag_mode: DragMode | None = None
        self._drag_handle: HandlePos | None = None
        self._drag_start = QPointF()   # 鼠标按下位置（归一化）
        self._drag_orig: Region | None = None  # 拖拽前的原始区域
        # 左键按下位置（widget 坐标），用于判定是否越过拖拽死区
        self._press_pos: QPointF | None = None

        # 右键拖拽平移
        self._panning = False
        self._pan_start = QPointF()

        # 吸附对齐参考线（归一化坐标，拖拽/拉伸时临时显示）
        self._snap_lines_x: list[float] = []
        self._snap_lines_y: list[float] = []

        # 信号回调
        self.on_region_changed = None  # callable() -> None
        self.on_canvas_changed = None  # callable() -> None
        self.on_panel_changed = None  # callable() -> None
        self.on_subscene_ref_changed = None  # callable() -> None
        self.on_selection_changed = None  # callable() -> None（仅选中态变化，不代表数据修改，不应标记 dirty）
        self.on_status_message = None  # callable(str) -> None（面向用户的提示，显示到对话框状态栏）

        # 当前场景的区域列表（由外部通过 set_regions 设置）
        self._current_regions: list[tuple[str, str]] = []

        # 画布配置（布局级别，由外部设置）
        self._canvas_config = CanvasConfig()
        self._edit_mode = EditMode.REGION  # 当前编辑模式

        # Panel 列表（布局级别实例数据）
        self._panels: list[Panel] = []
        self._panel_selected_idx: int = -1

        # Panel 放置模式（框选矩形绑定到 PanelDef）
        self._pending_panel_def = None  # PanelDef | None
        self._panel_drag_start: QPointF | None = None  # widget 坐标
        self._panel_drag_current: QPointF | None = None  # widget 坐标

        # Panel 移动/缩放状态
        self._panel_edit_mode = None  # DragMode (MOVING/RESIZING)
        self._panel_edit_handle: HandlePos | None = None  # 拉伸手柄
        self._panel_edit_start = QPointF()  # 拖拽起始 widget 坐标
        self._panel_edit_orig: Panel | None = None  # type: ignore[assignment]

        # 子场景引用：外框可编辑，内部实体只读绘制
        self._subscene_refs: list[SubsceneRef] = []
        self._subscene_contents: dict[str, dict[str, list]] = {}
        self._subscene_selected_idx = -1
        self._pending_subscene_ref_def = None
        self._subscene_drag_start: QPointF | None = None
        self._subscene_drag_current: QPointF | None = None
        self._subscene_edit_mode: DragMode | None = None
        self._subscene_edit_handle: HandlePos | None = None
        self._subscene_edit_start = QPointF()
        self._subscene_edit_orig: SubsceneRef | None = None

        # 画布编辑模式的交互状态
        self._canvas_drag_mode = None  # DragMode
        self._canvas_drag_handle: HandlePos | None = None
        self._canvas_drag_start = QPointF()
        self._canvas_drag_orig: CanvasConfig | None = None

        # point / arrow 状态（CanvasPoiMixin）
        self._init_poi_state()
        # 停手吸附检测定时器：画箭头时周期性检查鼠标是否静止
        self._arrow_snap_timer = QTimer(self)
        self._arrow_snap_timer.setInterval(50)
        self._arrow_snap_timer.timeout.connect(self.on_arrow_snap_tick)
        self._arrow_snap_timer.start()

    # ─── 公开接口 ────────────────────────────────────────

    def set_item_disabled(self, kind: str, key: str, disabled: bool):
        """设置某类型某 key 实例的 disabled 属性，并触发 dirty 回调"""
        target = self._find_item_by_kind(kind, key)
        if target is None and disabled:
            # 为无实例的 key 创建占位，保证 disabled 状态可持久化
            target = self._create_placeholder(kind, key)
            self._append_to_kind(kind, target)
        if target is not None:
            target.disabled = disabled
        # 按 kind 分发正确的回调
        if kind == "region" and self.on_region_changed:
            self.on_region_changed()
        elif kind in ("point", "arrow") and self.on_poi_changed:
            self.on_poi_changed()
        elif kind == "panel" and self.on_panel_changed:
            self.on_panel_changed()
        elif kind == "subscene_ref" and self.on_subscene_ref_changed:
            self.on_subscene_ref_changed()

    def get_disabled_keys(self, kind: str) -> set[str]:
        """获取某类型中已标记 disabled 的全部 key"""
        items = self._items_by_kind(kind)
        return {item.key for item in items if item.disabled}

    def get_item_activation_key(self, kind: str, key: str) -> str:
        """读取当前布局实体的激活按键；未放置或未绑定时为空。"""
        if kind not in ("region", "point"):
            return ""
        item = self._find_item_by_kind(kind, key)
        return getattr(item, "activation_key", "") if item is not None else ""

    def set_item_activation_key(self, kind: str, key: str, value: str) -> bool:
        """修改已放置实体的布局级激活按键，并触发布局 dirty。"""
        if kind not in ("region", "point"):
            raise ValueError(f"实体类型不支持激活按键: {kind}")
        item = self._find_item_by_kind(kind, key)
        if item is None:
            return False
        normalized = normalize_key(value) if value else ""
        if item.activation_key == normalized:
            return True
        item.activation_key = normalized
        if kind == "region":
            self._notify_changed()
        else:
            self._notify_poi_changed()
        return True

    def _items_by_kind(self, kind: str) -> list:
        """按类型取实例列表（含隐藏项）"""
        if kind == "region":
            return self._regions + self._hidden_regions
        if kind == "panel":
            return self._panels + self._hidden_panels
        if kind == "point":
            return self._points + self._hidden_points
        if kind == "arrow":
            return self._arrows + self._hidden_arrows
        if kind == "subscene_ref":
            return self._subscene_refs + self._hidden_subscene_refs
        return []

    def _find_item_by_kind(self, kind: str, key: str):
        """按类型+key 查找实例"""
        for item in self._items_by_kind(kind):
            if item.key == key:
                return item
        return None

    @staticmethod
    def _create_placeholder(kind: str, key: str):
        """创建零坐标占位实例（用于 disabled 但无画布实例的 key）"""
        if kind == "region":
            return Region(key=key, x_ratio=0, y_ratio=0, w_ratio=0, h_ratio=0, disabled=True)
        if kind == "point":
            return Point(key=key, cx_ratio=0, cy_ratio=0, disabled=True)
        if kind == "arrow":
            return Arrow(key=key, from_key="", disabled=True)
        if kind == "panel":
            return Panel(key=key, x_ratio=0, y_ratio=0, w_ratio=0, h_ratio=0, disabled=True)
        if kind == "subscene_ref":
            return SubsceneRef(key=key, x_ratio=0, y_ratio=0, w_ratio=0, h_ratio=0, disabled=True)
        raise ValueError(f"unknown kind: {kind}")

    def _append_to_kind(self, kind: str, item):
        """将占位实例追加到对应列表"""
        if kind == "region":
            self._regions.append(item)
        elif kind == "point":
            self._points.append(item)
        elif kind == "arrow":
            self._arrows.append(item)
        elif kind == "panel":
            self._panels.append(item)
        elif kind == "subscene_ref":
            self._subscene_refs.append(item)

    def set_current_regions(self, regions: list[tuple[str, str]]):
        """设置当前场景的区域列表（由对话框调用）"""
        self._current_regions = regions

    def set_regions(self, regions: list[Region]):
        """设置区域列表（从预设加载）"""
        self._regions, self._hidden_regions = self._split_by_filter(
            [region.clone() for region in regions]
        )
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    def get_regions(self) -> list[Region]:
        """获取全部区域列表（含被视图过滤隐藏的，保存布局时不能写丢）"""
        return [
            region.clone()
            for region in self._regions + self._hidden_regions
            if region.key
        ]

    def get_visible_regions(self) -> list[Region]:
        """获取当前视图下可见的区域列表（识别/OCR 只应作用于可见区域）"""
        return [region.clone() for region in self._regions if region.key]

    # ─── 视图过滤 ─────────────────────────────────────────

    def _split_by_filter(self, items: list) -> tuple[list, list]:
        """按当前视图过滤把定义拆成 (可见, 隐藏) 两份"""
        if self._visible_keys is None:
            return list(items), []
        visible: list[Region] = []
        hidden: list[Region] = []
        for it in items:
            (visible if it.key in self._visible_keys else hidden).append(it)
        return visible, hidden

    def set_view_filter(self, visible_keys: set[str] | None):
        """设置视图过滤（None = 显示全部）

        隐藏的实例只是移出渲染与命中检测，数据仍保留在画布内，
        get_regions/get_points/... 会连同隐藏项一起返回。
        """
        self._visible_keys = set(visible_keys) if visible_keys is not None else None
        self._regions, self._hidden_regions = self._split_by_filter(
            self._regions + self._hidden_regions
        )
        self._panels, self._hidden_panels = self._split_by_filter(
            self._panels + self._hidden_panels
        )
        self._subscene_refs, self._hidden_subscene_refs = self._split_by_filter(
            self._subscene_refs + self._hidden_subscene_refs
        )
        self._apply_poi_filter()
        self._selected_idx = -1
        self._field_selected = False
        self._panel_selected_idx = -1
        self.update()

    def select_region(self, idx: int):
        """从外部（右侧字段列表）选中某区域，进入单区域编辑模式"""
        if 0 <= idx < len(self._regions):
            self._selected_idx = idx
            self._field_selected = True
            self._panel_selected_idx = -1
            self._subscene_selected_idx = -1
            self.update()

    def delete_selected(self):
        """删除选中区域"""
        if self._selected_idx >= 0:
            self._regions.pop(self._selected_idx)
            self._selected_idx = -1
            self._field_selected = False
            self._notify_changed()
            self.update()

    def clear_field_selection(self):
        """清除字段选择，回到全局调整模式"""
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    # ─── Panel 管理 ───────────────────────────────

    def set_scene_key(self, scene_key: str):
        """设置当前场景 key（用于 DSL 引用复制）"""
        self._scene_key = scene_key

    def set_panels(self, panels: list[Panel]):
        """设置面板列表（从布局加载）"""
        self._panels, self._hidden_panels = self._split_by_filter(
            [panel.clone() for panel in panels]
        )
        self._panel_selected_idx = -1
        self.update()

    def get_panels(self) -> list[Panel]:
        """获取全部面板列表（含被视图过滤隐藏的）"""
        return [panel.clone()
                for panel in self._panels + self._hidden_panels]

    def get_visible_panels(self) -> list[Panel]:
        """获取当前视图下可见的面板列表（识别/OCR 只应作用于可见面板）"""
        return [panel.clone() for panel in self._panels]

    def select_panel_by_key(self, key: str):
        """按 key 选中面板"""
        for i, p in enumerate(self._panels):
            if p.key == key:
                self._panel_selected_idx = i
                self._selected_idx = -1
                self._field_selected = False
                self._subscene_selected_idx = -1
                self.update()
                return
        self._panel_selected_idx = -1
        self.update()

    def _notify_panel_changed(self):
        """通知面板变化"""
        if self.on_panel_changed:
            self.on_panel_changed()

    def begin_place_panel(self, panel_def):
        """进入面板放置模式：等待用户在画布上框选矩形区域绑定到 PanelDef"""
        self._pending_panel_def = panel_def
        self._panel_drag_start = None
        self._panel_drag_current = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()  # 确保画布获得焦点以接收鼠标事件
        self.update()

    def cancel_panel_place(self):
        """取消面板放置模式"""
        self._pending_panel_def = None
        self._panel_drag_start = None
        self._panel_drag_current = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    # ─── Panel 命中检测与移动/缩放 ─────────────────────

    def _panel_rect_widget(self, p: Panel) -> QRectF:
        """Panel 的 widget 坐标矩形"""
        sx, sy = self._canvas_to_screenshot_norm(p.x_ratio, p.y_ratio)
        sw, sh = self._canvas_to_screenshot_norm(
            p.x_ratio + p.w_ratio, p.y_ratio + p.h_ratio
        )
        tl = self._norm_to_widget(sx, sy)
        br = self._norm_to_widget(sw, sh)
        return QRectF(tl, br)

    def _panel_handle_positions(self, p: Panel) -> dict[HandlePos, QPointF]:
        """获取 panel 的 8 个缩放手柄 widget 坐标"""
        rect = self._panel_rect_widget(p)
        cx = rect.center().x()
        cy = rect.center().y()
        return {
            HandlePos.TOP_LEFT:     rect.topLeft(),
            HandlePos.TOP:          QPointF(cx, rect.top()),
            HandlePos.TOP_RIGHT:    rect.topRight(),
            HandlePos.RIGHT:        QPointF(rect.right(), cy),
            HandlePos.BOTTOM_RIGHT: rect.bottomRight(),
            HandlePos.BOTTOM:       QPointF(cx, rect.bottom()),
            HandlePos.BOTTOM_LEFT:  rect.bottomLeft(),
            HandlePos.LEFT:         QPointF(rect.left(), cy),
        }

    def _hit_panel_handle(self, p: Panel, pos: QPointF) -> HandlePos | None:
        """检测是否命中 panel 的缩放手柄"""
        handles = self._panel_handle_positions(p)
        for hpos, center in handles.items():
            hr = QRectF(
                center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                HANDLE_SIZE * 2, HANDLE_SIZE * 2,
            )
            if hr.contains(pos):
                return hpos
        return None

    def _hit_panel_test(self, pos: QPointF) -> tuple[int, HandlePos | None]:
        """测试鼠标位置命中了哪个 panel，返回 (panel_index, handle or None)"""
        # 先检测手柄（优先级更高）
        if self._panel_selected_idx >= 0:
            p = self._panels[self._panel_selected_idx]
            handle = self._hit_panel_handle(p, pos)
            if handle is not None:
                return self._panel_selected_idx, handle
        # 再检测矩形
        for i in range(len(self._panels) - 1, -1, -1):
            rect = self._panel_rect_widget(self._panels[i])
            if rect.contains(pos):
                return i, None
        return -1, None

    def _apply_panel_resize(
        self, p: Panel, orig: Panel, pos: QPointF, shift_held: bool = False,
    ):
        """根据拖拽手柄位置调整 panel 大小"""
        # widget 位移 -> 归一化位移
        dx_n, dy_n = self._widget_delta_to_canvas_norm(
            self._panel_edit_start, pos)
        handle = self._panel_edit_handle
        x1, y1 = orig.x_ratio, orig.y_ratio
        x2 = x1 + orig.w_ratio
        y2 = y1 + orig.h_ratio
        min_size = 0.02
        moving_left = handle in (
            HandlePos.LEFT, HandlePos.TOP_LEFT, HandlePos.BOTTOM_LEFT)
        moving_right = handle in (
            HandlePos.RIGHT, HandlePos.TOP_RIGHT, HandlePos.BOTTOM_RIGHT)
        moving_top = handle in (
            HandlePos.TOP, HandlePos.TOP_LEFT, HandlePos.TOP_RIGHT)
        moving_bottom = handle in (
            HandlePos.BOTTOM, HandlePos.BOTTOM_LEFT, HandlePos.BOTTOM_RIGHT)
        if moving_left:
            x1 = min(x1 + dx_n, x2 - min_size)
        if moving_right:
            x2 = max(x1 + min_size, x2 + dx_n)
        if moving_top:
            y1 = min(y1 + dy_n, y2 - min_size)
        if moving_bottom:
            y2 = max(y1 + min_size, y2 + dy_n)
        x1, y1, x2, y2 = self._snap_resize_edges(
            x1, y1, x2, y2,
            moving_left=moving_left, moving_right=moving_right,
            moving_top=moving_top, moving_bottom=moving_bottom,
            exclude_kind="panel", exclude_idx=self._panel_selected_idx,
            min_size=min_size, shift_held=shift_held,
        )
        p.x_ratio = max(0.0, x1)
        p.y_ratio = max(0.0, y1)
        p.w_ratio = min(1.0, x2) - p.x_ratio
        p.h_ratio = min(1.0, y2) - p.y_ratio

    # ─── 子场景引用管理 ─────────────────────────────────

    def set_subscene_refs(self, refs: list[SubsceneRef]):
        self._subscene_refs, self._hidden_subscene_refs = self._split_by_filter(
            [ref.clone() for ref in refs])
        self._subscene_selected_idx = -1
        self.update()

    def get_subscene_refs(self) -> list[SubsceneRef]:
        return [ref.clone()
                for ref in self._subscene_refs + self._hidden_subscene_refs]

    def set_subscene_contents(self, contents: dict[str, dict[str, list]]):
        self._subscene_contents = contents
        self.update()

    def select_subscene_ref_by_key(self, key: str):
        self._subscene_selected_idx = next(
            (i for i, r in enumerate(self._subscene_refs) if r.key == key), -1)
        if self._subscene_selected_idx >= 0:
            self._selected_idx = -1
            self._field_selected = False
            self._panel_selected_idx = -1
        self.update()

    def begin_place_subscene_ref(self, ref_def):
        self._pending_subscene_ref_def = ref_def
        self._subscene_drag_start = None
        self._subscene_drag_current = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self.update()

    def cancel_subscene_ref_place(self):
        self._pending_subscene_ref_def = None
        self._subscene_drag_start = None
        self._subscene_drag_current = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def _subscene_ref_rect_widget(self, ref: SubsceneRef) -> QRectF:
        return self._region_rect_widget(ref)  # geometry 与 Region 相同

    def _hit_subscene_ref_test(self, pos: QPointF):
        if self._subscene_selected_idx >= 0:
            ref = self._subscene_refs[self._subscene_selected_idx]
            handle = self._hit_handle(ref, pos)
            if handle is not None:
                return self._subscene_selected_idx, handle
        for i in range(len(self._subscene_refs) - 1, -1, -1):
            if self._subscene_ref_rect_widget(self._subscene_refs[i]).contains(pos):
                return i, None
        return -1, None

    def _notify_subscene_ref_changed(self):
        if self.on_subscene_ref_changed:
            self.on_subscene_ref_changed()

    def focus_canvas(self):
        """把当前画布裁剪区域按宽或高完整填入显示区。"""
        if not self._pixmap or self.width() <= 0 or self.height() <= 0:
            return
        c = self._canvas_config
        crop_w = max(1.0, self._img_w * c.w_ratio)
        crop_h = max(1.0, self._img_h * c.h_ratio)
        scale = min(self.width() / crop_w, self.height() / crop_h)
        self._zoom = max(0.1, scale / max(self._base_scale, 1e-9))
        full_w, full_h = self._img_w * scale, self._img_h * scale
        x = (self.width() - crop_w * scale) / 2 - c.x_ratio * full_w
        y = (self.height() - crop_h * scale) / 2 - c.y_ratio * full_h
        self._display_rect = QRectF(x, y, full_w, full_h)
        self.update()

    # ─── 模式切换 ───────────────────────────────────

    def set_canvas_mode(self):
        """切换到画布编辑模式"""
        self._edit_mode = EditMode.CANVAS
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    def set_region_mode(self):
        """切换到区域编辑模式"""
        self._edit_mode = EditMode.REGION
        self.update()

    @property
    def edit_mode(self) -> EditMode:
        return self._edit_mode

    # ─── 画布配置访问 ───────────────────────────────

    def set_canvas_config(self, config: CanvasConfig):
        """设置画布配置（由外部调用）

        存副本而非直接引用，避免多个 Tab 共享同一 CanvasConfig 实例，
        否则在一个 Tab 中原地修改会联动影响其他 Tab。
        """
        self._canvas_config = CanvasConfig(
            config.x_ratio, config.y_ratio, config.w_ratio, config.h_ratio,
        )
        self.update()

    def get_canvas_config(self) -> CanvasConfig:
        """获取当前画布配置"""
        return CanvasConfig(
            self._canvas_config.x_ratio, self._canvas_config.y_ratio,
            self._canvas_config.w_ratio, self._canvas_config.h_ratio,
        )

    # ─── 图片管理 ────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """设置背景图片（BGR numpy 数组）"""
        rgb = np.ascontiguousarray(image[:, :, ::-1])
        h, w = rgb.shape[:2]
        self._img_w = w
        self._img_h = h
        qimg = QImage(bytes(rgb.data), w, h, w * 3, QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self._original_image = image  # 保存原始 numpy 数组，供 OCR 使用
        self._zoom = 1.0  # 新图片时重置缩放
        self._recalc_display()
        self.update()

    def get_image(self) -> np.ndarray | None:
        """获取原始截图 numpy 数组（供 OCR 裁剪）"""
        return self._original_image

    def clear_image(self):
        """清除背景图片（切换布局时调用）"""
        self._pixmap = None
        self._original_image = None
        self._img_w = 0
        self._img_h = 0
        self._display_rect = QRectF()
        self.update()

    @property
    def pixmap(self) -> QPixmap | None:
        """是否有背景图片"""
        return self._pixmap

    @property
    def image_size(self) -> tuple[int, int]:
        """当前背景图尺寸 (w, h)，无图时为 (0, 0)"""
        return (self._img_w, self._img_h)

    # ─── 绘制 ────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制图片
        if self._pixmap:
            painter.drawPixmap(
                int(self._display_rect.x()),
                int(self._display_rect.y()),
                int(self._display_rect.width()),
                int(self._display_rect.height()),
                self._pixmap,
            )
        else:
            # 无图片时绘制占位提示
            self._draw_placeholder(painter)

        # 绘制画布框（遮罩 + 边框）
        self._draw_canvas_frame(painter)

        # 绘制区域
        if self._field_selected and self._selected_idx >= 0 and self._selected_idx < len(self._regions):
            # 单区域编辑模式：只显示选中区域
            r = self._regions[self._selected_idx]
            color = REGION_COLORS[self._selected_idx % len(REGION_COLORS)]
            self._draw_region(painter, r, color, True)
        else:
            # 全局调整模式：显示所有区域，选中的高亮
            for i, r in enumerate(self._regions):
                color = REGION_COLORS[i % len(REGION_COLORS)]
                is_sel = (i == self._selected_idx and not self._field_selected)
                self._draw_region(painter, r, color, is_sel)

        # 吸附对齐参考线（拖拽/拉伸时临时显示）
        if self._snap_lines_x or self._snap_lines_y:
            painter.save()
            painter.setPen(QPen(QColor(255, 0, 255), 1, Qt.PenStyle.DashLine))
            for cx in self._snap_lines_x:
                # 画布相对归一化 -> 截图归一化 -> widget 坐标
                sx, _ = self._canvas_to_screenshot_norm(cx, 0)
                x = self._display_rect.x() + sx * self._display_rect.width()
                painter.drawLine(
                    QPointF(x, self._display_rect.top()),
                    QPointF(x, self._display_rect.bottom()),
                )
            for cy in self._snap_lines_y:
                # 画布相对归一化 -> 截图归一化 -> widget 坐标
                _, sy = self._canvas_to_screenshot_norm(0, cy)
                y = self._display_rect.y() + sy * self._display_rect.height()
                painter.drawLine(
                    QPointF(self._display_rect.left(), y),
                    QPointF(self._display_rect.right(), y),
                )
            painter.restore()

        # 单区域编辑模式下隐藏 points/arrows/panels，聚焦当前 region
        if self._field_selected:
            painter.end()
            return

        # 绘制 point / arrow（在 region 之上）
        self._draw_arrows(painter)
        self._draw_points(painter)

        # 绘制 panel（在 point/arrow 之下，region 之上）
        self._draw_panels(painter)
        self._draw_subscene_refs(painter)

        # 绘制 panel 放置模式的拖拽预览矩形
        if self._panel_drag_start is not None and self._panel_drag_current is not None:
            preview_rect = QRectF(self._panel_drag_start, self._panel_drag_current).normalized()
            if preview_rect.width() > 1 and preview_rect.height() > 1:
                painter.fillRect(preview_rect, QColor(0, 255, 255, 40))
                pen = QPen(QColor(0, 255, 255), 2, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(preview_rect)
                # 显示待绑定的 panel key
                if self._pending_panel_def is not None:
                    label = f"绑定: {self._pending_panel_def.key}"
                    font = QFont("Microsoft YaHei", 10)
                    painter.setFont(font)
                    painter.setPen(QColor(255, 255, 255))
                    painter.drawText(
                        preview_rect.adjusted(4, 4, -4, -4),
                        Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                        label,
                    )

        if self._subscene_drag_start is not None and self._subscene_drag_current is not None:
            preview = QRectF(self._subscene_drag_start, self._subscene_drag_current).normalized()
            painter.setPen(QPen(QColor(80, 220, 255), 2, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(80, 220, 255, 35))
            painter.drawRect(preview)

        painter.end()

    def _draw_subscene_refs(self, painter: QPainter):
        for index, ref in enumerate(self._subscene_refs):
            rect = self._subscene_ref_rect_widget(ref)
            if rect.width() < 1 or rect.height() < 1:
                continue
            # 内部结构只读：使用变换后的父画布坐标绘制细线，不进入命中列表。
            content = self._subscene_contents.get(ref.key, {})
            for child in content.get("regions", []):
                virtual = Region(
                    key=child.key,
                    x_ratio=ref.x_ratio + child.x_ratio * ref.w_ratio,
                    y_ratio=ref.y_ratio + child.y_ratio * ref.h_ratio,
                    w_ratio=child.w_ratio * ref.w_ratio,
                    h_ratio=child.h_ratio * ref.h_ratio,
                )
                child_rect = self._region_rect_widget(virtual)
                painter.setPen(QPen(QColor(120, 220, 255, 170), 1))
                painter.setBrush(QColor(80, 180, 230, 25))
                painter.drawRect(child_rect)
            for child in content.get("panels", []):
                virtual = Region(
                    key=child.key,
                    x_ratio=ref.x_ratio + child.x_ratio * ref.w_ratio,
                    y_ratio=ref.y_ratio + child.y_ratio * ref.h_ratio,
                    w_ratio=child.w_ratio * ref.w_ratio,
                    h_ratio=child.h_ratio * ref.h_ratio,
                )
                painter.setPen(QPen(QColor(160, 120, 255, 170), 1,
                                    Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(self._region_rect_widget(virtual))
            for child in content.get("points", []):
                cx = ref.x_ratio + child.cx_ratio * ref.w_ratio
                cy = ref.y_ratio + child.cy_ratio * ref.h_ratio
                center = self._canvas_norm_center_widget(cx, cy)
                painter.setPen(QPen(QColor(120, 255, 180, 200), 2))
                painter.setBrush(QColor(120, 255, 180, 80))
                painter.drawEllipse(center, 3, 3)
            selected = index == self._subscene_selected_idx
            painter.setPen(QPen(QColor(255, 255, 0) if selected else QColor(40, 200, 255),
                                3 if selected else 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            ref_def = get_subscene_ref_def(self._scene_key, ref.key)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(rect.adjusted(4, 2, -4, -2),
                             Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                             ref_def.name if ref_def else ref.key)
            if selected:
                painter.setBrush(QColor(255, 255, 0))
                for center in self._get_handle_positions(ref).values():
                    painter.drawRect(QRectF(center.x() - HANDLE_SIZE,
                                            center.y() - HANDLE_SIZE,
                                            HANDLE_SIZE * 2, HANDLE_SIZE * 2))

    def _draw_placeholder(self, painter: QPainter):
        """无图片时绘制占位提示"""
        painter.save()
        # 深灰背景
        painter.fillRect(self.rect(), QColor(40, 40, 40))
        # 居中提示文字
        font = QFont("Microsoft YaHei", 12)
        painter.setFont(font)
        painter.setPen(QColor(150, 150, 150))
        text = tr("无截图\n请连接投屏后点击「刷新截图」")
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _draw_canvas_frame(self, painter: QPainter):
        """绘制画布框：遮罩 + 边框 + 手柄"""
        c = self._canvas_config
        # 如果画布覆盖全图，只绘制边框提示
        is_full = (c.x_ratio <= 0.001 and c.y_ratio <= 0.001
                   and c.w_ratio >= 0.998 and c.h_ratio >= 0.998)

        canvas_rect = self._canvas_rect_widget()

        if not is_full:
            # 画布外半透明遮罩
            painter.save()
            dr = self._display_rect
            # 上
            painter.fillRect(QRectF(dr.left(), dr.top(), dr.width(), canvas_rect.top() - dr.top()),
                             QColor(0, 0, 0, 120))
            # 下
            painter.fillRect(QRectF(dr.left(), canvas_rect.bottom(), dr.width(), dr.bottom() - canvas_rect.bottom()),
                             QColor(0, 0, 0, 120))
            # 左
            painter.fillRect(QRectF(dr.left(), canvas_rect.top(), canvas_rect.left() - dr.left(), canvas_rect.height()),
                             QColor(0, 0, 0, 120))
            # 右
            painter.fillRect(QRectF(canvas_rect.right(), canvas_rect.top(), dr.right() - canvas_rect.right(), canvas_rect.height()),
                             QColor(0, 0, 0, 120))
            painter.restore()

        # 画布边框（始终清晰可见）
        painter.save()
        is_canvas_mode = self._edit_mode == EditMode.CANVAS
        pen_color = QColor(255, 200, 0) if is_canvas_mode else QColor(255, 200, 0, 200)
        pen_width = 3 if is_canvas_mode else 2
        # 先画黑色底衬，确保在任何背景下都可辨
        painter.setPen(QPen(QColor(0, 0, 0, 180), pen_width + 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(canvas_rect)
        # 再画黄色虚线
        painter.setPen(QPen(pen_color, pen_width, Qt.PenStyle.DashLine))
        painter.drawRect(canvas_rect)
        painter.restore()

        # 画布编辑模式下绘制手柄
        if self._edit_mode == EditMode.CANVAS:
            painter.save()
            cx = canvas_rect.center().x()
            cy = canvas_rect.center().y()
            handles = {
                HandlePos.TOP_LEFT:     canvas_rect.topLeft(),
                HandlePos.TOP:          QPointF(cx, canvas_rect.top()),
                HandlePos.TOP_RIGHT:    canvas_rect.topRight(),
                HandlePos.RIGHT:        QPointF(canvas_rect.right(), cy),
                HandlePos.BOTTOM_RIGHT: canvas_rect.bottomRight(),
                HandlePos.BOTTOM:       QPointF(cx, canvas_rect.bottom()),
                HandlePos.BOTTOM_LEFT:  canvas_rect.bottomLeft(),
                HandlePos.LEFT:         QPointF(canvas_rect.left(), cy),
            }
            painter.setPen(QPen(QColor(255, 200, 0), 1))
            painter.setBrush(QBrush(QColor(255, 200, 0)))
            for center in handles.values():
                painter.drawRect(
                    QRectF(
                        center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                        HANDLE_SIZE * 2, HANDLE_SIZE * 2,
                    )
                )
            painter.restore()

    def _draw_region(self, painter: QPainter, r: Region, color: QColor, selected: bool):
        """绘制单个区域"""
        rect = self._region_rect_widget(r)
        if rect.width() < 1 or rect.height() < 1:
            return

        # 半透明填充
        painter.fillRect(rect, color)

        # 边框
        pen_color = color.lighter(150) if not selected else QColor(255, 255, 0)
        pen = QPen(pen_color, 2 if selected else 1)
        painter.setPen(pen)
        painter.drawRect(rect)

        # 标签（从场景定义获取名称）
        label = get_region_name(self._scene_key, r.key)
        if label:
            font = QFont("Microsoft YaHei", 9)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(label) + 8
            th = fm.height() + 4
            label_rect = QRectF(rect.x(), rect.y() - th, tw, th)
            # 标签背景
            painter.fillRect(label_rect, QColor(0, 0, 0, 180))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        # 缩放手柄
        if selected:
            painter.save()
            handles = self._get_handle_positions(r)
            painter.setPen(QPen(QColor(255, 255, 0), 1))
            painter.setBrush(QBrush(QColor(255, 255, 0)))
            for center in handles.values():
                painter.drawRect(
                    QRectF(
                        center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                        HANDLE_SIZE * 2, HANDLE_SIZE * 2,
                    )
                )
            painter.restore()

    def _draw_panels(self, painter: QPainter):
        """绘制所有 panel（青色虚线矩形 + 网格线 + 标签）"""
        for i, p in enumerate(self._panels):
            self._draw_panel(painter, p, i == self._panel_selected_idx)

    def _draw_panel(self, painter: QPainter, p: Panel, selected: bool):
        """绘制单个 panel：虚线外框 + 内部网格线 + 标签"""
        # 计算 panel 在 widget 中的矩形
        sx, sy = self._canvas_to_screenshot_norm(p.x_ratio, p.y_ratio)
        sw, sh = self._canvas_to_screenshot_norm(
            p.x_ratio + p.w_ratio, p.y_ratio + p.h_ratio
        )
        tl = self._norm_to_widget(sx, sy)
        br = self._norm_to_widget(sw, sh)
        rect = QRectF(tl, br)
        if rect.width() < 1 or rect.height() < 1:
            return

        # 半透明填充
        fill_color = QColor(0, 200, 200, 30) if not selected else QColor(0, 255, 255, 50)
        painter.fillRect(rect, fill_color)

        # 虚线外框
        pen_color = QColor(0, 220, 220) if not selected else QColor(0, 255, 255)
        pen = QPen(pen_color, 2 if selected else 1, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        # 内部网格线（cols/rows 分隔线）
        if p.cols > 1 or p.rows > 1:
            grid_pen = QPen(QColor(0, 200, 200, 100), 1, Qt.PenStyle.DotLine)
            painter.setPen(grid_pen)
            cell_w = rect.width() / max(p.cols, 1)
            cell_h = rect.height() / max(p.rows, 1)
            # 竖线
            for c in range(1, p.cols):
                x = rect.left() + c * cell_w
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            # 横线
            for r in range(1, p.rows):
                y = rect.top() + r * cell_h
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        # 标签
        label = f"{p.key} ({p.rows}x{p.cols})"
        font = QFont("Microsoft YaHei", 8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(label) + 8
        th = fm.height() + 4
        label_rect = QRectF(rect.x(), rect.y() - th, tw, th)
        painter.fillRect(label_rect, QColor(0, 180, 180, 200))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        # 选中时绘制缩放手柄
        if selected:
            painter.save()
            handles = self._panel_handle_positions(p)
            painter.setPen(QPen(QColor(0, 255, 255), 1))
            painter.setBrush(QBrush(QColor(0, 255, 255)))
            for center in handles.values():
                painter.drawRect(
                    QRectF(
                        center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                        HANDLE_SIZE * 2, HANDLE_SIZE * 2,
                    )
                )
            painter.restore()

    # ─── 尺寸变化 ────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小变化时，重置缩放并重新计算显示区域
        if self._pixmap:
            self._zoom = 1.0
            self._recalc_display()
