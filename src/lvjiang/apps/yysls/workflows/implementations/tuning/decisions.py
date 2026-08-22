"""自动调律行为决策的稳定值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BehaviorAction(str, Enum):
    """扫描与调律结束行为的统一动作集合。"""

    CONTINUE = "continue"
    RESET = "reset"
    RECYCLE = "recycle"
    SKIP = "skip"
    TUNE_FULL_RECYCLE = "tune_full_recycle"
    TUNE_THIS = "tune_this"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class BehaviorDecision:
    action: BehaviorAction
    reason: str

    @classmethod
    def from_raw(cls, action: str, reason: str) -> BehaviorDecision:
        try:
            normalized = BehaviorAction(action)
        except ValueError as exc:
            raise ValueError(f"未知自动调律行为: {action!r}") from exc
        return cls(normalized, reason)
