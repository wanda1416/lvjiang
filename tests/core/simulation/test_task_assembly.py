"""Real Qt worker/batch composition with explicitly simulated hardware only."""

import json
from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QCheckBox


def test_desktop_start_freezes_checkbox_and_task_identity(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.core.config.models import InputSimConfig
    from lvjiang.core.layout.models import Layout
    from lvjiang.core.ocr.engine import OCREngine
    from lvjiang.core.simulation.demo import make_demo
    from lvjiang.core.simulation.scenario import Scenario, SimulatedDevice
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
    checkbox = QCheckBox()
    qtbot.addWidget(checkbox)
    assert not checkbox.isChecked()
    checkbox.setChecked(True)
    done = []

    class Host(RunControlMixin):
        def _on_workflow_finished(self, run_id):
            done.append(run_id)

    host = Host()
    host.chk_full_task_capture = checkbox
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
    assert not checkbox.isChecked()
    checkbox.setChecked(True)
    host.chk_full_task_capture = None
    qtbot.waitUntil(lambda: bool(done), timeout=15000)
    assert context.worker.wait(1000)
    ocr.close()
    assert done == ["desktop-task-synthetic"]
    assert engine.return_value == 0
    manifest = json.loads(
        next((tmp_path / "logs/recordings").glob("*/manifest.json")).read_text(
            encoding="utf-8"
        )
    )
    assert manifest["task_run_id"] == "desktop-task-synthetic"
    assert manifest["complete"] and manifest["frames"] == 3
    assert context.metadata["full_task_capture"] is True


def test_batch_real_dispatch_uses_history_task_and_batch_ids(
    tmp_path, monkeypatch, qapp
):
    import numpy as np

    from lvjiang import constants
    from lvjiang.core.batch.models import BatchContext, BatchRunSpec, BatchScript
    from lvjiang.core.batch.scheduler import BatchScheduler
    from lvjiang.core.config.users import SessionManager
    from lvjiang.core.users.document import User, save_user_metadata

    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    users = tmp_path / "users"
    save_user_metadata(User("synthetic-user"), users)
    wf = tmp_path / "task.wf"
    wf.write_text("screenshot\nreturn 0\n", encoding="utf-8")

    class Capture:
        def capture(self):
            return np.zeros((16, 16, 3), np.uint8)

    ctx = BatchContext(
        Capture(),
        None,
        None,
        None,
        task_run_id="batch-context-synthetic",
        target_id="synthetic-target",
        full_task_capture=True,
    )
    spec = BatchRunSpec(
        name="synthetic",
        entries=("synthetic-user",),
        candidate_usernames=("synthetic-user",),
        scripts=(BatchScript("capture-test", "Synthetic", wf_file=str(wf)),),
        skip_lifecycle_for_single_item=True,
    )
    scheduler = BatchScheduler(spec, ctx, SessionManager(users), lambda: False)
    scheduler.run()
    manifests = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "logs/recordings").glob("*/manifest.json")
    ]
    assert manifests and all(m["complete"] for m in manifests)
    task = next(m for m in manifests if m["frames"] == 1)
    assert task["task_run_id"] and task["task_run_id"] != "batch-context-synthetic"
    assert task["batch_run_id"]
    assert task["task_status"] == "completed"


def test_real_batch_tab_capture_option_is_transient(qtbot, monkeypatch):
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
    assert not tab._full_task_capture_check.isChecked()
    tab._full_task_capture_check.setChecked(True)
    assert tab.consume_full_task_capture() is True
    assert tab.consume_full_task_capture() is False


@pytest.fixture(autouse=True)
def _synthetic_recording_provenance(monkeypatch):
    from functools import partial

    import lvjiang.core.simulation.host as recording_host

    monkeypatch.setattr(
        recording_host,
        "recording_scope",
        partial(recording_host.recording_scope, provenance="synthetic"),
    )
