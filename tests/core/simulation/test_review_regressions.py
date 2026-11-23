"""Real regressions: warm input caches, window reassignment, and stale catalogs."""

import hashlib
import json
from pathlib import Path

import numpy as np

from lvjiang.core.simulation.bundle import bundle_workflow
from lvjiang.core.simulation.catalog import discover, run_discovered
from lvjiang.core.simulation.demo import make_demo
from lvjiang.core.simulation.host import recording_scope
from lvjiang.workflows.engine import WorkflowEngine


class Capture:
    min_capture_interval = 0

    def capture(self):
        return np.zeros((16, 16, 3), np.uint8)

    def get_capture_size(self):
        return 16, 16

    def set_capture_region(self, *args):
        self.region = args


class Input:
    target_hwnd = 1

    def stop_check(self):
        return False

    def __init__(self):
        self.actions = []

    def key_down(self, key):
        self.actions.append((self.target_hwnd, "down", key))

    def key_up(self, key):
        self.actions.append((self.target_hwnd, "up", key))


def test_warm_and_repeated_registry_record_every_key_and_restore_backend(tmp_path):
    backend = Input()
    engine = WorkflowEngine(
        capture=Capture(), input_ctrl=backend, ocr=None, layout=None
    )
    wf = tmp_path / "task.wf"
    # Deliberately leave a key down: real execute.finally must release through the recording.
    wf.write_text('screenshot\npress "ESC" down\nreturn 0\n', encoding="utf-8")
    engine.execute(wf)
    registry = engine._key_registry
    for number in (1, 2):
        engine.execution_scope = recording_scope(
            tmp_path / "records",
            task_run_id=f"synthetic-{number}",
            provenance="synthetic",
        )
        engine.execute(wf)
        record = Path(engine.full_capture_status["directory"])
        events = [
            json.loads(line)
            for line in (record / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        actions = [
            event["action"] for event in events if event["kind"] == "input_requested"
        ]
        assert actions == ["key_down", "key_up"]
        assert json.loads((record / "manifest.json").read_text(encoding="utf-8"))[
            "complete"
        ]
        assert engine._key_registry is registry and registry._backend is backend
        assert not registry.is_pressed("ESC")
    engine.execute(wf)
    assert len(list((tmp_path / "records").iterdir())) == 2
    assert backend.actions == [
        (1, direction, "ESC") for _ in range(4) for direction in ("down", "up")
    ]


def test_window_rebind_under_recording_updates_real_input_and_capture(tmp_path):
    capture, backend = Capture(), Input()
    engine = WorkflowEngine(capture=capture, input_ctrl=backend, ocr=None, layout=None)
    wf = tmp_path / "task.wf"
    wf.write_text('screenshot\npress "ESC" down\nreturn 0\n', encoding="utf-8")

    def rebind(line, _variables):
        if line == 2:
            engine.rebind_target_window(
                {"hwnd": 2, "left": 3, "top": 4, "width": 16, "height": 16}
            )

    engine.statement_hook = rebind
    engine.execution_scope = recording_scope(
        tmp_path / "records", task_run_id="synthetic-rebind", provenance="synthetic"
    )
    engine.execute(wf)
    assert backend.target_hwnd == 2 and capture.region == (3, 4, 16, 16)
    assert backend.actions == [(2, "down", "ESC"), (2, "up", "ESC")]
    assert engine._input is backend and engine._key_registry._backend is backend


def test_saved_catalog_rechecks_current_source_dependencies_and_fixture_layout(
    tmp_path,
):
    case = tmp_path / "fixtures/case"
    make_demo(case)
    root = tmp_path / "source"
    (root / "lib").mkdir(parents=True)
    library = root / "lib/shared.wf"
    library.write_text("def helper()\nreturn 0\nend\n", encoding="utf-8")
    source = root / "task.wf"
    original = 'import "lib/shared.wf"\ncall $result = helper()\nreturn $result\n'
    source.write_text(original, encoding="utf-8")
    (case / "task.wf").write_bytes(source.read_bytes())
    bundle_workflow(source, case / "bundle", roots=[root])
    spec_path = case / "scenario.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec.update(
        workflow_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        workflow_bundle="bundle",
        terminal=["start"],
        edges=[],
        layout={"canvas": {"width": 640, "height": 160}},
    )
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(discover(root, case.parent)), encoding="utf-8")
    assert run_discovered(catalog, tmp_path / "unchanged")["all_required_passed"]
    source.write_text("return -1\n", encoding="utf-8")
    stale = run_discovered(catalog, tmp_path / "changed-source")
    assert not stale["all_required_passed"] and stale["counts"]["passed"] == 0
    source.write_text(original, encoding="utf-8")
    library.write_text("def helper()\nreturn -1\nend\n", encoding="utf-8")
    assert not run_discovered(catalog, tmp_path / "changed-dependency")[
        "all_required_passed"
    ]
    library.unlink()
    assert not run_discovered(catalog, tmp_path / "missing-dependency")[
        "all_required_passed"
    ]
    library.write_text("def helper()\nreturn 0\nend\n", encoding="utf-8")
    spec["layout"]["canvas"]["width"] = 320
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    assert not run_discovered(catalog, tmp_path / "changed-layout")[
        "all_required_passed"
    ]
    spec["layout"]["canvas"]["width"] = 640
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    assert run_discovered(catalog, tmp_path / "restored")["all_required_passed"]
