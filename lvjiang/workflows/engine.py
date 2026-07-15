"""工作流 DSL 引擎 - 解析并执行 .wf 文件"""

import random
import time

from loguru import logger
from pathlib import Path
from typing import Callable, Optional

from ..config import DelayConfig
from ..core.capture import ScreenCapture
from ..core.ocr import OCREngine
from ..core.input import InputController
from ..core.region_config import Layout, Region
from .parser import Step, parse_file


class WorkflowEngine:
    """DSL 工作流引擎
    
    解析 .wf 文件并逐条执行指令。
    维护两个运行时状态：
    - _last_scan: 上次 scan 的 OCR 结果 (dict)
    - _last_scan_scene: 上次 scan 的场景 key（供 click_match 定位区域）
    - _output: 收集的输出数据
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
        self._last_scan: dict[str, str] = {}
        self._last_scan_scene: str = ""
        self._output: dict = {}

    def run(self, workflow_path: Path | str) -> dict | str:
        """加载并执行工作流文件
        
        Returns:
            collect 模式: 返回最后 scan 结果（dict 或 str）
            collect_as 模式: 返回累积的输出字典
        """
        workflow_path = Path(workflow_path)
        steps = parse_file(workflow_path)
        logger.info(f"=== 工作流开始: {workflow_path.stem} ({len(steps)} 步) ===")

        # 重置运行时状态
        self._last_scan = {}
        self._last_scan_scene = ""
        self._output = {}
        direct_output = None  # collect 模式直接输出

        for i, step in enumerate(steps):
            if self._stop_check():
                logger.info("工作流被用户停止")
                return "(已停止)"

            logger.debug(f"  步骤 {i+1}/{len(steps)}: {step.instruction} {step.args}")

            match step.instruction:
                case "click":
                    self._exec_click(step)
                case "wait":
                    self._exec_wait(step)
                case "scan":
                    self._exec_scan(step)
                case "click_match":
                    result = self._exec_click_match(step)
                    if result is not None:
                        return result
                case "collect":
                    direct_output = dict(self._last_scan)
                case "collect_as":
                    self._exec_collect_as(step)
                case "log":
                    self._exec_log(step)

        # 返回结果
        if direct_output is not None:
            logger.info(f"=== 工作流完成 ===")
            return direct_output
        if self._output:
            logger.info(f"=== 工作流完成，收集到 {len(self._output)} 项数据 ===")
            return self._output
        logger.info(f"=== 工作流完成 ===")
        return {}

    # ─── 指令执行 ──────────────────────────────────────────

    def _exec_click(self, step: Step):
        """click [scene].[field]"""
        scene_key = step.args["scene"]
        field_key = step.args["field"]
        self._click_region(scene_key, field_key)

    def _exec_wait(self, step: Step):
        """wait <delay_name> | <seconds>"""
        if "seconds" in step.args:
            seconds = step.args["seconds"]
            logger.debug(f"等待 {seconds}s")
            time.sleep(seconds)
        else:
            delay_name = step.args["delay_name"]
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

    def _exec_scan(self, step: Step):
        """scan [scene] 或 scan [scene].[f1, f2, ...]"""
        scene_key = step.args["scene"]
        field_keys = step.args.get("fields")

        img = self._capture.capture()
        if img is None:
            logger.error("截图失败")
            self._last_scan = {}
            self._last_scan_scene = ""
            return

        canvas = self._layout.get_canvas()
        regions = self._layout.get_scene_regions(scene_key)
        if not regions:
            logger.warning(f"场景 {scene_key} 没有定义区域")
            self._last_scan = {}
            self._last_scan_scene = ""
            return

        # 如果指定了字段，只 OCR 这些字段
        if field_keys:
            regions = [r for r in regions if r.key in field_keys]

        self._last_scan = self._ocr.ocr_scene_regions(img, canvas, regions, scene_key)
        self._last_scan_scene = scene_key
        logger.info(f"OCR [{scene_key}]: {self._last_scan}")

    def _exec_click_match(self, step: Step) -> Optional[str]:
        """click_match "text" [error "msg"]
        
        在上次 scan 结果中找 OCR 文本包含目标文字的区域并点击。
        返回 None 表示成功，返回错误字符串表示失败。
        """
        target_text = step.args["text"]
        error_msg = step.args.get("error_msg")

        if not self._last_scan:
            msg = "click_match 前没有 scan 结果"
            logger.error(msg)
            return f"(错误: {msg})"

        # 查找匹配
        matched_key = None
        for key, text in self._last_scan.items():
            if target_text in text:
                matched_key = key
                logger.debug(f"  匹配: {key} = {text!r} 包含 {target_text!r}")
                break

        if matched_key is None:
            msg = error_msg or f"未找到包含 {target_text!r} 的区域"
            logger.error(f"{msg}，OCR 结果: {self._last_scan}")
            return f"(错误: {msg})"

        logger.info(f"click_match: 找到 {matched_key}，点击")
        self._click_region(self._last_scan_scene, matched_key)
        return None

    def _exec_collect_as(self, step: Step):
        """collect_as <key>"""
        key = step.args["key"]
        self._output[key] = dict(self._last_scan)
        logger.debug(f"collect_as: {key} = {self._last_scan}")

    def _exec_log(self, step: Step):
        """log "message" """
        logger.info(step.args["message"])

    # ─── 坐标计算与点击（复用 WorkflowBase 逻辑） ──────────

    def _click_region(self, scene_key: str, field_key: str, jitter: bool = True):
        """点击指定场景中指定区域"""
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
