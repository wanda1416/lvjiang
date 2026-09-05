"""背包滚动手段策略：精准拖拽或兼容后台输入的鼠标滚轮。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


class BagScrollStrategy(ABC):
    """移动背包网格一段距离；方向表示内容移动方向。"""

    name = ""

    @abstractmethod
    def move(self, wf: "AutoTuningWorkflow", direction: str, *,
             distance: float = 1.0, hold: float | None = None) -> None:
        """按指定内容方向移动背包。"""


class DragBagScroll(BagScrollStrategy):
    """前台精准模式：沿用按网格行高计算距离的拖拽。"""

    name = "drag"

    def move(self, wf: "AutoTuningWorkflow", direction: str, *,
             distance: float = 1.0, hold: float | None = None) -> None:
        wf.drag_grid(
            wf.GRID_SCENE, wf.GRID_PANEL, direction,
            distance=distance, hold=hold)


class WheelBagScroll(BagScrollStrategy):
    """PC 后台兼容模式：一个滚轮刻度替代一次拖拽。"""

    name = "wheel"
    _WHEEL_DIRECTION = {"up": "down", "down": "up"}

    def move(self, wf: "AutoTuningWorkflow", direction: str, *,
             distance: float = 1.0, hold: float | None = None) -> None:
        del distance, hold
        wf.scroll_any(
            wf.GRID_SCENE, wf.GRID_PANEL,
            self._WHEEL_DIRECTION[direction], amount=1)


SCROLL_STRATEGIES: dict[str, type[BagScrollStrategy]] = {
    "drag": DragBagScroll,
    "wheel": WheelBagScroll,
}


def create_bag_scroll_strategy(
    run_env: str, pc_background_scroll: bool,
) -> BagScrollStrategy:
    """按运行环境和配置选择一次滚动手段，Android 永远使用拖拽。"""
    mode = {
        ("desktop", True): "wheel",
    }.get((run_env, pc_background_scroll), "drag")
    return SCROLL_STRATEGIES[mode]()
