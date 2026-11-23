"""Default GUI execution remains the original execution, not a disabled recorder."""

from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest

from lvjiang.core import task_diagnostics as diag
from lvjiang.workflows.engine import WorkflowEngine


class Capture:
    min_capture_interval = 0

    def __init__(self):
        self.calls = 0

    def capture(self):
        self.calls += 1
        return np.zeros((16, 16, 3), np.uint8)

    def get_capture_size(self):
        return 16, 16

    def set_capture_region(self, *region):
        self.region = region


class Input:
    target_hwnd = 1

    def __init__(self):
        self.actions = []

    def key_down(self, key):
        self.actions.append((self.target_hwnd, "down", key))

    def key_up(self, key):
        self.actions.append((self.target_hwnd, "up", key))


@pytest.mark.parametrize("opened_dialog", [False, True])
def test_disabled_desktop_keeps_backend_cache_scope_and_business_results(
    qtbot, tmp_path, monkeypatch, opened_dialog
):
    from lvjiang import constants
    from lvjiang.ui.main.run_control import RunControlMixin

    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    backend, capture = Input(), Capture()
    stopped = [False]
    engine = WorkflowEngine(
        capture=capture,
        input_ctrl=backend,
        ocr=None,
        layout=None,
        stop_check=lambda: stopped[0],
    )
    clock = SimpleNamespace(monotonic=lambda: 1.0, sleep=lambda seconds: None)
    engine.runtime_clock = clock
    observations = []

    def observer(meta, pixels):
        observations.append(meta["source"])

    engine.capture_observer = observer
    entered = []

    @contextmanager
    def existing_scope(actual_engine, source):
        assert actual_engine is engine
        entered.append(source)
        yield

    engine.execution_scope = existing_scope
    wf = tmp_path / "task.wf"
    wf.write_text('screenshot\npress "ESC" down\nreturn 0\n', encoding="utf-8")
    engine.execute(wf)  # Warm the real facade/KeyStateRegistry before comparing.
    registry, facade = engine._key_registry, engine._workflow

    def rebind(line, variables):
        if line == 2:
            engine.rebind_target_window(
                {"hwnd": 2, "left": 3, "top": 4, "width": 16, "height": 16}
            )

    engine.statement_hook = rebind
    reference = engine.execute(wf)
    baseline_calls = capture.calls
    baseline_observations = len(observations)
    expected_actions = backend.actions[-2:]

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled path constructed diagnostic machinery")

    monkeypatch.setattr(diag, "RecentFrameHistory", forbidden)
    monkeypatch.setattr(diag.DiagnosticRequest, "scope", forbidden)

    class Host(RunControlMixin):
        def _on_workflow_finished(self, run_id):
            pass

    host = Host()
    host._current_engine = engine
    if opened_dialog:
        host._task_diagnostics = diag.TaskDiagnostics()
        assert host._task_diagnostics.freeze() is None
    context = SimpleNamespace(
        task_run_id="synthetic-off",
        target_id="synthetic-target",
        metadata={},
        lease=None,
    )
    host._current_run_context = context
    host._start_workflow("task", "Task", lambda: engine.execute(wf))
    qtbot.waitUntil(context.worker.isFinished, timeout=5000)
    assert context.worker.wait(1000)
    assert context.worker.result_or_exception == reference
    assert engine.return_value == 0 and capture.calls == baseline_calls + 1
    assert len(observations) == baseline_observations + 1
    assert (
        backend.actions[-2:]
        == expected_actions
        == [(2, "down", "ESC"), (2, "up", "ESC")]
    )
    assert engine._capture is capture and engine._input is backend
    assert engine._key_registry is registry and registry._backend is backend
    assert not registry.is_pressed("ESC") and backend.target_hwnd == 2
    assert engine._workflow is facade and engine.runtime_clock is clock
    assert (
        engine.capture_observer is observer and engine.execution_scope is existing_scope
    )
    assert len(entered) == 3 and not (tmp_path / "logs/recordings").exists()
    # Stop and application exceptions still take the original worker path.
    stopped[0] = True
    stopped_reference = engine.execute(wf)
    host._start_workflow("stopped", "Stopped", lambda: engine.execute(wf))
    worker = host._current_worker
    qtbot.waitUntil(worker.isFinished, timeout=5000)
    assert worker.wait(1000) and worker.result_or_exception == stopped_reference
    assert capture.calls == baseline_calls + 1

    def original_error():
        raise ValueError("original-error")

    host._start_workflow("error", "Error", original_error)
    worker = host._current_worker
    qtbot.waitUntil(worker.isFinished, timeout=5000)
    assert worker.wait(1000)
    assert isinstance(worker.result_or_exception, ValueError)
    assert str(worker.result_or_exception) == "original-error"
    assert (
        engine.capture_observer is observer and engine._key_registry._backend is backend
    )


def test_disabled_batch_builder_keeps_unmodified_dependencies_and_controls(
    tmp_path, monkeypatch, qapp
):
    from lvjiang.core.batch.models import BatchContext, BatchRunSpec
    from lvjiang.core.batch.scheduler import BatchScheduler
    from lvjiang.core.config.users import SessionManager

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled batch built a diagnostic scope/recorder")

    monkeypatch.setattr(diag, "RecentFrameHistory", forbidden)
    monkeypatch.setattr(diag.DiagnosticRequest, "scope", forbidden)
    capture, backend = Capture(), Input()
    pause = __import__("threading").Event()
    ctx = BatchContext(capture, None, backend, None, pause_event=pause)
    assert ctx.diagnostics is None
    scheduler = BatchScheduler(
        BatchRunSpec(name="synthetic"),
        ctx,
        SessionManager(tmp_path / "users"),
        lambda: False,
    )
    engine = scheduler._create_engine()
    assert engine.execution_scope is None and engine.capture_observer is None
    assert engine._capture is capture and engine._input is backend
    assert engine.runtime_clock is None and engine._pause_event is pause


def test_enabled_scope_also_leaves_input_window_registry_and_clock_unwrapped(tmp_path):
    capture, backend = Capture(), Input()
    engine = WorkflowEngine(capture=capture, input_ctrl=backend, ocr=None, layout=None)
    wf = tmp_path / "task.wf"
    wf.write_text('screenshot\npress "ESC" down\nreturn 0\n', encoding="utf-8")
    engine.execute(wf)
    registry = engine._key_registry
    service = diag.TaskDiagnostics()
    service.options = diag.DiagnosticOptions(True)
    clock = SimpleNamespace(monotonic=lambda: 1.0, sleep=lambda seconds: None)
    engine.runtime_clock = clock

    def rebind(line, variables):
        assert engine._capture is capture and engine._input is backend
        assert engine._key_registry is registry and registry._backend is backend
        if line == 2:
            engine.rebind_target_window(
                {"hwnd": 2, "left": 3, "top": 4, "width": 16, "height": 16}
            )
            # Synthetic consumption at the public snapshot seam, not real OCR.
            engine.capture_frame(source="synthetic_consumption")

    engine.statement_hook = rebind
    engine.execution_scope = service.freeze().scope(task_run_id="synthetic-on")
    engine.execute(wf)
    history = service.latest
    assert history.snapshot()["frames"] == 1 and history.snapshot()["state"] == "closed"
    assert backend.actions[-2:] == [(2, "down", "ESC"), (2, "up", "ESC")]
    assert capture.region == (3, 4, 16, 16)
    assert engine._key_registry is registry and registry._backend is backend
    assert engine.execution_scope is None and engine.capture_observer is None
    assert engine.runtime_clock is clock
    service.options = diag.DiagnosticOptions()
    engine.execute(wf)
    assert service.latest is history and history.snapshot()["frames"] == 1
    assert not history._worker.is_alive()
