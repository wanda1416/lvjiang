"""调律页面路径策略。

业务编排只描述要到达的页面；不同运行环境如何到达，由策略负责。
现有长路径仍复用 navigation.wf，后续可以逐条迁入各环境策略而不改编排层。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from lvjiang.apps.yysls.workflows.implementations.tuning.ports import (
    RouteHostPort,
    SubcallEnginePort,
)

from ......i18n import tr

_NAV_FILE = "subcall/navigation.wf"


class TuningRouteStrategy(ABC):
    """调律流程涉及的页面路径。"""

    env: str

    def __init__(self, wf: RouteHostPort):
        self._wf = wf

    def load_dependencies(self) -> None:
        engine = self._require_engine("load_dependencies")
        engine.load_subcalls(_NAV_FILE)

    def enter_equip(self) -> bool:
        result = self._call_subcall("nav_main_to_equip")
        return self._subcall_succeeded(result)

    def return_main(self) -> bool:
        result = self._call_subcall("nav_back_to_main")
        return self._subcall_succeeded(result)

    def enter_tune_detail(self) -> bool:
        result = self._call_subcall("nav_equip_to_tune")
        return self._subcall_succeeded(result)

    def leave_tune_detail(self) -> None:
        """离开调律页，并将装备详情页恢复到可继续操作的状态。"""
        self._wf.click_region(self._wf.TUNE_SCENE, "back")
        self._wf.wait_stable("page_refresh")

    @abstractmethod
    def open_recycle_dialog(self) -> bool:
        """打开装备回收确认弹窗。返回 True=已打开；False=未找到入口（装备保留）。"""
        raise NotImplementedError

    @abstractmethod
    def close_recycle_entry_on_lock(self) -> None:
        """回收确认弹窗判定装备锁定后，恢复本环境回收入口的 UI 状态。"""
        raise NotImplementedError

    def grid_click_x_ratio(self, col: int) -> float:
        """背包格子内的横向点击比例（0~1）；默认居中点击。"""
        return 0.5

    def _require_engine(self, operation: str) -> SubcallEnginePort:
        engine = self._wf.engine
        if engine is None:
            raise RuntimeError(
                f"{type(self).__name__}.{operation}: 未注入 WorkflowEngine，"
                "导航 subcall 无法执行")
        return engine

    def _call_subcall(self, proc_name: str, args: list | None = None) -> Any:
        return self._require_engine(proc_name).call_subcall(proc_name, args)

    @staticmethod
    def _subcall_succeeded(result: Any) -> bool:
        """导航 DSL 约定：负数表示失败，非负数表示成功。"""
        return not (isinstance(result, (int, float)) and result < 0)


class AndroidTuningRouteStrategy(TuningRouteStrategy):
    env = "android"

    def leave_tune_detail(self) -> None:
        super().leave_tune_detail()
        # Android 通过「更多」进入调律，返回后弹窗仍保持展开。
        self._wf.click_region(self._wf.EQUIP_DETAIL, "more_func")
        self._wf.wait_delay("step_interval")

    def open_recycle_dialog(self) -> bool:
        # 经「更多」弹窗 → 子菜单「回收」
        wf = self._wf
        wf.click_region(wf.EQUIP_DETAIL, "more_func")
        wf.wait_stable("page_refresh")
        key = wf.ocr_scene_by(
            wf.EQUIP_DETAIL,
            ["sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4"],
            tr("回收"), "contains")
        if not key:
            logger.warning("未找到回收按钮，装备保留")
            wf.click_region(wf.EQUIP_DETAIL, "more_func")
            wf.wait_delay("step_interval")
            return False
        wf.click_region(wf.EQUIP_DETAIL, key)
        wf.wait_stable("page_refresh")  # 回收确认弹窗
        return True

    def close_recycle_entry_on_lock(self) -> None:
        wf = self._wf
        wf.click_region(wf.EQUIP_DETAIL, "more_func")
        wf.wait_delay("step_interval")


class DesktopTuningRouteStrategy(TuningRouteStrategy):
    env = "desktop"

    def open_recycle_dialog(self) -> bool:
        # 直接扫描功能区找「回收」→ 按 X 回收
        wf = self._wf
        key = wf.ocr_scene_by(wf.EQUIP_DETAIL, ["func_area"], tr("回收"), "contains")
        if not key:
            logger.warning("桌面端：未找到回收按钮，装备保留")
            return False
        logger.info("  [桌面端] 找到回收入口，按 X 回收")
        wf.press("X", wait=None)
        wf.wait_stable("page_refresh")  # 回收确认弹窗
        return True

    def close_recycle_entry_on_lock(self) -> None:
        # 桌面端被锁定不会出现任何现象，但也要等待
        self._wf.wait_delay("step_interval")

    def grid_click_x_ratio(self, col: int) -> float:
        # 第 1 列：装备详情弹窗的功能区域遮挡格子左半区，偏移到右 3/4 处避开遮挡
        return 0.75 if col == 1 else 0.5


def create_tuning_route_strategy(
        wf: RouteHostPort) -> TuningRouteStrategy:
    """按工作流启动时的环境快照创建路径策略。"""
    engine = wf.engine
    if engine is None:
        raise RuntimeError("未注入 WorkflowEngine，无法取得运行环境")
    env = engine.run_env
    strategies = {
        AndroidTuningRouteStrategy.env: AndroidTuningRouteStrategy,
        DesktopTuningRouteStrategy.env: DesktopTuningRouteStrategy,
    }
    try:
        strategy_type = strategies[env]
    except KeyError as exc:
        raise ValueError(f"不支持调律导航环境: {env!r}") from exc
    return strategy_type(wf)
