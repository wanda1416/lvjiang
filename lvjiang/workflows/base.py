"""工作流基类 - 提供运行时状态和所有操作能力

子类可以：
1. 纯 Python 实现：直接重写 run() 方法
2. DSL 驱动：在 run() 中调用 self.run_file(wf_path)

所有操作（点击、截图、OCR、等待、变量管理）均在此类中实现，
子类可直接使用，无需重复编写。
"""

import math
import random
import time
import traceback

from loguru import logger
from pathlib import Path
from typing import Callable, Optional

from ..config import DelayConfig
from ..core.capture import ScreenCapture
from ..core.ocr import OCREngine
from ..core.input import InputController
from ..core.region_config import Layout, Point, Region
from . import builtins  # noqa: F401  触发内置函数注册


class BaseWorkflow:
    """工作流基类

    运行时状态：
    - last_scan: 上次 scan 的 OCR 结果 (dict)
    - last_scan_scene: 上次 scan 的场景 key
    - output: 收集的输出数据列表（由 collect 语句追加）
    - variables: 用户变量表（scan/convert/eval 赋值）
    """

    def __init__(
        self,
        capture: ScreenCapture,
        ocr: OCREngine,
        input_ctrl: InputController,
        layout: Layout,
        delay_config: DelayConfig | None = None,
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Optional[Callable[[], bool]] = None,
    ):
        self._capture = capture
        self._ocr = ocr
        self._input = input_ctrl
        self._layout = layout
        self._delay = delay_config or DelayConfig()
        self._window_left = window_left
        self._window_top = window_top
        self._stop_check = stop_check or (lambda: False)

        # 运行时状态
        self.last_scan: dict[str, str] = {}
        self.last_scan_scene: str = ""
        self.output: list = []  # collect 语句追加的输出列表
        self.variables: dict = {}

    def run(self) -> list:
        """执行工作流（子类重写）

        Returns:
            list: collect 累积结果
        """
        raise NotImplementedError

    def reset_state(self):
        """重置运行时状态（在 run 开始前调用）"""
        self.last_scan = {}
        self.last_scan_scene = ""
        self.output = []
        self.variables = {}

    @property
    def is_stopped(self) -> bool:
        """是否请求了停止"""
        return self._stop_check()

    # ─── DSL 便捷入口 ──────────────────────────────────────

    def run_file(self, workflow_path: Path | str) -> list:
        """加载并执行 .wf 文件（DSL 驱动的工作流使用）"""
        from .engine import WorkflowEngine
        engine = WorkflowEngine(self)
        return engine.run(workflow_path)

    # ─── 点击操作 ──────────────────────────────────────────

    def click_region(self, scene_key: str, field_key: str, jitter: bool = True):
        """点击指定场景中指定区域的中心（带随机抖动）"""
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

    # ─── 截图与 OCR ────────────────────────────────────────

    def ocr_scene(self, scene_key: str, field_keys: list[str] | None = None) -> dict[str, str]:
        """对指定场景执行截图 + OCR

        Args:
            scene_key: 场景 key
            field_keys: 可选，只 OCR 指定字段列表

        Returns:
            {field_key: ocr_text, ...}
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            self.last_scan = {}
            self.last_scan_scene = ""
            return {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            self.last_scan = {}
            self.last_scan_scene = ""
            return {}

        if field_keys:
            regions = [r for r in regions if r.key in field_keys]

        result = self._ocr.ocr_scene_regions(img, canvas, regions, scene_key)
        self.last_scan = result
        self.last_scan_scene = scene_key
        self.variables["last_scan"] = result
        logger.info(f"OCR [{scene_key}]: {result}")
        return result

    def click_match_text(self, target_text: str, error_msg: str | None = None) -> str | None:
        """在上次 OCR 结果中找包含目标文字的区域并点击

        Returns:
            None: 成功
            str: 错误信息
        """
        if not self.last_scan:
            msg = "click_match 前没有 scan 结果"
            logger.error(msg)
            return f"(错误: {msg})"

        matched_key = None
        for key, text in self.last_scan.items():
            if target_text in text:
                matched_key = key
                logger.debug(f"  匹配: {key} = {text!r} 包含 {target_text!r}")
                break

        if matched_key is None:
            msg = error_msg or f"未找到包含 {target_text!r} 的区域"
            logger.error(f"{msg}，OCR 结果: {self.last_scan}")
            return f"(错误: {msg})"

        logger.info(f"click_match: 找到 {matched_key}，点击")
        self.click_region(self.last_scan_scene, matched_key)
        return None

    # ─── 等待 ──────────────────────────────────────────────

    def wait_delay(self, delay_name: str):
        """按命名延迟参数等待"""
        delay_val = getattr(self._delay, delay_name, None)
        if delay_val is None:
            logger.error(f"未知的延迟参数: {delay_name}")
            return
        if isinstance(delay_val, tuple):
            actual = random.uniform(*delay_val)
        else:
            actual = float(delay_val)
        logger.debug(f"等待 {delay_name} = {actual:.2f}s")
        time.sleep(actual)

    def wait_seconds(self, seconds: float):
        """固定等待"""
        logger.debug(f"等待 {seconds}s")
        time.sleep(seconds)

    # ─── 变量与函数调用 ────────────────────────────────────

    def get_variable(self, name: str):
        """获取变量值"""
        return self.variables.get(name)

    def set_variable(self, name: str, value):
        """设置变量"""
        self.variables[name] = value

    def call_function(self, func_name: str, args: list) -> any:
        """调用内置函数

        若函数第一参数名为 _wf，自动注入 workflow 实例作为上下文。
        """
        fn = builtins.get_function(func_name)
        if fn is None:
            available = ", ".join(builtins.list_functions())
            raise ValueError(f"未知内置函数: {func_name}，可用函数: {available}")
        # 检查函数是否需要 workflow context（第一参数名为 _wf）
        import inspect
        sig = inspect.signature(fn)
        params = list(sig.parameters.keys())
        if params and params[0] == '_wf':
            return fn(self, *args)
        return fn(*args)

    # ─── 坐标计算 ──────────────────────────────────────────

    def _region_to_screen(self, region: Region, jitter: bool = True) -> tuple[int | None, int | None]:
        """区域坐标 → 屏幕坐标"""
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return None, None

        h, w = img.shape[:2]
        canvas = self._layout.get_canvas()

        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        cx = canvas_x + (region.x_ratio + region.w_ratio / 2) * canvas_w
        cy = canvas_y + (region.y_ratio + region.h_ratio / 2) * canvas_h

        if jitter:
            jitter_ratio = self._delay.region_jitter_ratio
            region_w = region.w_ratio * canvas_w
            region_h = region.h_ratio * canvas_h
            cx += region_w * random.uniform(-jitter_ratio, jitter_ratio)
            cy += region_h * random.uniform(-jitter_ratio, jitter_ratio)

        return int(self._window_left + cx), int(self._window_top + cy)

    # ─── Point / Arrow 操作 ────────────────────────────────

    def click_any(self, scene_key: str, key: str):
        """点击 region 或 point（自动识别，region 优先）"""
        regions = self._layout.get_scene_regions(scene_key)
        region = next((r for r in regions if r.key == key), None)
        if region is not None:
            self.click_region(scene_key, key)
            return
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == key), None)
        if point is not None:
            self.click_point(scene_key, key)
            return
        logger.error(f"场景 {scene_key} 没有定义 region 或 point: {key}")

    def click_point(self, scene_key: str, point_key: str):
        """点击 point 中心（带半径内随机偏移）"""
        points = self._layout.get_scene_points(scene_key)
        point = next((p for p in points if p.key == point_key), None)
        if point is None:
            logger.error(f"场景 {scene_key} 没有定义 point: {point_key}")
            return
        screen_x, screen_y = self._point_to_screen(point)
        if screen_x is None:
            return
        logger.debug(f"点击 point: {scene_key}/{point_key} -> 屏幕({screen_x},{screen_y})")
        self._input.click_screen(screen_x, screen_y, f"{scene_key}/{point_key}")

    def drag_arrow(self, scene_key: str, arrow_key: str, duration: float | tuple[float, float] | None = None, hold: float | None = None):
        """执行 arrow 定义的拖拽

        Args:
            duration: 拖拽移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认值。
            hold: 到达目标后按住不放的时长（秒）。None 表示不按住。
        """
        arrows = self._layout.get_scene_arrows(scene_key)
        arrow = next((a for a in arrows if a.key == arrow_key), None)
        if arrow is None:
            logger.error(f"场景 {scene_key} 没有定义 arrow: {arrow_key}")
            return
        points = self._layout.get_scene_points(scene_key)
        from_point = next((p for p in points if p.key == arrow.from_key), None)
        if from_point is None:
            logger.error(f"arrow {arrow_key} 的起点 point {arrow.from_key} 未定义")
            return
        # 终点：吸附态动态查 point，绝对态直接用坐标
        if arrow.to_key is not None:
            to_point = next((p for p in points if p.key == arrow.to_key), None)
            if to_point is None:
                logger.error(f"arrow {arrow_key} 的终点 point {arrow.to_key} 未定义")
                return
            to_cx, to_cy = to_point.cx_ratio, to_point.cy_ratio
        else:
            to_cx, to_cy = arrow.to_cx_ratio, arrow.to_cy_ratio
        fx, fy = self._point_to_screen(from_point)
        tx, ty = self._ratio_to_screen(to_cx, to_cy)
        if fx is None or tx is None:
            return
        logger.debug(f"拖拽 arrow: {scene_key}/{arrow_key} ({fx},{fy})->({tx},{ty})" + (f" hold {hold}s" if hold else ""))
        self._input.drag_screen(fx, fy, tx, ty, f"{scene_key}/{arrow_key}", duration=duration, hold=hold)

    def _point_to_screen(self, point: Point) -> tuple[int | None, int | None]:
        """point 中心 → 屏幕坐标（带半径内随机偏移）"""
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return None, None
        h, w = img.shape[:2]
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        cx = canvas_x + point.cx_ratio * canvas_w
        cy = canvas_y + point.cy_ratio * canvas_h
        # 半径内随机偏移
        r = point.r_ratio * min(canvas_w, canvas_h)
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(0, r)
        cx += dist * math.cos(angle)
        cy += dist * math.sin(angle)
        return int(self._window_left + cx), int(self._window_top + cy)

    def _ratio_to_screen(self, cx_ratio: float, cy_ratio: float) -> tuple[int | None, int | None]:
        """画布内归一化坐标 → 屏幕坐标"""
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return None, None
        h, w = img.shape[:2]
        canvas = self._layout.get_canvas()
        sx = canvas.x_ratio + cx_ratio * canvas.w_ratio
        sy = canvas.y_ratio + cy_ratio * canvas.h_ratio
        return int(self._window_left + sx * w), int(self._window_top + sy * h)
