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


def test_confirm_requires_matching_reply_and_reject_returns_false(state, monkeypatch):
    requested = threading.Event()
    original = state.request_confirmation

    def request(message):
        original(message)
        requested.set()

    monkeypatch.setattr(state, "request_confirmation", request)
    replies = []
    worker = threading.Thread(target=lambda: replies.append(runner._task_ui("confirm", message="是否执行？")))
    worker.start()
    try:
        assert requested.wait(2)
        interaction = state.snapshot()["interaction"]
        assert interaction["message"] == "是否执行？"
        assert not json.loads(runner.resume_task())["ok"]
        assert not json.loads(runner.respond_interaction("stale", True))["ok"]
        assert json.loads(runner.respond_interaction(interaction["id"], False))["ok"]
        worker.join(2)
        assert replies == [False]
        state.request_confirmation("下一条问题")
        assert not json.loads(runner.respond_interaction(interaction["id"], True))["ok"]
        current = state.snapshot()["interaction"]["id"]
        assert json.loads(runner.respond_interaction(current, True))["ok"]
        assert state._answer is True
    finally:
        state.request_stop()
        worker.join(2)


def test_stop_wakes_confirmation_without_accepting(state, monkeypatch):
    requested = threading.Event()
    original = state.request_confirmation

    def request(message):
        original(message)
        requested.set()

    monkeypatch.setattr(state, "request_confirmation", request)
    errors = []

    def confirm():
        try:
            runner._task_ui("confirm", message="继续？")
        except WorkflowAbort as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=confirm)
    worker.start()
    try:
        assert requested.wait(2)
        request_id = state.snapshot()["interaction"]["id"]
        assert json.loads(runner.stop_task())["ok"]
        worker.join(2)
        assert errors == ["用户已结束任务"]
        assert not json.loads(runner.respond_interaction(request_id, True))["ok"]
    finally:
        state.request_stop()
        worker.join(2)


def test_finished_worker_reserves_slot_until_history_cleanup_exits(state):
    finishing = threading.Event()
    release = threading.Event()

    def worker_body():
        state.finish(runner.STATE_DONE, "流程结束")
        finishing.set()
        release.wait(2)

    worker = threading.Thread(target=worker_body)
    state.bind_thread(worker)
    worker.start()
    try:
        assert finishing.wait(2)
        assert state.is_running() and state.snapshot()["busy"]
        assert not json.loads(runner.stop_task())["ok"]
        assert state.snapshot()["state"] == runner.STATE_DONE
    finally:
        release.set()
        worker.join(2)
    assert not state.is_running()


def test_selecting_next_user_preserves_run_owner_and_is_blocked_while_paused(state, monkeypatch):
    from types import SimpleNamespace

    active = {"user": "runner"}
    store = SimpleNamespace(get_active=lambda key, default="": active.get(key, default),
                            set_active=lambda key, value: active.__setitem__(key, value))
    monkeypatch.setattr("lvjiang.core.config.interface.get_interface_store", lambda: store)
    monkeypatch.setattr("lvjiang.core.ondevice.offline.configuration_status", lambda: {"ready": True})
    monkeypatch.setattr(runner, "_local_user_names", lambda: ["runner", "viewer"])
    state.begin("task", "test", "runner")
    state.finish(runner.STATE_DONE, "流程结束")
    assert json.loads(runner.select_user("viewer"))["ok"]
    snapshot = json.loads(runner.get_status())
    assert snapshot["username"] == "runner"
    assert snapshot["sync"]["execution_username"] == "viewer"

    state.begin("next", "next", "viewer")
    state.request_pause()
    state.acknowledge_pause()
    assert not json.loads(runner.select_user("runner"))["ok"]
    assert active["user"] == "viewer" and state.snapshot()["username"] == "viewer"


def test_device_result_uses_pc_output_tree_and_relative_history(tmp_path, monkeypatch, state):
    from datetime import datetime
    from types import SimpleNamespace

    from lvjiang.core import daily_history

    started = datetime(2026, 1, 31, 23, 59, 58)
    repository = daily_history.TaskHistoryRepository(tmp_path / "history.db")
    monkeypatch.setattr("lvjiang.constants.PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("lvjiang.constants.OUTPUT_DIR", tmp_path / "config/session/output")
    session_type = daily_history.TaskRunSession
    monkeypatch.setattr(daily_history, "TaskRunSession", lambda **kwargs: session_type(
        **kwargs, repository=repository, started_at=started, task_run_id="device-run"))
    monkeypatch.setattr("lvjiang.core.ondevice.diagnostics.record", lambda *args: None)
    monkeypatch.setattr("lvjiang.core.config.wf_configs.get_wf_config", lambda *args: {})
    monkeypatch.setattr("lvjiang.core.task_params.resolve_task_params", lambda *args: ({}, {}))
    engine = SimpleNamespace(run_username="用户甲", users_dir=tmp_path,
                             execute=lambda *args, **kwargs: {"message": "完成"})
    monkeypatch.setattr(runner, "_get_engine", lambda: engine)
    monkeypatch.setattr(runner, "_reset_engine_state", lambda *args: None)
    monkeypatch.setattr(runner, "_build_source", lambda *args: "fake")
    monkeypatch.setattr(runner, "_ENGINE", None)
    state.begin("daily", "日常", "用户甲")

    runner._run_in_thread({"id": "daily", "name": "日常"}, None)

    record = repository.list_task_runs()[0]
    assert record.status == "completed"
    assert record.result_path == (
        "config/session/output/用户甲/2026-01/31/daily_20260131_235958_000000_device-run.json")
    result = daily_history.resolve_history_path(record.result_path)
    assert json.loads(result.read_text(encoding="utf-8")) == {"message": "完成"}
    assert record.log_path.startswith("logs/daily/用户甲/2026-01/31/")
    assert daily_history.resolve_history_path(record.log_path).is_file()
