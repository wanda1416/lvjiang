"""Panel 对齐与格子级操作原语"""

from typing import TYPE_CHECKING

from loguru import logger

from ...i18n import tr
from ..align import GridAlignment, _make_even_alignment, detect_grid

if TYPE_CHECKING:
    import numpy as np

    from ..align import GridAlignment


class _PanelMixin:
    """panel 网格对齐、格子点击/扫描、grid 拖拽滚动"""

    def _find_panel(self, scene_key: str, panel_key: str):
        """在 layout 中查找 panel 实例"""
        panels = self._layout.get_scene_panels(scene_key)
        return next((p for p in panels if p.key == panel_key), None)

    def _capture_panel_image(self, panel_obj) -> "np.ndarray | None":
        """截取 panel 区域图像"""
        full = self._capture.capture()
        if full is None:
            return None
        w_cap, h_cap = self._capture.get_capture_size()
        if w_cap == 0 or h_cap == 0:
            return None
        canvas = self._layout.get_canvas()
        canvas_x = int(canvas.x_ratio * w_cap)
        canvas_y = int(canvas.y_ratio * h_cap)
        canvas_w = int(canvas.w_ratio * w_cap)
        canvas_h = int(canvas.h_ratio * h_cap)
        px = canvas_x + int(panel_obj.x_ratio * canvas_w)
        py = canvas_y + int(panel_obj.y_ratio * canvas_h)
        pw = max(1, int(panel_obj.w_ratio * canvas_w))
        ph = max(1, int(panel_obj.h_ratio * canvas_h))
        h_img, w_img = full.shape[:2]
        x1, y1 = max(0, min(px, w_img)), max(0, min(py, h_img))
        x2, y2 = max(0, min(px + pw, w_img)), max(0, min(py + ph, h_img))
        if x2 <= x1 or y2 <= y1:
            return None
        return full[y1:y2, x1:x2].copy()

    def _panel_ratio_to_screen(self, panel_obj, cx: float, cy: float) -> tuple[int, int]:
        """panel 内归一化坐标 → 屏幕绝对坐标（钳位到 panel 内缩边距内）

        半可见行（min_visible 接近 0.5 时）的 slot 中心可能恰好压在 panel
        边缘，叠加底层点击的 ±click_random_offset 随机偏移与取整误差后，
        tap 会落到网格黑框之外导致点击静默失效。半截行的可见部分一定在
        边缘内侧，统一钳位到 panel 内缩 margin 的矩形内即可保证点中该行。
        """
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        px = canvas_x + panel_obj.x_ratio * canvas_w
        py = canvas_y + panel_obj.y_ratio * canvas_h
        pw = panel_obj.w_ratio * canvas_w
        ph = panel_obj.h_ratio * canvas_h
        sx = px + cx * pw
        sy = py + cy * ph
        margin = self._input_sim.click_random_offset + 2
        csx = max(px + margin, min(sx, px + pw - margin))
        csy = max(py + margin, min(sy, py + ph - margin))
        if (csx, csy) != (sx, sy):
            logger.debug(f"panel 坐标钳位: ({sx:.0f},{sy:.0f}) → ({csx:.0f},{csy:.0f})")
        return int(self._window_left + csx), int(self._window_top + csy)

    def align_panel(self, scene_key: str, panel_key: str) -> "GridAlignment | None":
        """对齐 panel 网格，缓存结果并返回 GridAlignment

        panel 没在布局里定义是配置错误，直接抛错；截图失败 / 未检测到
        slot 属于运行时情况（列表为空、页面未加载完），继续返回 None
        交由调用方判断。

        校准模式由 panel.calibration 控制：
        - "even"：跳过图像检测，直接按 rows/cols 等分
        - "image"：仅图像检测，失败返回 None
        - "auto"（默认）：先图像检测，失败降级为等分
        """
        panel_obj = self._find_panel(scene_key, panel_key)
        if panel_obj is None:
            raise ValueError(
                f"align: 布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        calibration = getattr(panel_obj, "calibration", "auto")

        # even 模式：跳过图像检测，直接等分
        alignment: GridAlignment | None
        if calibration == "even":
            alignment = _make_even_alignment(panel_obj.rows, panel_obj.cols)
            self._panel_alignments[(scene_key, panel_key)] = alignment
            logger.info(
                f"align: {scene_key}.{panel_key} 等分模式，"
                f"{alignment.n_rows}×{alignment.n_cols} = {alignment.total_slots} 个 slot"
            )
            return alignment

        # image / auto 模式：先尝试图像检测
        panel_img = self._capture_panel_image(panel_obj)
        if panel_img is None:
            logger.error(f"align: 无法截取 panel {scene_key}.{panel_key}")
            if calibration == "auto":
                alignment = _make_even_alignment(panel_obj.rows, panel_obj.cols)
                self._panel_alignments[(scene_key, panel_key)] = alignment
                logger.warning(f"align: {scene_key}.{panel_key} 截图失败，降级为等分模式")
                return alignment
            return None

        fallback = (calibration == "auto")
        alignment = detect_grid(
            panel_img,
            expected_rows=panel_obj.rows,
            expected_cols=panel_obj.cols,
            min_visible=getattr(panel_obj, "min_visible", 0.95),
            fallback=fallback,
            scroll_direction=getattr(panel_obj, "scroll_direction", "vertical"),
        )
        if alignment is None:
            logger.error(f"align: panel {scene_key}.{panel_key} 未检测到 slot")
            return None
        self._panel_alignments[(scene_key, panel_key)] = alignment
        mode = tr("等分降级") if (fallback and alignment.row_span == 0.0) else tr("图像检测")
        logger.info(
            f"align: {scene_key}.{panel_key} 已对齐（{mode}），"
            f"检测到 {alignment.n_rows}×{alignment.n_cols} = {alignment.total_slots} 个 slot 中心"
        )
        return alignment

    def _ensure_aligned(self, scene_key: str, panel_key: str) -> "GridAlignment | None":
        """确保 panel 已对齐，未对齐则自动触发"""
        cache_key = (scene_key, panel_key)
        if cache_key not in self._panel_alignments:
            return self.align_panel(scene_key, panel_key)
        return self._panel_alignments[cache_key]

    def _invalidate_align(self, scene_key: str, panel_key: str):
        """失效 panel 对齐缓存（drag 后调用）"""
        self._panel_alignments.pop((scene_key, panel_key), None)

    def click_panel(self, scene_key: str, panel_key: str, row: int, col: int) -> bool:
        """点击 panel 中指定格子（1-based），返回是否成功

        未对齐 / 索引越界返回 False 而不抛错：调用方把越界当作遍历网格
        的终止条件。panel 本身没绑定则由 align_panel 抛错。
        """
        cal = self._ensure_aligned(scene_key, panel_key)
        if cal is None:
            return False
        row_idx, col_idx = row - 1, col - 1
        if not (0 <= row_idx < cal.n_rows and 0 <= col_idx < cal.n_cols):
            logger.debug(f"panel 索引越界: [{row}][{col}]，对齐结果 {cal.n_rows}×{cal.n_cols}")
            return False
        panel_obj = self._find_panel(scene_key, panel_key)
        cx, cy = cal.slot_center(row_idx, col_idx)
        sx, sy = self._panel_ratio_to_screen(panel_obj, cx, cy)
        self._input.click_screen(sx, sy, f"panel({scene_key}.{panel_key}[{row}][{col}])")
        return True

    def scan_panel(self, scene_key: str, panel_key: str, row: int, col: int,
                   detail_scene: str, fields: list[str]) -> dict:
        """点击格子后 OCR 详情场景，返回解析后的装备 dict（空则返回 {}）"""
        if not self.click_panel(scene_key, panel_key, row, col):
            return {}
        raw = self.ocr_scene(detail_scene, fields)
        if not raw:
            return {}
        return self.call_function("to_equipment", [raw])

    def drag_grid(self, scene_key: str, panel_key: str, direction: str,
                  distance: float = 1.0, hold: float | None = None):
        """按 panel 中心执行 grid 级拖拽（滚动），drag 后自动失效缓存"""
        panel_obj = self._find_panel(scene_key, panel_key)
        if panel_obj is None:
            raise ValueError(
                f"drag grid: 布局中未定义 panel {scene_key}.{panel_key}，"
                f"请在场景布局编辑器中绑定后重试"
            )
        cal = self._ensure_aligned(scene_key, panel_key)
        if cal is None or cal.row_slot <= 0:
            logger.error(f"drag grid: align 失败: {scene_key}.{panel_key}")
            return
        # 起点：panel 中心
        cx = panel_obj.x_ratio + panel_obj.w_ratio / 2
        cy = panel_obj.y_ratio + panel_obj.h_ratio / 2
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()
        x = int((canvas.x_ratio + cx * canvas.w_ratio) * w + self._window_left)
        y = int((canvas.y_ratio + cy * canvas.h_ratio) * h + self._window_top)
        # 距离计算
        dx, dy = 0, 0
        if direction in ("up", "down"):
            step_norm = cal.row_slot + cal.row_span / 2.0
            panel_pixel_h = panel_obj.h_ratio * canvas.h_ratio * h
            dy = int(step_norm * panel_pixel_h * distance)
            if direction == "up":
                dy = -dy
            if abs(dy) < 10:
                dy = 10 if dy >= 0 else -10
        else:
            if cal.col_slot <= 0:
                logger.error(f"drag grid: align 列数据无效: {scene_key}.{panel_key}")
                return
            step_norm = cal.col_slot + cal.col_span / 2.0
            panel_pixel_w = panel_obj.w_ratio * canvas.w_ratio * w
            dx = int(step_norm * panel_pixel_w * distance)
            if direction == "left":
                dx = -dx
            if abs(dx) < 10:
                dx = 10 if dx >= 0 else -10
        self._input.drag_screen(
            x, y, x + dx, y + dy,
            f"grid({scene_key}.{panel_key}) {direction} {distance}",
            hold=hold,
        )
        # drag 后失效缓存，下次访问时懒加载重新对齐
        self._invalidate_align(scene_key, panel_key)

    def get_panel_rows(self, scene_key: str, panel_key: str) -> int:
        """返回 panel 实际检测到的行数"""
        cal = self._ensure_aligned(scene_key, panel_key)
        return cal.n_rows if cal else 0

    def get_panel_cols(self, scene_key: str, panel_key: str) -> int:
        """返回 panel 实际检测到的列数"""
        cal = self._ensure_aligned(scene_key, panel_key)
        return cal.n_cols if cal else 0
