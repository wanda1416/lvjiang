"""手机暂停必须真实停住，结束必须能唤醒暂停中的任务。"""
import json
import threading

import pytest

from lvjiang.core.ondevice import task_runner as runner
from lvjiang.workflows.errors import WorkflowAbort


@pytest.fixture
def state(monkeypatch):
    state = runner._TaskState()
    gate = runner._PauseGate()
    gate.set()
    monkeypatch.setattr(runner, "_STATE", state)
    monkeypatch.setattr(runner, "_PAUSE_GATE", gate)
    state.begin("task", "test")
    yield state
    state.request_stop()


def test_pause_occupies_task_slot_until_explicit_resume(state):
    assert json.loads(runner.pause_task())["ok"]
    assert state.snapshot()["state"] == "pausing"
    assert state.is_running()
    assert not runner._PAUSE_GATE.wait(0)
    assert state.snapshot()["state"] == "paused"
    assert json.loads(runner.resume_task())["ok"]
    assert runner._PAUSE_GATE.is_set()
    assert state.snapshot()["state"] == "running"


def test_stop_wakes_manual_pause_and_does_not_continue(state, monkeypatch):
    acknowledged = threading.Event()
    original = state.acknowledge_pause

    def acknowledge():
        original()
        acknowledged.set()

    monkeypatch.setattr(state, "acknowledge_pause", acknowledge)
    errors = []

    def manual_pause():
        try:
            runner._task_ui("pause", message="manual handling")
        except WorkflowAbort as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=manual_pause)
    worker.start()
    try:
        assert acknowledged.wait(2)
        assert state.snapshot()["state"] == "paused"
        assert state.snapshot()["message"] == "manual handling"
        assert json.loads(runner.stop_task())["ok"]
        worker.join(2)
        assert not worker.is_alive()
        assert errors == ["用户已结束任务"]
        assert state.snapshot()["state"] == "stopping"
    finally:
        state.request_stop()
        worker.join(2)
