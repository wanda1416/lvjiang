"""调律页面路径策略。

业务编排只描述要到达的页面；不同运行环境如何到达，由策略负责。
现有长路径仍复用 navigation.wf，后续可以逐条迁入各环境策略而不改编排层。
"""

from __future__ import annotations

from abc import ABC
from typing import TYPE_CHECKING, Any

from lvjiang.core.config import session

if TYPE_CHECKING:
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


_NAV_FILE = "subcall/navigation.wf"


class TuningRouteStrategy(ABC):
    """调律流程涉及的页面路径。"""

    env: str

    def __init__(self, wf: AutoTuningWorkflow):
        self._wf = wf

    def load_dependencies(self) -> None:
        engine = self._require_engine("load_dependencies")
        engine.load_subcalls(_NAV_FILE)

    def enter_equip(self) -> None:
        self._call_subcall("nav_main_to_equip")

    def return_main(self) -> None:
        self._call_subcall("nav_back_to_main")

    def enter_tune_detail(self) -> bool:
        result = self._call_subcall("nav_equip_to_tune")
        return not (isinstance(result, (int, float)) and result < 0)

    def leave_tune_detail(self) -> None:
        """离开调律页，并将装备详情页恢复到可继续操作的状态。"""
        self._wf.click_region(self._wf.TUNE_SCENE, "back")
        self._wf.wait_stable("page_refresh")

    def _require_engine(self, operation: str):
        engine = self._wf.engine
        if engine is None:
            raise RuntimeError(
                f"{type(self).__name__}.{operation}: 未注入 WorkflowEngine，"
                "导航 subcall 无法执行")
        return engine

    def _call_subcall(self, proc_name: str, args: list | None = None) -> Any:
        return self._require_engine(proc_name).call_subcall(proc_name, args)


class AndroidTuningRouteStrategy(TuningRouteStrategy):
    env = "android"

    def leave_tune_detail(self) -> None:
        super().leave_tune_detail()
        # Android 通过「更多」进入调律，返回后弹窗仍保持展开。
        self._wf.click_region(self._wf.EQUIP_DETAIL, "more_func")
        self._wf.wait_delay("step_interval")


class DesktopTuningRouteStrategy(TuningRouteStrategy):
    env = "desktop"


def create_tuning_route_strategy(
        wf: AutoTuningWorkflow) -> TuningRouteStrategy:
    """按当前运行环境创建路径策略；环境只在这里判定一次。"""
    env = session.load_env()
    strategies = {
        AndroidTuningRouteStrategy.env: AndroidTuningRouteStrategy,
        DesktopTuningRouteStrategy.env: DesktopTuningRouteStrategy,
    }
    try:
        strategy_type = strategies[env]
    except KeyError as exc:
        raise ValueError(f"不支持调律导航环境: {env!r}") from exc
    return strategy_type(wf)
