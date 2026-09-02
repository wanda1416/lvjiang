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

_NAV_FILE = "subcall/navigation.wf"
_PAGE_DETECTION_FILE = "subcall/page_detection.wf"
_RECYCLE_ENTRY_FIELDS = [
    "sub_func_1", "sub_func_2", "sub_func_3", "sub_func_4",
]


class TuningRouteStrategy(ABC):
    """调律流程涉及的页面路径。"""

    env: str

    def __init__(self, wf: RouteHostPort):
        self._wf = wf

    def load_dependencies(self) -> None:
        """加载导航与回收失败后的页面状态判断，不加载通用页面动作。"""
        engine = self._require_engine("load_dependencies")
        engine.load_subcalls(_NAV_FILE)
        engine.load_subcalls(_PAGE_DETECTION_FILE)

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
        """打开装备回收确认弹窗。返回 False 表示当前环境未找到入口。"""
        raise NotImplementedError

    def try_confirm_recycle_dialog(self) -> bool:
        """识别并确认回收专用弹窗，不改变通用页面确认方法的语义。"""
        values = self._wf.ocr_scene(
            self._wf.CONTROL_SCENE, ["confirm", "cancel"])
        confirm = values.get("confirm", "") or ""
        cancel = values.get("cancel", "") or ""
        visible = (
            "确认" in confirm
            or "确定" in confirm
            or "取消" in cancel
        )
        if not visible:
            logger.warning(
                "回收确认弹窗未识别到确认或取消："
                f"confirm={confirm!r}, cancel={cancel!r}")
            return False
        self._wf.click_region(self._wf.CONTROL_SCENE, "confirm")
        return True

    def is_in_bag_page(self) -> bool:
        """复用页面状态库判断当前是否已经回到背包页。"""
        return self._call_subcall("is_in_bag_page") == 1

    @abstractmethod
    def close_recycle_entry_on_lock(self) -> None:
        """回收确认弹窗判定装备锁定后，恢复本环境回收入口的 UI 状态。"""
        raise NotImplementedError

    def close_equipment_detail(self) -> None:
        """关闭当前装备详情；移动端无需额外动作。"""
        return None

    def open_reset_dialog(self) -> None:
        """通过当前布局定义的动作打开重置调律弹窗。"""
        self._wf.click_region(self._wf.TUNE_SCENE, "reset_tune")

    def confirm_reset(self, region: str) -> None:
        """通过当前布局定义的动作确认重置。"""
        if region == "confirm":
            self._wf.click_region(self._wf.CONTROL_SCENE, "confirm")
        else:
            self._wf.click_region(self._wf.TUNE_SCENE, region)

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
        # "回收" 是要在游戏截屏 OCR 结果里找的按钮文字，恒为中文，
        # 不能过 tr()（英文界面下会拿翻译后的英文去匹配中文截屏，
        # 永远匹配不上，回收功能会在英文界面下完全失效）。
        key = wf.ocr_scene_by(
            wf.EQUIP_DETAIL,
            _RECYCLE_ENTRY_FIELDS,
            "回收", "contains")
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
        # 桌面端回收始终由 X 触发；按键本身无副作用，无需先 OCR
        # 判断功能区是否存在「回收」。是否真正打开回收弹窗，由
        # TuningRecycler 紧接着联合识别通用确认区域中的确认/取消判定。
        wf = self._wf
        logger.info("  [桌面端] 按 X 回收")
        wf.press("X", wait=None)
        wf.wait_stable("page_refresh")  # 回收确认弹窗
        return True

    def close_recycle_entry_on_lock(self) -> None:
        # 桌面端被锁定时不出现确认弹窗，且 X 已经退出装备详情；这里只等待
        # 页面稳定，调用方不得再补 ESC。
        self._wf.wait_delay("step_interval")

    def close_equipment_detail(self) -> None:
        # 端游装备详情覆盖在背包网格上。必须先 ESC 关闭再继续对齐/点击；
        # 偏移点击只能绕开局部遮挡，无法恢复被遮罩破坏的网格轮廓。
        logger.info("  [桌面端] 按 ESC 关闭装备详情")
        self._wf.press("ESC", wait=None)
        self._wf.wait_stable("page_refresh")


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
