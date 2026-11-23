"""状态设备必须被动作驱动，不允许截图次数偷偷推进。"""

import hashlib
import json

import cv2
import numpy as np
import pytest

from lvjiang.core.simulation.scenario import Scenario, SimulatedDevice, SimulationError


def make_case(root):
    root.mkdir(exist_ok=True)
    frames = {}
    for name, color in [
        ("start", (0, 0, 255)),
        ("loading", (0, 0, 255)),
        ("done", (0, 255, 0)),
    ]:
        data = cv2.imencode(".png", np.full((100, 100, 3), color, np.uint8))[
            1
        ].tobytes()
        (root / f"{name}.png").write_bytes(data)
        frames[name] = {
            "frame": f"{name}.png",
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    wf = root / "task.wf"
    wf.write_text("click (0.5, 0.5)\nwait 1\nreturn 0\n", encoding="utf-8")
    spec = {
        "format_version": 1,
        "provenance": "synthetic",
        "workflow": "task.wf",
        "workflow_sha256": hashlib.sha256(wf.read_bytes()).hexdigest(),
        "start": "start",
        "states": frames,
        "terminal": ["done"],
        "edges": [
            {
                "source": "start",
                "target": "loading",
                "action": "click",
                "match": {"box": [45, 45, 55, 55], "button": "left"},
            },
            {"source": "loading", "target": "done", "action": "timer", "after": 1},
        ],
        "expected_return": 0,
        "layout": {},
        "limits": {"events": 100, "virtual_seconds": 5},
    }
    (root / "scenario.json").write_text(json.dumps(spec), encoding="utf-8")
    return root


def test_capture_does_not_advance_and_same_picture_can_mean_different_state(tmp_path):
    device = SimulatedDevice(Scenario.load(make_case(tmp_path)))
    for _ in range(3):
        assert device.capture()[0, 0].tolist() == [0, 0, 255]
    assert device.state == "start"
    device.click_screen(50, 50)
    assert device.state == "loading"
    assert device.capture()[0, 0].tolist() == [0, 0, 255]
    device.sleep(0.9)
    assert device.state == "loading"
    device.sleep(0.1)
    assert device.state == "done"
    device.assert_finished()


def test_wrong_click_never_advances_and_failure_is_sticky(tmp_path):
    device = SimulatedDevice(Scenario.load(make_case(tmp_path)))
    with pytest.raises(SimulationError):
        device.click_screen(1, 1)
    assert device.state == "start"
    with pytest.raises(SimulationError):
        device.assert_finished()


def test_missing_or_corrupt_frame_rejected_before_execution(tmp_path):
    root = make_case(tmp_path)
    (root / "done.png").write_bytes(b"broken")
    with pytest.raises(SimulationError):
        Scenario.load(root)


def test_unknown_branch_timeout_and_ambiguous_edge_are_not_pass(tmp_path):
    root = make_case(tmp_path)
    device = SimulatedDevice(Scenario.load(root))
    with pytest.raises(SimulationError):
        device.key_down("ESC")
    device = SimulatedDevice(Scenario.load(root))
    with pytest.raises(SimulationError):
        device.sleep(6)
    p = root / "scenario.json"
    spec = json.loads(p.read_text(encoding="utf-8"))
    spec["edges"].append(spec["edges"][0])
    p.write_text(json.dumps(spec), encoding="utf-8")
    device = SimulatedDevice(Scenario.load(root))
    with pytest.raises(SimulationError, match="ambiguous"):
        device.click_screen(50, 50)


def test_stop_and_postload_corruption_are_sticky(tmp_path):
    root = make_case(tmp_path)
    device = SimulatedDevice(Scenario.load(root))
    device.stopped = True
    with pytest.raises(SimulationError, match="stopped"):
        device.capture()
    device = SimulatedDevice(Scenario.load(root))
    (root / "start.png").write_bytes(b"broken after initial validation")
    with pytest.raises(SimulationError):
        device.capture()
    with pytest.raises(SimulationError):
        device.assert_finished()


def test_action_time_window_and_unrecorded_hold_are_strict(tmp_path):
    root = make_case(tmp_path)
    device = SimulatedDevice(Scenario.load(root))
    with pytest.raises(SimulationError):
        device.click_screen(50, 50, hold=1)
    assert device.state == "start"
    p = root / "scenario.json"
    spec = json.loads(p.read_text(encoding="utf-8"))
    spec["edges"][0]["max_elapsed"] = 0.5
    p.write_text(json.dumps(spec), encoding="utf-8")
    device = SimulatedDevice(Scenario.load(root))
    device.sleep(0.6)
    with pytest.raises(SimulationError):
        device.click_screen(50, 50)
    assert device.state == "start"
