"""一次自动调律运行的结构化记录会话。"""
from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone

from loguru import logger

from .models import (
    RESET_COMPLETED,
    RESULT_RECYCLED,
    RESULT_SKIPPED,
    RESULT_TUNED,
    RESULT_TUNED_RECYCLED,
    TuningEquipmentResult,
    TuningRunSummary,
)
from .projector import TuningResultProjector
from .repository import TuningHistoryRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class TuningRunSession:
    """统一持有运行期结果并增量落库；任何失败都不影响自动调律。"""

    def __init__(
        self,
        repository: TuningHistoryRepository,
        *,
        username: str,
        selected_slots: list[str],
        rule_snapshot: list[dict] | None = None,
        config_snapshot: dict | None = None,
        clock=_now,
    ):
        self.repository = repository
        self.clock = clock
        # 实时进度保持连续展示；历史在成功重置处拆成两次独立调律事件。
        self.projector = TuningResultProjector(clock, split_resets=True)
        self.results: list[TuningEquipmentResult] = []
        self.run_id = uuid.uuid4().hex
        self._finished = False
        self.summary = TuningRunSummary(
            run_id=self.run_id,
            started_at=clock(), finished_at="", username=username,
            status="running", stop_reason="",
            selected_slots=tuple(selected_slots),
            rule_snapshot=tuple(rule_snapshot or ()),
            config_snapshot=dict(config_snapshot or {}),
        )
        try:
            repository.create_run(self.summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史运行记录创建失败，继续执行调律: {exc}")

    def consume(self, event: str, *args) -> TuningEquipmentResult | None:
        try:
            item = self.projector.consume(event, *args)
            if item is None:
                return None
            self.results.append(item)
            self.repository.save_equipment(
                self.run_id, item,
                event_id=uuid.uuid4().hex if item.entered_tuning else None,
            )
            return item
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史增量保存失败，继续执行调律: {exc}")
            return None

    def finish(self, *, status: str, stop_reason: str = "",
               markdown_path: str = "") -> None:
        if self._finished:
            return
        self._finished = True
        results = self.results
        self.summary = replace(
            self.summary,
            finished_at=self.clock(), status=status,
            stop_reason=stop_reason,
            total_equipment=len(results),
            tuned_count=sum(item.result in {RESULT_TUNED, RESULT_TUNED_RECYCLED}
                            for item in results),
            recycled_count=sum(item.result in {RESULT_RECYCLED, RESULT_TUNED_RECYCLED}
                               for item in results),
            skipped_count=sum(item.result == RESULT_SKIPPED for item in results),
            reset_count=sum(item.reset_outcome == RESET_COMPLETED
                            for item in results),
            total_rounds=sum(item.rounds for item in results),
            markdown_path=markdown_path,
        )
        try:
            self.repository.finish_run(self.summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"调律历史运行收尾失败，继续退出调律: {exc}")
