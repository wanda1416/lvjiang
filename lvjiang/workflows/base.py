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
    - variables: 用户变量表（scan/eval 赋值，所有数据显式存储）
    - output: 收集的输出数据字典（由 collect 语句写入，key 为 alias 或变量名）
    """

    # 类级别共享：MaterialRecognizer 跨所有实例复用，避免重复加载参考图
    _shared_material_recognizer = None

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
        self.output: dict = {}  # collect 语句写入的输出字典
        self.variables: dict = {}

    def run(self) -> dict:
        """执行工作流（子类重写）

        Returns:
            dict: collect 累积结果
        """
        raise NotImplementedError

    def reset_state(self):
        """重置运行时状态（在 run 开始前调用）"""
        self.output = {}
        self.variables = {}

    @property
    def is_stopped(self) -> bool:
        """是否请求了停止"""
        return self._stop_check()

    # ─── DSL 便捷入口 ──────────────────────────────────────

    def run_file(self, workflow_path: Path | str, initial_variables: dict | None = None) -> dict:
        """加载并执行 .wf 文件（DSL 驱动的工作流使用）

        Args:
            workflow_path: .wf 文件路径
            initial_variables: 外部注入的初始变量（如 UI 参数面板传入的参数）
        """
        from .engine import WorkflowEngine
        engine = WorkflowEngine(self)
        if initial_variables:
            engine.variables.update(initial_variables)
        output = engine.run(workflow_path)
        # 同步 engine 最终状态回 wf，供外部调用者读取
        self.variables = dict(engine.variables)
        return output

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
            return {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}

        if field_keys:
            regions = [r for r in regions if r.key in field_keys]

        result = self._ocr.ocr_scene_regions(img, canvas, regions, scene_key)
        fields_display = field_keys if field_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.info(f"OCR [{scene_key}]:{fields_display} => {result}")
        return result

    def click_at(self, x: int, y: int):
        """点击屏幕绝对坐标"""
        logger.debug(f"点击坐标: ({x}, {y})")
        self._input.click_screen(x, y, "dynamic")

    # ─── 材料识别 ──────────────────────────────────────────

    @property
    def material_recognizer(self):
        """延迟构造 MaterialRecognizer（类级别共享，跨工作流运行复用）"""
        if BaseWorkflow._shared_material_recognizer is None:
            from ..core.material_recognizer import MaterialRecognizer
            BaseWorkflow._shared_material_recognizer = MaterialRecognizer(self._ocr)
        return BaseWorkflow._shared_material_recognizer

    def recognize_materials(
        self,
        scene_key: str,
        slot_keys: list[str] | None = None,
    ) -> tuple[dict[str, str], dict]:
        """对指定场景的每个 slot 执行材料识别

        Args:
            scene_key: 场景 key
            slot_keys: 可选，只识别指定 slot

        Returns:
            (result, region_map)
            result: {slot_key: material_type, ...}  空槽为 ""
            region_map: {slot_key: Region, ...}  供 coord_meta 存储
        """
        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            return {}, {}

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            return {}, {}

        if slot_keys:
            regions = [r for r in regions if r.key in slot_keys]

        # 建立 region_map（供 coord_meta 存储）
        region_map = {r.key: r for r in regions}

        # 逐 slot 裁切 + 识别
        result: dict[str, str] = {}
        h, w = img.shape[:2]
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        for region in regions:
            x1 = int(canvas_x + region.x_ratio * canvas_w)
            y1 = int(canvas_y + region.y_ratio * canvas_h)
            x2 = int(canvas_x + (region.x_ratio + region.w_ratio) * canvas_w)
            y2 = int(canvas_y + (region.y_ratio + region.h_ratio) * canvas_h)
            slot_img = img[y1:y2, x1:x2]

            if slot_img.size == 0:
                logger.warning(f"slot {region.key} 裁切为空，跳过")
                result[region.key] = ""
                continue

            info = self.material_recognizer.recognize(slot_img)
            result[region.key] = info.type  # 空槽 info.type == ""
            logger.debug(
                f"材料识别 [{scene_key}].[{region.key}]: "
                f"type={info.type!r} level={info.level} count={info.count}"
            )

        fields_display = slot_keys if slot_keys else [r.key for r in self._layout.get_scene_regions(scene_key)]
        logger.info(f"材料识别 [{scene_key}]:{fields_display} => {result}")
        return result, region_map

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
        w, h = self._capture.get_capture_size()
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
        size = self._capture.get_capture_size()
        if size == (0, 0):
            logger.error("无法获取截屏尺寸")
            return None, None
        w, h = size
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
        size = self._capture.get_capture_size()
        if size == (0, 0):
            logger.error("无法获取截屏尺寸")
            return None, None
        w, h = size
        canvas = self._layout.get_canvas()
        sx = canvas.x_ratio + cx_ratio * canvas.w_ratio
        sy = canvas.y_ratio + cy_ratio * canvas.h_ratio
        return int(self._window_left + sx * w), int(self._window_top + sy * h)
