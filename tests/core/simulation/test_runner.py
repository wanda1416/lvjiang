"""真实WF/真实随包OCR走模拟设备，fresh-process隔离且失败不能放行。"""

import hashlib
import json
from pathlib import Path

from lvjiang.core.simulation.demo import make_demo
from lvjiang.core.simulation.runner import run_cases


def change_workflow(root: Path, code: str):
    wf = root / "task.wf"
    wf.write_text(code, encoding="utf-8")
    p = root / "scenario.json"
    spec = json.loads(p.read_text(encoding="utf-8"))
    spec["workflow_sha256"] = hashlib.sha256(wf.read_bytes()).hexdigest()
    p.write_text(json.dumps(spec), encoding="utf-8")


def test_parallel_real_ocr_navigation_and_wrong_click_do_not_share_state(tmp_path):
    good, wrong = tmp_path / "good", tmp_path / "wrong"
    make_demo(good)
    make_demo(wrong)
    # Real OCR still runs, but this deliberately incorrect input must not get the done frame.
    change_workflow(
        wrong,
        (wrong / "task.wf")
        .read_text(encoding="utf-8")
        .replace("click $start", "click (0.8, 0.8)"),
    )
    good_result, bad_result = run_cases(
        [good, wrong], tmp_path / "results", jobs=2, timeout=30, record=True
    )
    assert good_result["status"] == "passed"
    assert good_result["provenance"] == "synthetic"
    assert good_result["virtual_seconds"] == 1
    assert bad_result["status"] != "passed"
    assert bad_result["final_state"] == "start"
    assert good_result["case_directory"] != bad_result["case_directory"]
    trace = Path(good_result["case_directory"]) / "workflow.log"
    assert "12345" in trace.read_text(encoding="utf-8")
    assert "67890" in trace.read_text(encoding="utf-8")
    # Real WF+OCR collection -> immutable raw tape -> explicit human-style graph -> replay.
    from lvjiang.core.simulation.recording import annotation_template, compile_tape

    tape = Path(good_result["case_directory"]) / "recording"
    assert good_result["recording_complete"] is True
    sidecar = tmp_path / "annotations.json"
    annotation_template(tape, sidecar)
    annotation = json.loads(sidecar.read_text(encoding="utf-8"))
    frames = [
        json.loads(line)
        for line in (tape / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line)["kind"] == "capture"
    ]
    assert len(frames) == 3
    annotation.update(
        frames={
            str(frame["event_seq"]): state
            for frame, state in zip(frames, ["start", "loading", "done"], strict=True)
        },
        start="start",
        edges=json.loads((good / "scenario.json").read_text(encoding="utf-8"))["edges"],
    )
    sidecar.write_text(json.dumps(annotation), encoding="utf-8")
    compiled = tmp_path / "compiled"
    compile_tape(
        tape,
        compiled,
        workflow=good / "task.wf",
        terminal=["done"],
        annotations=sidecar,
    )
    rerun = run_cases([compiled], tmp_path / "rerun", jobs=1)[0]
    assert rerun["status"] == "passed"
    assert rerun["provenance"] == "synthetic"


def test_unrecorded_plugin_capability_and_early_success_are_not_pass(tmp_path):
    unsupported, premature = tmp_path / "unsupported", tmp_path / "premature"
    make_demo(unsupported)
    make_demo(premature)
    change_workflow(unsupported, 'eval confirm("not allowed")\nreturn 0\n')
    change_workflow(premature, "return 0\n")
    failed = tmp_path / "business-failed"
    make_demo(failed)
    change_workflow(failed, "return -1\n")
    spec = json.loads((failed / "scenario.json").read_text(encoding="utf-8"))
    spec["terminal"] = ["start"]
    (failed / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    results = run_cases([unsupported, premature, failed], tmp_path / "results", jobs=2)
    assert results[2]["status"] == "failed"
    assert results[2]["code"] == "business_failure"
    assert results[0]["status"] == "unsupported"
    assert results[1]["status"] == "failed"
    assert results[1]["code"] == "nonterminal_state"


def test_wall_watchdog_kills_case_instead_of_hanging(tmp_path):
    root = tmp_path / "case"
    make_demo(root)
    result = run_cases([root], tmp_path / "results", timeout=0.001)[0]
    assert result["status"] == "timeout"


def test_real_press_hold_uses_virtual_time_and_exact_release_edge(tmp_path):
    from tests.core.simulation.test_state_device import make_case

    root = make_case(tmp_path / "keys")
    change_workflow(root, 'press "ESC" hold 1\nreturn 0\n')
    path = root / "scenario.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    spec["edges"] = [
        {
            "source": "start",
            "target": "loading",
            "action": "key_down",
            "match": {"key": "ESC"},
        },
        {
            "source": "loading",
            "target": "done",
            "action": "key_up",
            "match": {"key": "ESC"},
            "min_elapsed": 1,
            "max_elapsed": 1,
        },
    ]
    path.write_text(json.dumps(spec), encoding="utf-8")
    result = run_cases([root], tmp_path / "result", jobs=1)[0]
    assert result["status"] == "passed"
    assert result["virtual_seconds"] == 1


def test_worker_guard_blocks_network_process_and_outside_io(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path

    runtime, fixture, outside = (
        tmp_path / name for name in ("runtime", "fixture", "outside")
    )
    runtime.mkdir()
    fixture.mkdir()
    outside.write_text("synthetic protected value", encoding="utf-8")
    code = """
import socket, subprocess, sys
from pathlib import Path
from lvjiang.core.simulation.runner import _guard
runtime, fixture, outside = map(Path, sys.argv[1:])
_guard(runtime, fixture)
operations = [socket.socket, lambda: outside.read_text(encoding='utf-8'),
              lambda: outside.write_text('changed', encoding='utf-8'),
              lambda: subprocess.run([sys.executable, '-c', 'pass'], check=True)]
for operation in operations:
    try:
        operation()
    except PermissionError:
        continue
    raise AssertionError('guard allowed forbidden operation')
(runtime / 'allowed').write_text('isolated', encoding='utf-8')
print('guarded=4')
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(runtime), str(fixture), str(outside)],
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src"),
            "PYTHONIOENCODING": "utf-8",
            "PATH": os.environ.get("PATH", ""),
        },
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "guarded=4"
    assert outside.read_text(encoding="utf-8") == "synthetic protected value"
    assert (runtime / "allowed").read_text(encoding="utf-8") == "isolated"


def test_empty_fixture_selection_is_not_a_vacuous_pass(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="explicit fixture"):
        run_cases([], tmp_path / "no-cases")
    assert not (tmp_path / "no-cases").exists()


def test_explicit_attribute_snapshot_uses_real_builtin_and_missing_is_unsupported(
    tmp_path,
):
    case = tmp_path / "attributes"
    make_demo(case)
    wf = case / "task.wf"
    wf.write_text(
        'eval $level = user_get("synthetic-user", "level")\nif $level != 7 -> return -1\nreturn 0\n',
        encoding="utf-8",
    )
    spec = json.loads((case / "scenario.json").read_text(encoding="utf-8"))
    spec.update(
        workflow_sha256=hashlib.sha256(wf.read_bytes()).hexdigest(),
        edges=[],
        terminal=["start"],
        environment={"attributes": {"synthetic-user": {"level": 7}}},
    )
    (case / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    assert run_cases([case], tmp_path / "known")[0]["status"] == "passed"
    spec["environment"]["attributes"]["synthetic-user"] = {}
    (case / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    result = run_cases([case], tmp_path / "unknown")[0]
    assert (
        result["status"] == "unsupported"
        and result["code"] == "unsupported_environment"
    )
    wf.write_text('if env("desktop") -> return -1\nreturn 0\n', encoding="utf-8")
    spec["workflow_sha256"] = hashlib.sha256(wf.read_bytes()).hexdigest()
    (case / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    assert run_cases([case], tmp_path / "no-environment")[0]["status"] == "unsupported"
