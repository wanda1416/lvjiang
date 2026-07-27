"""背包滚动遍历策略基类"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型标注，防循环导入
    from src.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )


class BagTraversal(ABC):
    """背包滚动遍历策略基类：traverse 完成一个部位的整包遍历

    策略通过 wf 调用工作流共享原语：_find_panel/align_panel/drag_grid/
    wait_delay/is_stopped/_read_row/_process_equipment/_process_row_cols/
    _equip_label 与 GRID_SCENE/GRID_PANEL 常量。
    """

    name = ""

    @abstractmethod
    def traverse(self, wf: "AutoTuningWorkflow", detail_scene: str) -> None:
        """遍历当前部位背包的全部装备（含滚动与到底判定）"""
