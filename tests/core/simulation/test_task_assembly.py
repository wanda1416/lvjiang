"""Real Qt worker/batch composition with explicitly simulated hardware only."""

from types import SimpleNamespace


def test_desktop_start_freezes_diagnostic_options_and_task_identity(
    qtbot, tmp_path, monkeypatch
):
    from lvjiang import constants
    from lvjiang.core.config.models import InputSimConfig
    from lvjiang.core.layout.models import Layout
    from lvjiang.core.ocr.engine import OCREngine
    from lvjiang.core.simulation.demo import make_demo
    from lvjiang.core.simulation.scenario import Scenario, SimulatedDevice
    from lvjiang.core.task_diagnostics import DiagnosticOptions, TaskDiagnostics
    from lvjiang.ui.main.run_control import RunControlMixin
    from lvjiang.workflows.engine.builders import DeviceWorkflowEngineBuilder

    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    case = tmp_path / "demo"
    make_demo(case)
    device = SimulatedDevice(Scenario.load(case))
    ocr = OCREngine(
        rapidocr_options={"intra_op_num_threads": 1, "inter_op_num_threads": 1}
    )
    engine = DeviceWorkflowEngineBuilder(
        capture=device,
        input_ctrl=device,
        ocr=ocr,
        layout=Layout.from_dict("synthetic", {}),
        input_sim=InputSimConfig(
            click_random_offset=0, before_click_wait=(0, 0), after_click_wait=(0, 0)
        ),
    ).build()
    engine.runtime_clock = device
    diagnostics = TaskDiagnostics()
    diagnostics.options = DiagnosticOptions(enabled=True, max_frames=2)
    original_capture, original_input, original_clock = (
        engine._capture,
        engine._input,
        engine.runtime_clock,
    )
    done = []

    class Host(RunControlMixin):
        def _on_workflow_finished(self, run_id):
            done.append(run_id)

    host = Host()
    host._task_diagnostics = diagnostics
    host._current_engine = engine
    context = SimpleNamespace(
        task_run_id="desktop-task-synthetic",
        target_id="target-synthetic",
        metadata={},
        lease=None,
    )
    host._current_run_context = context
    host._start_workflow(
        "synthetic", "Synthetic task", lambda: engine.execute(case / "task.wf")
    )
    diagnostics.options = (
        DiagnosticOptions()
    )  # Already launched task keeps its frozen limits.
    qtbot.waitUntil(lambda: bool(done), timeout=15000)
    assert context.worker.wait(1000)
    ocr.close()
    assert done == ["desktop-task-synthetic"]
    assert engine.return_value == 0
    history = diagnostics.latest
    assert history is not None
    summary = history.snapshot()
    assert summary["task_run_id"] == "desktop-task-synthetic"
    assert summary["frames"] == 2 and summary["evicted"] == 1
    assert summary["max_frames"] == 2 and summary["state"] == "closed"
    assert engine._capture is original_capture and engine._input is original_input
    assert engine.runtime_clock is original_clock and engine.capture_observer is None
    assert not (tmp_path / "logs/recordings").exists()


def test_batch_real_dispatch_uses_history_task_and_batch_ids(
    tmp_path, monkeypatch, qapp
):
    from lvjiang import constants
    from lvjiang.core.batch.models import BatchContext, BatchRunSpec, BatchScript
    from lvjiang.core.batch.scheduler import BatchScheduler
    from lvjiang.core.config.users import SessionManager
    from lvjiang.core.layout.models import Layout
    from lvjiang.core.ocr.engine import OCREngine
    from lvjiang.core.simulation.demo import make_demo
    from lvjiang.core.simulation.scenario import Scenario, SimulatedDevice
    from lvjiang.core.task_diagnostics import DiagnosticOptions, TaskDiagnostics
    from lvjiang.core.users.document import User, save_user_metadata

    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    users = tmp_path / "users"
    save_user_metadata(User("synthetic-user"), users)
    case = tmp_path / "demo"
    make_demo(case)
    wf = case / "task.wf"
    device = SimulatedDevice(Scenario.load(case))
    ocr = OCREngine(
        rapidocr_options={"intra_op_num_threads": 1, "inter_op_num_threads": 1}
    )
    diagnostics = TaskDiagnostics()
    diagnostics.options = DiagnosticOptions(enabled=True)
    ctx = BatchContext(
        device,
        ocr,
        device,
        Layout.from_dict("synthetic", {}),
        task_run_id="batch-context-synthetic",
        target_id="synthetic-target",
        diagnostics=diagnostics.freeze(),
    )
    # A real builder still runs, with the simulated clock supplied at its seam.
    from lvjiang.core.batch import scheduler as scheduler_module

    real_build = scheduler_module.DeviceWorkflowEngineBuilder.build

    def build(builder):
        engine = real_build(builder)
        engine.runtime_clock = device
        return engine

    monkeypatch.setattr(scheduler_module.DeviceWorkflowEngineBuilder, "build", build)
    diagnostics.options = DiagnosticOptions()  # Batch launch snapshot must not change.
    spec = BatchRunSpec(
        name="synthetic",
        entries=("synthetic-user",),
        candidate_usernames=("synthetic-user",),
        scripts=(BatchScript("capture-test", "Synthetic", wf_file=str(wf)),),
        skip_lifecycle_for_single_item=True,
    )
    scheduler = BatchScheduler(spec, ctx, SessionManager(users), lambda: False)
    scheduler.run()
    ocr.close()
    task = diagnostics.latest.snapshot()
    assert task["task_run_id"] and task["task_run_id"] != "batch-context-synthetic"
    assert task["batch_run_id"] and task["target_scope"] == "synthetic-target"
    assert task["task_status"] == "completed" and task["frames"] == 3
    assert task["state"] == "closed"
    assert not (tmp_path / "logs/recordings").exists()


def test_real_batch_tab_has_no_capture_control(qtbot, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal

    from lvjiang.core.config.models import UserConfig
    from lvjiang.ui.batch.batch_tab import BatchTab

    class Host(QObject):
        automation_state_changed = pyqtSignal(str)
        _user_config = UserConfig()

    monkeypatch.setattr(BatchTab, "_refresh_config_combo", lambda self: None)
    monkeypatch.setattr(BatchTab, "_refresh_group_contents", lambda self: None)
    tab = BatchTab(Host())
    qtbot.addWidget(tab)
    from PyQt6.QtWidgets import QCheckBox

    assert not hasattr(tab, "consume_full_task_capture")
    assert all("全帧采集" not in check.text() for check in tab.findChildren(QCheckBox))
