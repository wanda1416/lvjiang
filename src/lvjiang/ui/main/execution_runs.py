"""按执行目标管理自动化运行实例。

第一阶段只接管运行身份、目标占用和结构化启动判定；现有主窗口仍保留
单任务 UI 投影。并发入口必须等所有消费者迁移完成后再开放。
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from .execution_targets import ExecutionTargetSnapshot


class RunState(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSING = "pausing"
    PAUSED = "paused"
    WAITING_TARGET = "waiting_target"
    STOPPING = "stopping"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class StartDenial(StrEnum):
    NONE = ""
    TARGET_BUSY = "target_busy"
    USER_BUSY = "user_busy"
    PARALLEL_DISABLED = "parallel_disabled"
    LV1_REQUIRED = "lv1_required"


@dataclass(frozen=True)
class StartDecision:
    allowed: bool
    denial_code: StartDenial = StartDenial.NONE
    reason: str = ""
    conflicting_task_run_id: str = ""
    conflicting_target_id: str = ""


@dataclass
class ExecutionRunContext:
    task_run_id: str
    target_id: str
    target_snapshot: ExecutionTargetSnapshot
    username: str
    name: str
    state: RunState = RunState.STARTING
    engine: object | None = None
    ocr: Any = None
    worker: object | None = None
    ui_helper: object | None = None
    lease: object | None = None
    pause_event: object | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    metadata: dict[str, object] = field(default_factory=dict)


class ExecutionRunManager:
    """运行实例与目标占用的唯一进程内真源。"""

    def __init__(
        self, *, lv1_check: Callable[[], bool], parallel_enabled: bool = False,
    ) -> None:
        self._lv1_check = lv1_check
        self._parallel_enabled = parallel_enabled
        self._runs: dict[str, ExecutionRunContext] = {}
        self._occupied: dict[str, str] = {}
        self._lock = threading.RLock()

    def can_start(self, *, target_id: str, username: str) -> StartDecision:
        with self._lock:
            occupying = self._occupied.get(target_id, "")
            if occupying:
                return StartDecision(
                    False, StartDenial.TARGET_BUSY, "该执行目标已有任务运行",
                    occupying, target_id,
                )
            for run in self._runs.values():
                if username and run.username == username:
                    return StartDecision(
                        False, StartDenial.USER_BUSY,
                        f"用户「{username}」正在其他目标执行任务",
                        run.task_run_id, run.target_id,
                    )
            if self._runs and not self._parallel_enabled:
                run = next(iter(self._runs.values()))
                return StartDecision(
                    False, StartDenial.PARALLEL_DISABLED,
                    "多目标运行尚未开放，当前仍保持单任务限制",
                    run.task_run_id, run.target_id,
                )
            if self._runs and not self._lv1_check():
                run = next(iter(self._runs.values()))
                return StartDecision(
                    False, StartDenial.LV1_REQUIRED,
                    "同时运行多个执行目标需要激活 Lv1",
                    run.task_run_id, run.target_id,
                )
            return StartDecision(True)

    def try_begin(
        self, *, target: ExecutionTargetSnapshot, username: str, name: str,
    ) -> tuple[StartDecision, ExecutionRunContext | None]:
        with self._lock:
            decision = self.can_start(target_id=target.id, username=username)
            if not decision.allowed:
                return decision, None
            context = ExecutionRunContext(
                task_run_id=uuid.uuid4().hex,
                target_id=target.id,
                target_snapshot=target,
                username=username,
                name=name,
            )
            self._runs[context.task_run_id] = context
            self._occupied[target.id] = context.task_run_id
            return decision, context

    def set_state(self, task_run_id: str, state: RunState) -> None:
        with self._lock:
            run = self._runs.get(task_run_id)
            if run is not None:
                run.state = state

    def finish(self, task_run_id: str, state: RunState) -> ExecutionRunContext | None:
        if state not in {
            RunState.COMPLETED, RunState.INTERRUPTED, RunState.FAILED,
        }:
            raise ValueError(f"运行终态无效: {state}")
        with self._lock:
            run = self._runs.pop(task_run_id, None)
            if run is None:
                return None
            run.state = state
            if self._occupied.get(run.target_id) == task_run_id:
                self._occupied.pop(run.target_id, None)
            return run

    def run(self, task_run_id: str) -> ExecutionRunContext | None:
        with self._lock:
            return self._runs.get(task_run_id)

    def run_for_target(self, target_id: str) -> ExecutionRunContext | None:
        with self._lock:
            return self._runs.get(self._occupied.get(target_id, ""))

    def all_runs(self) -> tuple[ExecutionRunContext, ...]:
        with self._lock:
            return tuple(self._runs.values())

    def request_stop_all(self) -> tuple[ExecutionRunContext, ...]:
        """向全部运行实例发出停止请求，并保留占用直到线程真正结束。"""
        with self._lock:
            runs = tuple(self._runs.values())
            for run in runs:
                run.stop_event.set()
                run.state = RunState.STOPPING
            return runs

    def unfinished_workers(self) -> tuple[ExecutionRunContext, ...]:
        """返回仍持有活动线程的上下文，供退出协议等待与报告。"""
        with self._lock:
            return tuple(
                run for run in self._runs.values()
                if run.worker is not None
                and bool(getattr(run.worker, "isRunning", lambda: False)())
            )
