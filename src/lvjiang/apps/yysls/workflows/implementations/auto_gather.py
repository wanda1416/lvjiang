"""桌面标记采集专有任务；录制试跑和正式回放共用状态机。"""
from __future__ import annotations

import time
from copy import deepcopy
from typing import Callable

from loguru import logger

from lvjiang.apps.yysls.core.gather import (
    GatherRoute,
    GatherStep,
    crop_region,
    viewport_difference,
    viewport_signature,
)
from lvjiang.workflows.base import BaseWorkflow
from lvjiang.workflows.engine.signals import _BreakSignal


class AutoGatherWorkflow(BaseWorkflow):
    DISPLAY_NAME = "自动采集（实验性）"
    SCOPE = "dedicated"
    ENV = ["desktop"]
    NOTE = "固定起点的标记路线，单轮执行；请在采集页启动。"

    def configure(
        self, route: GatherRoute, *, selected_target: bool = False,
        progress: Callable[[str], None] | None = None,
        completed: Callable[[dict], None] | None = None,
    ) -> None:
        route.validate(runnable=True)
        self.route = deepcopy(route)
        self._layout = deepcopy(self._layout)
        self.selected_target = selected_target
        self.progress = progress or (lambda message: None)
        self.completed = completed or (lambda result: None)

    def _checkpoint(self) -> None:
        before = time.monotonic()
        self._wait_if_paused()
        self._paused_seconds += time.monotonic() - before
        if self._stop_check():
            raise _BreakSignal()

    def _now(self) -> float:
        return time.monotonic() - self._paused_seconds

    def _report(self, state: str, message: str) -> None:
        self._checkpoint()
        event = {"step": self._step_number, "state": state, "message": message}
        self.output["events"].append(event)
        logger.info(f"[采集 {self._step_number}/{len(self.route.steps)}] {message}")
        self.progress(message)

    def _frame(self):
        self._checkpoint()
        frame = self.capture_frame(source="gather")
        if frame is None:
            raise ValueError("采集截图失败，请检查窗口连接")
        return frame

    def _text(self, frame, key: str) -> str:
        self._checkpoint()
        return self._ocr.ocr_single(crop_region(frame, self._layout, key)).replace(" ", "")

    def _is_map(self, frame) -> bool:
        text = self._text(frame, "travel")
        return "识途" in text or "前往" in text

    def _is_home(self, frame) -> bool:
        text = self._text(frame, "home_controls").upper()
        return "TAB" in text and "NUM" in text

    def _sleep(self, seconds: float) -> None:
        # 小段等待，把暂停耗时排除在到达预算之外，同时及时响应停止。
        end = self._now() + seconds
        while self._now() < end:
            self._checkpoint()
            time.sleep(min(0.1, max(0, end - self._now())))
        self._checkpoint()

    def _press(self, key: str) -> None:
        self._checkpoint()
        self.press(key, wait=None)

    def _ensure_map(self):
        frame = self._frame()
        if self._is_map(frame):
            return frame
        if not self._is_home(frame):
            raise ValueError("未识别到采集地图或游戏主页，请手动打开目标资源的采集地图")
        self._press(self.route.map_key)
        deadline = self._now() + 10
        while self._now() < deadline:
            self._sleep(0.5)
            frame = self._frame()
            if self._is_map(frame):
                self._sleep(0.8)
                return self._frame()
        raise ValueError("打开采集地图超时，请确认开图键及资源筛选")

    def _arrive(self, step: GatherStep) -> float:
        self._report("travel", "发起识途并确认前往")
        self._press(self.route.travel_key)
        self._sleep(0.8)
        self._press(self.route.confirm_key)
        start = self._now()
        self._report("timing", f"按录制时间等待 {step.travel_seconds:.1f} 秒")
        self._sleep(step.travel_seconds)
        deadline = start + max(step.travel_seconds, self.route.travel_timeout)
        while self._now() < deadline:
            frame = self._frame()
            if self._is_home(frame):
                self._report("arrived", "已到达录制位置，准备采集")
                return self._now() - start
            self._sleep(0.5)
        raise ValueError("识途到达超时：可能无法识途或途中受阻。路线已停止，请恢复起点后重试")

    def run(self) -> dict:
        self._paused_seconds = 0.0
        self._step_number = 0
        self.output = {"events": [], "steps": []}
        try:
            return self._run_route()
        except ValueError as exc:
            # 宿主的 error 结果契约会保存完整输出并将任务标记为失败。
            # 避免异常包装只留下类名，使历史丢失可行动的失败原因。
            message = str(exc)
            self.output["error"] = message
            self.output["events"].append({"step": self._step_number, "state": "failed", "message": message})
            logger.error(f"[采集] {message}")
            getattr(self, "progress", lambda message: None)(message)
            return self.output

    def _run_route(self) -> dict:
        if self.engine is None or self.engine.run_env != "desktop":
            raise ValueError("实验性采集目前仅支持桌面游戏环境")
        if not hasattr(self, "route"):
            raise ValueError("请通过采集页选择路线后启动")
        self.route.validate(runnable=True)
        # 启动前检查全部识别区域，避免走到半程才发现缺少标定。
        frame = self._frame()
        required = ["travel", "home_controls"]
        if any(step.viewport for step in self.route.steps):
            required.append("map_view")
        for key in required:
            crop_region(frame, self._layout, key)
        for index, step in enumerate(self.route.steps, 1):
            self._step_number = index
            self._report("map", "检查采集地图")
            frame = self._ensure_map()
            if not self.selected_target:
                self._sleep(0.8)
                frame = self._frame()
                signature = (viewport_signature(crop_region(frame, self._layout, "map_view"))
                             if step.viewport else "")
                if step.viewport and viewport_difference(step.viewport, signature) > 0.04:
                    raise ValueError("地图视口与录制不符，未点击目标。请恢复起点、缩放和资源筛选")
                self._checkpoint()
                self.click_at(*self._ratio_to_screen(step.x, step.y), random_offset=False,
                              pre_delay=(0, 0), post_delay=(0, 0))
                self._sleep(1)
                if not self._is_map(self._frame()):
                    raise ValueError("点击后未识别到识途按钮，请重新录制该路线")
            travel = self._arrive(step)
            self._report("collect", "触发采集按键，等待采集动作")
            self._press(self.route.gather_key)
            self._sleep(self.route.gather_seconds)
            self._ensure_map()
            result = {"index": index, "travel_seconds": round(travel, 2),
                      "collection_triggered": True}
            self.output["steps"].append(result)
            self._report("done", "本点采集按键已执行，地图已打开")
        self.output["route"] = {"key": self.route.key, "name": self.route.name}
        self.completed(deepcopy(self.output))
        return self.output
