"""工作流基类 - 提供公共的坐标计算、点击、OCR 方法"""

import random

from loguru import logger

from typing import Callable, Optional

from ..config import DelayConfig
from ..core.capture import ScreenCapture
from ..core.ocr import OCREngine
from ..core.input import InputController
from ..core.region_config import Layout, Region


class WorkflowBase:
    """工作流基类
    
    提供：
    - 区域坐标计算（画布变换 + 可选抖动）
    - 区域点击（委托 InputController）
    - 场景 OCR
    - 停止检查
    """

    def __init__(
        self,
        capture: ScreenCapture,
        ocr: OCREngine,
        input_ctrl: InputController,
        layout: Layout,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Optional[Callable[[], bool]] = None,
        delay_config: DelayConfig | None = None,
    ):
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)
        self._delay = delay_config or DelayConfig()

    def _click_region(self, scene_key: str, field_key: str, jitter: bool = True):
        """点击指定场景中指定区域（带可选的区域级随机抖动）
        
        Args:
            scene_key: 场景 key
            field_key: 区域 key
            jitter: 是否启用区域级随机抖动（默认 True）
        """
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == field_key), None)
        if region is None:
            logger.error(f"场景 {scene_key} 没有定义区域: {field_key}")
            return

        screen_x, screen_y = self._region_to_screen(region, jitter)
        if screen_x is None:
            return

        logger.debug(f"点击: {scene_key}/{field_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{field_key}")

    def _region_to_screen(self, region: Region, jitter: bool = True) -> tuple[int | None, int | None]:
        """将区域坐标转换为屏幕坐标（带可选抖动）
        
        Args:
            region: 区域对象
            jitter: 是否启用区域级随机抖动
            
        Returns:
            (screen_x, screen_y)，截图失败时返回 (None, None)
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return None, None

        h, w = img.shape[:2]
        canvas = self._layout.get_canvas()

        # 画布变换：画布比例 → 截图像素
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        # 区域中心（截图像素）
        cx = canvas_x + (region.x_ratio + region.w_ratio / 2) * canvas_w
        cy = canvas_y + (region.y_ratio + region.h_ratio / 2) * canvas_h

        # 可选的区域级抖动（在中心 ±region_jitter_ratio 范围内随机取点）
        if jitter:
            jitter_ratio = self._delay.region_jitter_ratio
            region_w = region.w_ratio * canvas_w
            region_h = region.h_ratio * canvas_h
            cx += region_w * random.uniform(-jitter_ratio, jitter_ratio)
            cy += region_h * random.uniform(-jitter_ratio, jitter_ratio)

        # 窗口偏移 → 屏幕坐标
        screen_x = int(self._window_left + cx)
        screen_y = int(self._window_top + cy)

        return screen_x, screen_y

    def _ocr_scene(self, scene_key: str) -> dict[str, str]:
        """截图并对指定场景做 OCR
        
        Returns:
            dict，key 为字段 key，value 为 OCR 文本
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}

        return self._ocr.ocr_scene_regions(img, canvas, regions, scene_key)
