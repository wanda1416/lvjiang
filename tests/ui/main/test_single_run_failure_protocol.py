"""单次运行按通用返回协议判定失败，框架层不出现任何具体任务。"""
import threading
from types import SimpleNamespace

import pytest

from lvjiang.ui.main.run_control import RunControlMixin, WorkflowWorker


def _finish(return_value, *, stopped=False, output=None, flow_id="any_flow"):
    worker = WorkflowWorker(flow_id, lambda: None)
    worker.result_or_exception = {} if output is None else output
    stop = threading.Event()
    if stopped:
        stop.set()
    run = SimpleNamespace(worker=worker, engine=SimpleNamespace(return_value=return_value),
                          stop_event=stop, metadata={})
    finished, saved, sessions = [], [], []
    host = SimpleNamespace(
        _run_manager=SimpleNamespace(run=lambda _id: run),
        append_log=lambda *_args: None,
        _save_workflow_result=lambda _flow, result, **_kw: saved.append(result),
        _finish_task_run=lambda _worker, **kwargs: finished.append(kwargs),
        _auto_save_session=lambda *_args: sessions.append(True),
    )
    RunControlMixin._on_workflow_finished.__wrapped__(host, "run")
    return run, worker, finished[0], saved[0], sessions


@pytest.mark.parametrize("value, message", [
    ({"status": "failed", "message": "脚本自己的文案"}, "脚本自己的文案"),
    ({"status": "failed"}, "脚本返回失败"),
    (-1, "-1"),
    (-2.0, "-2"),
])
def test_failed_return_marks_run_failed(value, message):
    run, worker, finished, saved, sessions = _finish(value, output={"items": [1]})
    assert run.metadata["terminal_state"] == "failed"
    assert finished["status"] == "failed"
    assert message in finished["error_message"]
    assert saved["error"] == finished["error_message"]
    assert saved["items"] == [1]
    # 归一化结果写回 worker：MCP 等消费方读到的是同一个失败结论
    assert worker.result_or_exception is saved
    assert sessions == []


@pytest.mark.parametrize("value", [None, 0, 1, 8, True, False, {"status": "success"}, {}, "x"])
def test_other_returns_complete_normally(value):
    run, worker, finished, saved, sessions = _finish(value)
    assert run.metadata["terminal_state"] == "completed"
    assert finished["status"] == "completed"
    assert "error" not in saved
    assert sessions == [True]


@pytest.mark.parametrize("value", [-1, {"status": "failed", "message": "失败"}])
def test_user_stop_stays_interrupted(value):
    run, worker, finished, saved, sessions = _finish(value, stopped=True)
    assert run.metadata["terminal_state"] == "interrupted"
    assert finished["status"] == "interrupted"
    assert "error" not in saved
    assert sessions == []


def test_existing_error_output_is_kept():
    run, worker, finished, saved, sessions = _finish(-1, output={"error": "预检拒绝"})
    assert finished["status"] == "failed"
    assert finished["error_message"] == "预检拒绝"
