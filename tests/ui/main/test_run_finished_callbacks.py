"""运行实例结束时宿主按通用 hook 通知注册方，不认识注册方的任何属性名。"""
import threading
from types import SimpleNamespace

from lvjiang.ui.main.run_control import RunControlMixin


def _end(engine, *, metadata=None, stopped=False):
    stop = threading.Event()
    if stopped:
        stop.set()
    context = SimpleNamespace(
        stop_event=stop, ui_helper=None, metadata=metadata or {},
        engine=engine, task_run_id="run-1",
    )
    finished = []
    host = SimpleNamespace(
        _current_run_context=None,
        _stop_confirmation_dialog=None,
        _ui_helper=None,
        _stop_requested=False,
        _emit_run_instance_state=lambda *_args: None,
        _run_manager=SimpleNamespace(finish=lambda run_id, state: finished.append(state)),
        _emit_concurrency_changed=lambda: None,
        _refresh_execution_targets_ui=lambda: None,
    )
    RunControlMixin._end_automation(host, "flow", run_context=context)
    return finished


def test_callbacks_receive_run_id_and_final_state():
    calls = []
    engine = SimpleNamespace(run_finished_callbacks=[
        lambda run_id, state: calls.append(("a", run_id, state)),
        lambda run_id, state: calls.append(("b", run_id, state)),
    ])
    _end(engine, metadata={"terminal_state": "failed"})
    assert calls == [("a", "run-1", "failed"), ("b", "run-1", "failed")]


def test_stopped_run_reports_interrupted():
    calls = []
    engine = SimpleNamespace(run_finished_callbacks=[lambda *args: calls.append(args)])
    _end(engine, stopped=True)
    assert calls == [("run-1", "interrupted")]


def test_failing_callback_does_not_block_finish():
    calls = []

    def boom(*_args):
        raise RuntimeError("boom")

    engine = SimpleNamespace(run_finished_callbacks=[boom, lambda *args: calls.append(args)])
    finished = _end(engine)
    assert calls == [("run-1", "completed")]
    assert len(finished) == 1


def test_engine_without_callbacks_is_fine():
    assert len(_end(SimpleNamespace())) == 1
    assert len(_end(None)) == 1
