"""自动调律装备结果的 Qt 只读数据源。"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from ...tuning_history.models import (  # re-export：保持现有导入兼容
    RESET_ANOMALIES,
    RESET_COMPLETED,
    RESET_COOLDOWN,
    RESET_COUNT_UNREADABLE,
    RESET_EXHAUSTED,
    RESET_EXHAUSTED_RECYCLED,
    RESET_FAILED,
    RESET_MATERIAL_SHORTAGE,
    RESULT_RECYCLED,
    RESULT_RESET,
    RESULT_SKIPPED,
    RESULT_TUNED,
    RESULT_TUNED_RECYCLED,
    TuningEquipmentResult,
)
from ...tuning_history.projector import TuningResultProjector
from .progress_hub import TuningProgressHub

__all__ = [
    "RESET_ANOMALIES", "RESET_COMPLETED", "RESET_COOLDOWN", "RESET_COUNT_UNREADABLE",
    "RESET_EXHAUSTED",
    "RESET_EXHAUSTED_RECYCLED", "RESET_FAILED",
    "RESET_MATERIAL_SHORTAGE", "RESULT_RECYCLED", "RESULT_RESET",
    "RESULT_SKIPPED", "RESULT_TUNED", "RESULT_TUNED_RECYCLED",
    "TuningEquipmentResult", "TuningResultStore",
]


class TuningResultStore(QObject):
    """以统一归约器消费进度事件；自身只负责 Qt 信号和结果集合。"""

    changed = pyqtSignal()
    reset = pyqtSignal()
    result_added = pyqtSignal(object)

    def __init__(self, hub: TuningProgressHub | None = None, parent=None):
        super().__init__(parent)
        self._hub: TuningProgressHub | None = None
        self._results: list[TuningEquipmentResult] = []
        self._projector = TuningResultProjector()
        if hub is not None:
            self.reconnect(hub)

    @property
    def results(self) -> tuple[TuningEquipmentResult, ...]:
        return tuple(self._results)

    def results_for_slot(self, slot_key: str | None) -> tuple[TuningEquipmentResult, ...]:
        if not slot_key:
            return self.results
        if slot_key in {"main_weapon", "sub_weapon"}:
            return tuple(item for item in self._results
                         if item.slot_key in {"main_weapon", "sub_weapon"})
        return tuple(item for item in self._results if item.slot_key == slot_key)

    def count_for_slot(self, slot_key: str) -> int:
        return len(self.results_for_slot(slot_key))

    def reconnect(self, hub: TuningProgressHub) -> None:
        if hub is self._hub:
            return
        self._disconnect()
        self._hub = hub
        for signal, handler in self._connections():
            signal.connect(handler)

    def clear(self) -> None:
        self._results.clear()
        self._projector.reset()
        self.reset.emit()
        self.changed.emit()

    def replace_results(self, results) -> None:
        """载入不可变历史快照；供历史详情复用同一总览组件。"""
        self._results = list(results)
        self._projector.reset()
        self.reset.emit()
        self.changed.emit()

    def _connections(self):
        assert self._hub is not None
        return (
            (self._hub.slot_entered, self._on_slot_entered),
            (self._hub.equipment_started, self._on_equipment_started),
            (self._hub.scan_decision, self._on_scan_decision),
            (self._hub.operation_updated, self._on_operation_updated),
            (self._hub.equipment_reset, self._on_equipment_reset),
            (self._hub.round_prepared, self._on_round_prepared),
            (self._hub.tune_round_completed, self._on_tune_round_completed),
            (self._hub.equipment_finished, self._on_equipment_finished),
        )

    def _disconnect(self) -> None:
        if self._hub is None:
            return
        for signal, handler in self._connections():
            try:
                signal.disconnect(handler)
            except TypeError:
                pass

    def _consume(self, event: str, *args) -> None:
        item = self._projector.consume(event, *args)
        if item is None:
            return
        self._results.append(item)
        self.result_added.emit(item)
        self.changed.emit()

    def _on_slot_entered(self, slot_key: str, slot_name: str) -> None:
        self._consume("slot_entered", slot_key, slot_name)

    def _on_equipment_started(self, info: dict) -> None:
        self._consume("equipment_started", info)

    def _on_scan_decision(self, info: dict) -> None:
        self._consume("scan_decision", info)

    def _on_operation_updated(self, info: dict) -> None:
        self._consume("operation_updated", info)

    def _on_equipment_reset(self, info: dict) -> None:
        self._consume("equipment_reset", info)

    def _on_round_prepared(self, info: dict) -> None:
        # 狗粮不足在准备阶段中止的轮次只发 round_prepared（will_tune=False），
        # 不订阅它，实时总览就会缺掉这一轮，而历史详情有——两个视图共用同一
        # 张卡片，必须消费同一组事件。
        self._consume("round_prepared", info)

    def _on_tune_round_completed(self, info: dict) -> None:
        self._consume("tune_round_completed", info)

    def _on_equipment_finished(self, info: dict) -> None:
        self._consume("equipment_finished", info)
