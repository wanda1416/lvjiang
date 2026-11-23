"""Real libraries and real task composition, synthetic hardware/frames only."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from lvjiang.core.simulation.bundle import bundle_workflow, verify_bundle
from lvjiang.core.simulation.demo import make_demo
from lvjiang.core.simulation.host import recording_scope
from lvjiang.core.simulation.runner import run_cases
from lvjiang.core.simulation.scenario import SimulationError

ROOT = Path(__file__).resolve().parents[3]


def test_real_system_numeric_library_and_real_ocr_execute_from_pinned_bundle(tmp_path):
    case = tmp_path / "case"
    make_demo(case)
    workflow = case / "task.wf"
    code = (
        'import "lib/numeric.wf"\ncall $g = gcd(54, 24)\nif $g != 6 -> return -1\n'
        + workflow.read_text(encoding="utf-8")
    )
    workflow.write_text(code, encoding="utf-8")
    bundle_workflow(
        workflow, case / "bundle", roots=[case, ROOT / "config/system/workflows"]
    )
    spec = json.loads((case / "scenario.json").read_text(encoding="utf-8"))
    spec.update(
        workflow_sha256=hashlib.sha256(workflow.read_bytes()).hexdigest(),
        workflow_bundle="bundle",
    )
    (case / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    result = run_cases([case], tmp_path / "results", record=True)[0]
    assert result["status"] == "passed", result
    assert result["recording_complete"]
    assert (
        Path(result["case_directory"])
        / "recording/workflow_bundle/workflows/lib/numeric.wf"
    ).read_bytes() == (ROOT / "config/system/workflows/lib/numeric.wf").read_bytes()
    # Tampered dependency never reaches the interpreter/OCR.
    (case / "bundle/workflows/lib/numeric.wf").write_text(
        "return 0\n", encoding="utf-8"
    )
    assert run_cases([case], tmp_path / "bad")[0]["status"] != "passed"


def test_bundle_rejects_python_escape_cycle_and_corruption(tmp_path):
    root = tmp_path / "wf"
    root.mkdir()
    entry = root / "task.wf"
    entry.write_text('import "../outside.wf"\nreturn 0\n', encoding="utf-8")
    with pytest.raises(SimulationError):
        bundle_workflow(entry, tmp_path / "escape", roots=[root])
    entry.write_text('import "tool.py"\nreturn 0\n', encoding="utf-8")
    with pytest.raises(SimulationError):
        bundle_workflow(entry, tmp_path / "python", roots=[root])
    entry.write_text('import "task.wf"\nreturn 0\n', encoding="utf-8")
    with pytest.raises(SimulationError, match="cycle"):
        bundle_workflow(entry, tmp_path / "cycle", roots=[root])
    entry.write_text("return 0\n", encoding="utf-8")
    bundle_workflow(entry, tmp_path / "good", roots=[root])
    assert verify_bundle(tmp_path / "good")["entry"] == "task.wf"


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_real_engine_scope_closes_and_restores_backends(tmp_path, outcome):
    from lvjiang.workflows.engine import WorkflowEngine

    stopped = [False]

    class Capture:
        def capture(self):
            if outcome == "cancelled":
                stopped[0] = True
            return np.zeros((16, 16, 3), np.uint8)

    capture = Capture()
    engine = WorkflowEngine(
        capture=capture,
        input_ctrl=None,
        ocr=None,
        layout=None,
        stop_check=lambda: stopped[0],
    )
    workflow = tmp_path / "task.wf"
    workflow.write_text(
        "screenshot\nreturn " + ("-1" if outcome == "failed" else "0") + "\n",
        encoding="utf-8",
    )
    engine.execution_scope = recording_scope(
        tmp_path / "records",
        task_run_id="task-synthetic",
        batch_run_id="batch-synthetic",
        target_scope="target-synthetic",
        provenance="synthetic",
    )
    engine.execute(workflow)
    assert engine._capture is capture
    assert engine.full_capture_status["state"] == "complete"
    assert engine.full_capture_status["task_status"] == outcome
    manifest = json.loads(
        next((tmp_path / "records").glob("*/manifest.json")).read_text(encoding="utf-8")
    )
    assert manifest["complete"] and manifest["frames"] == 1
    assert manifest["task_run_id"] == "task-synthetic"
    assert manifest["batch_run_id"] == "batch-synthetic"
    assert manifest["task_status"] == outcome


def test_recorder_setup_failure_does_not_change_actual_business_result(
    tmp_path, monkeypatch
):
    import lvjiang.core.simulation.host as host
    from lvjiang.workflows.engine import WorkflowEngine

    def fail(*_a, **_k):
        raise OSError("synthetic disk full")

    monkeypatch.setattr(host, "FullTaskRecorder", fail)
    workflow = tmp_path / "task.wf"
    workflow.write_text("return 7\n", encoding="utf-8")
    engine = WorkflowEngine(capture=None, input_ctrl=None, ocr=None, layout=None)
    engine.execution_scope = recording_scope(
        tmp_path / "records", task_run_id="synthetic"
    )
    engine.execute(workflow)
    assert engine.return_value == 7
    assert engine.full_capture_status["state"] == "incomplete"


def test_unchanged_system_page_detection_with_actual_chinese_ocr(tmp_path):
    # Checked-in synthetic raster (Noto Sans CJK); no platform font dependency or real game pixels.
    case = tmp_path / "menu-case"
    make_demo(case)
    (case / "menu.png").write_bytes(
        (Path(__file__).parent / "assets/synthetic-menu.png").read_bytes()
    )
    wf = case / "task.wf"
    wf.write_text(
        'import "subcall/page_detection.wf"\ncall $menu = is_in_menu_page(0)\nif $menu -> return 0\nreturn -1\n',
        encoding="utf-8",
    )
    bundle_workflow(wf, case / "bundle", roots=[case, ROOT / "config/system/workflows"])
    spec = json.loads((case / "scenario.json").read_text(encoding="utf-8"))
    spec.update(
        workflow_sha256=hashlib.sha256(wf.read_bytes()).hexdigest(),
        workflow_bundle="bundle",
        start="menu",
        terminal=["menu"],
        edges=[],
        run_env="desktop",
        states={
            "menu": {
                "frame": "menu.png",
                "sha256": hashlib.sha256((case / "menu.png").read_bytes()).hexdigest(),
            }
        },
        layout={
            "canvas": {"width": 640, "height": 160},
            "scenes": {
                "game_menu_page": {
                    "regions": [
                        {
                            "key": "baoguo",
                            "x_ratio": 0,
                            "y_ratio": 0,
                            "w_ratio": 0.5,
                            "h_ratio": 1,
                        },
                        {
                            "key": "peiyang",
                            "x_ratio": 0.5,
                            "y_ratio": 0,
                            "w_ratio": 0.5,
                            "h_ratio": 1,
                        },
                    ]
                }
            },
        },
    )
    (case / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    result = run_cases([case], tmp_path / "page-results")[0]
    assert result["status"] == "passed", result
    assert "包裹" in (Path(result["case_directory"]) / "workflow.log").read_text(
        encoding="utf-8"
    )


def test_cached_facade_wait_stable_and_python_source_are_recorded_and_restored(
    tmp_path,
):
    from lvjiang.workflows.base import BaseWorkflow
    from lvjiang.workflows.engine import WorkflowEngine

    class Capture:
        def capture(self):
            return np.zeros((16, 16, 3), np.uint8)

    backend = Capture()
    engine = WorkflowEngine(capture=backend, input_ctrl=None, ocr=None, layout=None)
    facade = engine._ensure_workflow()
    wf = tmp_path / "task.wf"
    wf.write_text(
        "wait stable 1 duration 0.02 interval 0.01 least 0\nreturn 0\n",
        encoding="utf-8",
    )
    engine.execution_scope = recording_scope(
        tmp_path / "records", task_run_id="scope-one", provenance="synthetic"
    )
    engine.execute(wf)
    assert facade._capture is backend
    assert engine.full_capture_status["state"] == "complete"
    assert engine.execution_scope is None
    engine.execute(
        wf
    )  # Unchecked next launch cannot inherit the prior recorder/identity.
    assert len(list((tmp_path / "records").iterdir())) == 1

    class Flow(BaseWorkflow):
        def run(self):
            self.capture_frame(source="python")
            return {}

    flow = Flow(capture=backend, input_ctrl=None, ocr=None, layout=None)
    engine.execution_scope = recording_scope(
        tmp_path / "records", task_run_id="scope-two", provenance="synthetic"
    )
    engine.execute(flow)
    assert flow._capture is backend
    manifests = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in (tmp_path / "records").glob("*/manifest.json")
    ]
    assert len(manifests) == 2 and all(
        m["complete"] and m["frames"] >= 1 for m in manifests
    )


def test_capture_layout_and_reviewed_cli_context_are_usable_without_live_config(
    tmp_path,
):
    import subprocess
    import sys

    from lvjiang.core.layout.models import Layout
    from lvjiang.core.simulation.recording import annotation_template
    from lvjiang.core.simulation.scenario import Scenario
    from lvjiang.workflows.engine import WorkflowEngine

    class Capture:
        def capture(self):
            return np.zeros((16, 16, 3), np.uint8)

    workflow = tmp_path / "task.wf"
    workflow.write_text(
        "screenshot\nif $probe != 7 -> return -1\nif is_unattended() -> return -1\nreturn 0\n",
        encoding="utf-8",
    )
    layout = Layout.from_dict("synthetic", {"scenes": {"sample": {"regions": []}}})
    engine = WorkflowEngine(
        capture=Capture(), input_ctrl=None, ocr=None, layout=layout, unattended=False
    )
    engine.execution_scope = recording_scope(
        tmp_path / "records", task_run_id="synthetic", provenance="synthetic"
    )
    engine.execute(workflow, initial_variables={"probe": 7})
    tape = next((tmp_path / "records").iterdir())
    annotations = tmp_path / "annotations.json"
    annotation_template(tape, annotations)
    context = tmp_path / "context.json"
    context.write_text(
        json.dumps({"params": {"probe": 7}, "environment": {"unattended": False}}),
        encoding="utf-8",
    )
    command = [
        sys.executable,
        "-B",
        str(ROOT / "scripts/simulate_workflows.py"),
        "compile",
        str(tape),
        str(tmp_path / "fixture"),
        "--workflow",
        str(workflow),
        "--terminal",
        "state_1",
        "--annotations",
        str(annotations),
        "--context",
        str(context),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=20
    )
    assert result.returncode == 0, result.stderr
    spec = Scenario.load(tmp_path / "fixture").spec
    assert spec["layout"] == layout.to_dict() and spec["params"] == {"probe": 7}
    assert (
        run_cases([tmp_path / "fixture"], tmp_path / "results")[0]["status"] == "passed"
    )


def test_recorder_close_failure_keeps_business_return_and_scope_restoration(
    tmp_path, monkeypatch
):
    import lvjiang.core.simulation.host as host
    from lvjiang.workflows.engine import WorkflowEngine

    original = host.FullTaskRecorder.close

    def close_then_fail(self):
        original(self)
        raise OSError("synthetic close failure")

    monkeypatch.setattr(host.FullTaskRecorder, "close", close_then_fail)
    wf = tmp_path / "task.wf"
    wf.write_text("return 7\n", encoding="utf-8")
    engine = WorkflowEngine(capture=None, input_ctrl=None, ocr=None, layout=None)
    engine.execution_scope = recording_scope(
        tmp_path / "records", task_run_id="synthetic", provenance="synthetic"
    )
    engine.execute(wf)
    assert engine.return_value == 7 and engine._capture is None
    assert engine.full_capture_status["state"] == "incomplete"
