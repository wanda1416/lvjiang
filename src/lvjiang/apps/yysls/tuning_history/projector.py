"""把现有调律生命周期事件归约为稳定的装备终态。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from .models import (
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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TuningResultProjector:
    """纯 Python 事件归约器；实时 UI 与历史记录共用同一实现。"""

    def __init__(self, clock: Callable[[], str] = utc_now, *,
                 split_resets: bool = False):
        self._clock = clock
        self._split_resets = split_resets
        self.reset()

    def reset(self) -> None:
        self.current_slot = ""
        self.current: dict = {}
        self.current_id = 0
        self.next_id = 1
        self.decision: dict = {}
        self.was_modified = False
        self.reset_attempted = False
        self.reset_outcome = ""
        self.reset_reason = ""
        self.tuning_started_at = ""
        self.round_details: list[dict] = []
        self.after_reset = False
        self._ignore_reset_completion = False

    def consume(self, event: str, *args) -> TuningEquipmentResult | None:
        if event == "slot_entered":
            self.current_slot = str(args[0] or "")
        elif event == "equipment_started":
            self._start(dict(args[0] or {}))
        elif event == "scan_decision" and self.current:
            self.decision = deepcopy(args[0] or {})
        elif event == "operation_updated":
            self._operation(dict(args[0] or {}))
        elif event == "equipment_reset" and self.current:
            if self._split_resets:
                return self._split_on_reset(dict(args[0] or {}))
            self.was_modified = True
            self.reset_attempted = True
            self.reset_outcome = RESET_COMPLETED
        elif event == "round_prepared" and self.current:
            self._prepare_round(dict(args[0] or {}))
        elif event == "tune_round_completed" and self.current:
            self._complete_round(dict(args[0] or {}))
        elif event == "equipment_finished":
            return self._finish(dict(args[0] or {}))
        return None

    def _start(self, info: dict) -> None:
        self.current = deepcopy(info)
        self.current_id = self.next_id
        self.next_id += 1
        self.decision = {}
        self.was_modified = False
        self.reset_attempted = False
        self.reset_outcome = ""
        self.reset_reason = ""
        self.tuning_started_at = ""
        self.round_details = []
        self.after_reset = False
        self._ignore_reset_completion = False
        self.current["_scanned_at"] = self._clock()

    def _operation(self, info: dict) -> None:
        if not self.current:
            return
        phase = str(info.get("phase") or "")
        # material 只会在成功进入调律页后发出；navigation 仍可能以
        # no_tune_entry 结束，不能提前将它标记为可上报调律会话。
        if phase in {"material", "tuning"} and not self.tuning_started_at:
            self.tuning_started_at = self._clock()
        if phase == "reset":
            outcome = str(info.get("reset_outcome") or "")
            if (self._ignore_reset_completion
                    and outcome == RESET_COMPLETED):
                self._ignore_reset_completion = False
                return
            self.reset_attempted = True
            reason = str(info.get("reason") or info.get("message") or "").strip()
            if reason:
                self.reset_reason = reason
            if outcome:
                self.reset_outcome = outcome
            return
        if phase != "decision":
            return
        reason = str(info.get("reason") or "").strip()
        message = str(info.get("message") or "").strip()
        action = str(info.get("action") or "").strip()
        if reason or message or action:
            self.decision = {"reason": reason or message, "action": action}

    def _prepare_round(self, info: dict) -> None:
        detail = deepcopy(info)
        detail["completed"] = False
        round_no = int(detail.get("round_no") or len(self.round_details) + 1)
        detail["round_no"] = round_no
        for index, existing in enumerate(self.round_details):
            if int(existing.get("round_no") or 0) == round_no:
                self.round_details[index] = {**existing, **detail}
                return
        self.round_details.append(detail)

    def _complete_round(self, info: dict) -> None:
        detail = deepcopy(info)
        detail["completed"] = True
        round_no = int(detail.get("round_no") or len(self.round_details) + 1)
        detail["round_no"] = round_no
        for index, existing in enumerate(self.round_details):
            if int(existing.get("round_no") or 0) == round_no:
                self.round_details[index] = {**existing, **detail}
                return
        self.round_details.append(detail)

    def _completed_round_count(self) -> int:
        return sum(detail.get("completed", True) is not False
                   for detail in self.round_details)

    def _finish(self, info: dict) -> TuningEquipmentResult | None:
        started = self.current
        if not started:
            return None
        rounds = (self._completed_round_count() if self.after_reset
                  else int(info.get("rounds") or 0))
        status = str(info.get("status") or "")
        if status == "recycled":
            if (self._split_resets
                    and self.reset_outcome == RESET_EXHAUSTED_RECYCLED):
                result = RESULT_RECYCLED
            else:
                result = (RESULT_TUNED_RECYCLED
                          if rounds > 0 or self.was_modified
                          else RESULT_RECYCLED)
        elif (self._split_resets and self.reset_outcome in {
                RESET_COOLDOWN, RESET_EXHAUSTED,
                RESET_MATERIAL_SHORTAGE, RESET_FAILED,
                RESET_COUNT_UNREADABLE,
        }):
            result = RESULT_RESET
        elif rounds > 0 or self.was_modified:
            result = RESULT_TUNED
        elif self.reset_attempted:
            result = RESULT_RESET
        else:
            result = RESULT_SKIPPED

        if self.was_modified and rounds == 0:
            default_reason = ("执行重置调律后回收"
                              if result == RESULT_TUNED_RECYCLED
                              else "执行重置调律后保留")
        else:
            default_reason = {
                RESULT_RECYCLED: "扫描处理后回收",
                RESULT_SKIPPED: "扫描处理后跳过",
                RESULT_TUNED: f"完成 {rounds} 轮调律后保留",
                RESULT_TUNED_RECYCLED: f"完成 {rounds} 轮调律后回收",
                RESULT_RESET: "重置未执行",
            }[result]
        reason = str(
            info.get("reason") or self.decision.get("reason") or default_reason
        ).strip()
        initial = deepcopy(started.get("affixes") or [])
        final = deepcopy(info.get("final_affixes") or initial)
        item = TuningEquipmentResult(
            equipment_id=self.current_id,
            slot_key=self.current_slot,
            name=str((started.get("name") if self.after_reset else None)
                     or info.get("name") or started.get("name")
                     or started.get("type") or "未知"),
            type=str(started.get("type") or ""),
            level=started.get("level"),
            quality=str(started.get("quality") or ""),
            final_affixes=tuple(final),
            final_rating=str(info.get("final_rating") or ""),
            rounds=rounds,
            result=result,
            reason=reason,
            reset_outcome=self.reset_outcome,
            initial_affixes=tuple(initial),
            raw_status=status,
            scanned_at=str(started.get("_scanned_at") or ""),
            tuning_started_at=self.tuning_started_at,
            finished_at=self._clock(),
            round_details=tuple(deepcopy(self.round_details)),
            tuning_mode=str(info.get("tuning_mode") or ""),
            telemetry_stop_reason=str(
                info.get("telemetry_stop_reason") or ""),
            telemetry_final_rating=str(
                info.get("telemetry_final_rating") or ""),
            resets=int(info.get("resets") or 0),
        )
        self._clear_current()
        return item

    def _split_on_reset(self, info: dict) -> TuningEquipmentResult:
        """封存重置前事件，并以重置后的首词条建立全新历史事件。"""
        started = deepcopy(self.current)
        base_name = str(
            info.get("name") or started.get("name")
            or started.get("type") or "未知")
        now = self._clock()
        before = tuple(deepcopy(
            info.get("before_affixes") or started.get("affixes") or []))
        item = TuningEquipmentResult(
            equipment_id=self.current_id,
            slot_key=self.current_slot,
            name=f"{base_name}（重置前）",
            type=str(info.get("type") or started.get("type") or ""),
            level=info.get("level", started.get("level")),
            quality=str(info.get("quality") or started.get("quality") or ""),
            initial_affixes=tuple(deepcopy(started.get("affixes") or [])),
            final_affixes=before,
            final_rating=str(info.get("before_rating") or ""),
            rounds=self._completed_round_count(),
            result=RESULT_RESET,
            reason=self.reset_reason or "执行重置调律",
            reset_outcome=RESET_COMPLETED,
            raw_status="reset",
            scanned_at=str(started.get("_scanned_at") or ""),
            tuning_started_at=self.tuning_started_at,
            finished_at=now,
            round_details=tuple(deepcopy(self.round_details)),
            tuning_mode=str(info.get("tuning_mode") or "normal"),
            telemetry_stop_reason="reset_completed",
            telemetry_final_rating=str(info.get("before_rating") or ""),
            resets=int(info.get("resets_used") or 0),
        )

        after = deepcopy(started)
        after.pop("_scanned_at", None)
        after["name"] = f"{base_name}（重置后）"
        after["affixes"] = deepcopy(info.get("after_affixes") or [])
        self._start(after)
        # 新装备已经处于调律页，不等待下一次 material/tuning 事件再标记。
        self.tuning_started_at = now
        self.after_reset = True
        # equipment_reset 后紧跟的 reset_completed 只是上一条事件的确认，
        # 不能污染新事件的最终处理结果。
        self._ignore_reset_completion = True
        return item

    def _clear_current(self) -> None:
        self.current = {}
        self.current_id = 0
        self.decision = {}
        self.was_modified = False
        self.reset_attempted = False
        self.reset_outcome = ""
        self.reset_reason = ""
        self.tuning_started_at = ""
        self.round_details = []
        self.after_reset = False
        self._ignore_reset_completion = False
