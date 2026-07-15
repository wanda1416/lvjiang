"""工作流基类 - 提供运行时状态和所有操作能力

子类可以：
1. 纯 Python 实现：直接重写 run() 方法
2. DSL 驱动：在 run() 中调用 self.run_file(wf_path)

所有操作（点击、截图、OCR、等待、变量管理）均在此类中实现，
子类可直接使用，无需重复编写。
"""

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
from ..core.region_config import Layout, Region
from . import builtins  # noqa: F401  触发内置函数注册


class BaseWorkflow:
    """工作流基类

    运行时状态：
    - last_scan: 上次 scan 的 OCR 结果 (dict)
    - last_scan_scene: 上次 scan 的场景 key
    - output: 收集的输出数据
    - variables: 用户变量表（eval 赋值、scan 结果自动存入 last_scan）
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
        self.output: dict = {}
        self.variables: dict = {}

    def run(self) -> dict | str:
        """执行工作流（子类重写）

        Returns:
            dict: collect_as 累积结果
            str: collect 直接输出 或 错误信息
        """
        raise NotImplementedError

    def reset_state(self):
        """重置运行时状态（在 run 开始前调用）"""
        self.last_scan = {}
        self.last_scan_scene = ""
        self.output = {}
        self.variables = {}

    @property
    def is_stopped(self) -> bool:
        """是否请求了停止"""
        return self._stop_check()

    # ─── DSL 便捷入口 ──────────────────────────────────────

    def run_file(self, workflow_path: Path | str) -> dict | str:
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

    # ─── 数据收集 ──────────────────────────────────────────

    def collect_current(self) -> dict:
        """返回当前 last_scan 的副本"""
        return dict(self.last_scan)

    def collect_as(self, key: str):
        """将当前 last_scan 存入 output"""
        self.output[key] = dict(self.last_scan)
        logger.debug(f"collect_as: {key} = {self.last_scan}")

    # ─── 变量与函数调用 ────────────────────────────────────

    def get_variable(self, name: str):
        """获取变量值"""
        return self.variables.get(name)

    def set_variable(self, name: str, value):
        """设置变量"""
        self.variables[name] = value

    def call_function(self, func_name: str, args: list) -> any:
        """调用内置函数"""
        fn = builtins.get_function(func_name)
        if fn is None:
            available = ", ".join(builtins.list_functions())
            raise ValueError(f"未知内置函数: {func_name}，可用函数: {available}")
        return fn(*args)

    def eval_condition(self, condition: dict) -> bool:
        """求值 DSL 条件表达式

        condition 格式（由 parser 生成）：
            {"func": name, "args": [...], "negated": bool}
            {"var": name, "negated": bool}
        """
        negated = condition.get("negated", False)

        if "func" in condition:
            resolved = self._resolve_args(condition["args"])
            result = self.call_function(condition["func"], resolved)
            value = bool(result)
        elif "var" in condition:
            value = bool(self.variables.get(condition["var"]))
        else:
            logger.error(f"无法识别的条件: {condition}")
            value = False

        return (not value) if negated else value

    def resolve_args(self, func_args: list[tuple[str, str]]) -> list:
        """解析函数参数：变量引用 → 运行时值，字面量 → 原值"""
        return self._resolve_args(func_args)

    def _resolve_args(self, func_args: list[tuple[str, str]]) -> list:
        resolved = []
        for kind, value in func_args:
            if kind == "var":
                resolved.append(self.variables.get(value))
            else:
                resolved.append(value)
        return resolved

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
