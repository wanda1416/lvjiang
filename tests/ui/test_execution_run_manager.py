from __future__ import annotations

from lvjiang.ui.main.execution_runs import (
    ExecutionRunManager,
    RunState,
    StartDenial,
)
from lvjiang.ui.main.execution_targets import ExecutionTarget, android_target_id


def _snapshot(identity: str):
    return ExecutionTarget(
        id=android_target_id(identity), kind="adb", display_name=identity,
        capture=object(), input_ctrl=object(),
    ).snapshot()


def test_manager_keeps_single_run_during_migration() -> None:
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=False)
    _, first = manager.try_begin(
        target=_snapshot("A"), username="甲", name="任务 A")
    decision, second = manager.try_begin(
        target=_snapshot("B"), username="乙", name="任务 B")

    assert first is not None
    assert second is None
    assert decision.denial_code == StartDenial.PARALLEL_DISABLED


def test_manager_enforces_target_user_and_lv1_independently() -> None:
    entitled = False
    manager = ExecutionRunManager(
        lv1_check=lambda: entitled, parallel_enabled=True)
    _, first = manager.try_begin(
        target=_snapshot("A"), username="甲", name="任务 A")
    assert first is not None

    target_busy = manager.can_start(target_id=first.target_id, username="乙")
    assert target_busy.denial_code == StartDenial.TARGET_BUSY

    user_busy = manager.can_start(
        target_id=_snapshot("B").id, username="甲")
    assert user_busy.denial_code == StartDenial.USER_BUSY

    needs_lv1 = manager.can_start(
        target_id=_snapshot("B").id, username="乙")
    assert needs_lv1.denial_code == StartDenial.LV1_REQUIRED

    entitled = True
    allowed, second = manager.try_begin(
        target=_snapshot("B"), username="乙", name="任务 B")
    assert allowed.allowed and second is not None


def test_waiting_run_keeps_target_occupied_until_terminal_finish() -> None:
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    _, run = manager.try_begin(
        target=_snapshot("A"), username="甲", name="任务 A")
    assert run is not None

    manager.set_state(run.task_run_id, RunState.WAITING_TARGET)
    assert manager.run_for_target(run.target_id) is run
    assert manager.can_start(
        target_id=run.target_id, username="乙").denial_code == StartDenial.TARGET_BUSY

    manager.finish(run.task_run_id, RunState.INTERRUPTED)
    assert manager.run_for_target(run.target_id) is None


def test_stop_all_signals_every_context_and_reports_running_workers() -> None:
    class Worker:
        def __init__(self, running: bool) -> None:
            self.running = running

        def isRunning(self) -> bool:
            return self.running

    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    _, first = manager.try_begin(
        target=_snapshot("A"), username="甲", name="任务 A")
    _, second = manager.try_begin(
        target=_snapshot("B"), username="乙", name="任务 B")
    assert first is not None and second is not None
    first.worker = Worker(True)
    second.worker = Worker(False)

    stopped = manager.request_stop_all()

    assert stopped == (first, second)
    assert first.stop_event.is_set() and second.stop_event.is_set()
    assert first.state == RunState.STOPPING
    assert second.state == RunState.STOPPING
    assert manager.unfinished_workers() == (first,)
