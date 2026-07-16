"""画布组件 - 可交互的图片画布，支持框选/拖拽/缩放矩形"""

import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF, QPointF, QTimer
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QPen, QBrush, QColor,
    QPaintEvent, QFont,
)

from ...core.region_config import Region, CanvasConfig, EQUIP_REGIONS
from .canvas_interaction import CanvasInteractionMixin, EditMode, HandlePos, HANDLE_SIZE
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

        # 交互状态
        self._drag_mode = None  # DragMode
        self._drag_handle: HandlePos | None = None
        self._drag_start = QPointF()   # 鼠标按下位置（归一化）
        self._drag_orig: Region | None = None  # 拖拽前的原始区域

        # 右键拖拽平移
        self._panning = False
        self._pan_start = QPointF()

        # 吸附对齐参考线（归一化坐标，拖拽/拉伸时临时显示）
        self._snap_lines_x: list[float] = []
        self._snap_lines_y: list[float] = []

        # 信号回调
        self.on_region_changed = None  # callable() -> None
        self.on_canvas_changed = None  # callable() -> None

        # 当前场景的区域列表（由外部设置）
        self._current_regions = EQUIP_REGIONS

        # 画布配置（布局级别，由外部设置）
        self._canvas_config = CanvasConfig()
        self._edit_mode = EditMode.REGION  # 当前编辑模式

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

    def set_current_regions(self, regions: list[tuple[str, str]]):
        """设置当前场景的区域列表（由对话框调用）"""
        self._current_regions = regions

    def set_regions(self, regions: list[Region]):
        """设置区域列表（从预设加载）"""
        self._regions = [Region(**r.to_dict()) for r in regions]
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    def get_regions(self) -> list[Region]:
        """获取当前区域列表"""
        return [Region(**r.to_dict()) for r in self._regions if r.key]

    def select_region(self, idx: int):
        """从外部（右侧字段列表）选中某区域，进入单区域编辑模式"""
        if 0 <= idx < len(self._regions):
            self._selected_idx = idx
            self._field_selected = True
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
        """设置画布配置（由外部调用）"""
        self._canvas_config = config
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
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
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

    # ─── 绘制 ────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):
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

        # 绘制 point / arrow（在 region 之上）
        self._draw_arrows(painter)
        self._draw_points(painter)

        painter.end()

    def _draw_placeholder(self, painter: QPainter):
        """无图片时绘制占位提示"""
        painter.save()
        # 深灰背景
        painter.fillRect(self.rect(), QColor(40, 40, 40))
        # 居中提示文字
        font = QFont("Microsoft YaHei", 12)
        painter.setFont(font)
        painter.setPen(QColor(150, 150, 150))
        text = "无截图\n请连接投屏后点击「刷新截图」"
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

        # 标签
        if r.name:
            label = r.name
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

    # ─── 尺寸变化 ────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小变化时，重置缩放并重新计算显示区域
        if self._pixmap:
            self._zoom = 1.0
            self._recalc_display()
