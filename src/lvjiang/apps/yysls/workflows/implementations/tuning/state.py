"""自动调律运行期状态。

状态与报告严格分离：本模块只描述业务执行所需的可变状态，不负责日志、
Markdown 或 UI 通知。纯 dataclass 使状态转换可以脱离 Workflow 单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from lvjiang.apps.yysls.core.equip_parser import EquipmentData


class TuningMode(Enum):
    """单件装备当前采用的调律模式。"""

    NORMAL = "normal"
    FORCE_TUNE = "force_tune"
    TUNE_FULL_RECYCLE = "tune_full_recycle"


class SlotEffect(Enum):
    """单件处理对当前背包格位的影响。"""

    UNCHANGED = "unchanged"
    REMOVED = "removed"


@dataclass(frozen=True)
class EquipmentProcessingResult:
    """单件处理的结构化结果。

    fingerprint 是处理结束后当前格最终占位装备的指纹；slot_effect 表示
    处理期间是否发生过回收补位，避免遍历器从 Recorder 读取业务状态。
    """

    fingerprint: str
    slot_effect: SlotEffect = SlotEffect.UNCHANGED
    recycle_outcome: Any = None

    @property
    def slot_changed(self) -> bool:
        return self.slot_effect is SlotEffect.REMOVED


@dataclass
class EquipmentSession:
    """一件装备从扫描到处理结束的业务状态。"""

    name: str = ""
    equipment: EquipmentData | None = None
    expected_rating: str | None = None
    mode: TuningMode = TuningMode.NORMAL
    rounds: int = 0
    resets: int = 0
    stop_reason: str = ""

    @property
    def force_tune(self) -> bool:
        return self.mode is TuningMode.FORCE_TUNE

    @property
    def tune_full_recycle(self) -> bool:
        return self.mode is TuningMode.TUNE_FULL_RECYCLE


@dataclass
class TuningRunState:
    """一次自动调律运行中跨装备共享的业务状态。"""

    current_slot: str = ""
    locked_fingerprints: set[str] = field(default_factory=set)
    last_processing_result: EquipmentProcessingResult | None = None

    def enter_slot(self, slot: str) -> None:
        """切换部位时清空仅对上一部位有效的锁定指纹。"""
        self.current_slot = slot
        self.locked_fingerprints.clear()

    def record_locked(self, fingerprint: str) -> None:
        if fingerprint:
            self.locked_fingerprints.add(fingerprint)

    def is_locked(self, fingerprint: str) -> bool:
        return fingerprint in self.locked_fingerprints
