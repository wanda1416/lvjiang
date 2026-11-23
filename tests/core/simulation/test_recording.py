"""全任务记录不轮转为100帧，丢记录必须诚实标不完整。"""

import json

import numpy as np
import pytest

from lvjiang.core.simulation.recording import FullTaskRecorder, compile_tape
from lvjiang.core.simulation.scenario import Scenario, SimulationError


def test_full_tape_exceeds_100_and_compiles_only_explicit_edges(tmp_path):
    wf = tmp_path / "task.wf"
    wf.write_text("return 0\n", encoding="utf-8")
    tape = FullTaskRecorder(tmp_path / "tape", workflow=wf, provenance="synthetic")
    image = np.zeros((16, 16, 3), np.uint8)
    for seq in range(1, 121):
        assert tape.capture(image, state_id="idle", capture_seq=seq, engine_epoch="e1")
        assert tape.flush(5)
    tape.edge("idle", "done", "click", match={"box": [0, 0, 10, 10], "button": "left"})
    tape.capture(image, state_id="done", capture_seq=121, engine_epoch="e1")
    tape.close()
    manifest = json.loads(
        (tape.directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["complete"] is True
    assert manifest["frames"] == 121
    assert len(list((tape.directory / "frames").iterdir())) == 121
    compile_tape(tape.directory, tmp_path / "case", workflow=wf, terminal=["done"])
    scenario = Scenario.load(tmp_path / "case")
    assert set(scenario.spec["states"]) == {"idle", "done"}
    assert len(scenario.spec["edges"]) == 1


def test_raw_or_disk_limit_never_claims_complete(tmp_path):
    tape = FullTaskRecorder(
        tmp_path / "limited", max_raw_bytes=2, provenance="synthetic"
    )
    assert tape.capture(np.zeros((16, 16, 3), np.uint8), state_id="x") is False
    tape.close()
    m = json.loads((tape.directory / "manifest.json").read_text(encoding="utf-8"))
    assert m["complete"] is False
    assert "raw_budget" in m["reason"]
    with pytest.raises(SimulationError):
        compile_tape(
            tape.directory,
            tmp_path / "no",
            workflow=tmp_path / "none.wf",
            terminal=["x"],
        )


def test_async_recording_owns_copy_and_unlabelled_is_not_auto_scenario(tmp_path):
    tape = FullTaskRecorder(tmp_path / "copy", provenance="synthetic")
    frame = np.zeros((16, 16, 3), np.uint8)
    assert tape.capture(frame)
    frame[:] = 255
    tape.close()
    import cv2

    saved = next((tape.directory / "frames").iterdir())
    assert (
        cv2.imdecode(
            np.frombuffer(saved.read_bytes(), np.uint8), cv2.IMREAD_COLOR
        ).max()
        == 0
    )
    with pytest.raises(SimulationError):
        compile_tape(
            tape.directory,
            tmp_path / "no",
            workflow=tmp_path / "none.wf",
            terminal=["x"],
        )


def test_full_backend_getters_and_from_last_keep_correct_frame_key(tmp_path):
    from lvjiang.workflows.engine import WorkflowEngine

    class Capture:
        min_capture_interval = 0.0

        def capture(self):
            return np.zeros((16, 16, 3), np.uint8)

        def get_capture_size(self):
            return 16, 16

    tape = FullTaskRecorder(tmp_path / "whole", max_pending=8, provenance="synthetic")
    capture = tape.wrap_capture(Capture(), "e1")
    engine = WorkflowEngine(
        capture=capture,
        input_ctrl=None,
        ocr=None,
        layout=None,
        capture_observer=tape.observer("e1", capture=capture),
    )
    engine.capture_frame(source="recognize")
    # wait-stable and other backend samples don't replace the engine's last snapshot.
    capture.capture()
    with engine.capture_source(from_last=True):
        engine.capture_frame(source="find")
    tape.close()
    events = [
        json.loads(line)
        for line in (tape.directory / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    frames = [e for e in events if e["kind"] == "capture"]
    uses = [e for e in events if e["kind"] == "capture_use"]
    assert len(frames) == 2
    assert [u["device_seq"] for u in uses] == [1, 1]
    assert [u["reused"] for u in uses] == [False, True]


def test_observer_failure_does_not_replace_recognition_frame():
    from lvjiang.workflows.engine import WorkflowEngine

    class Capture:
        def capture(self):
            return np.zeros((8, 8, 3), np.uint8)

    def broken(_meta, _image):
        raise OSError("synthetic disk failure")

    engine = WorkflowEngine(
        capture=Capture(),
        input_ctrl=None,
        ocr=None,
        layout=None,
        capture_observer=broken,
    )
    assert engine.capture_frame().shape == (8, 8, 3)
    assert engine.capture_observer is None


def test_bounded_queue_overflow_and_disk_quota_mark_incomplete(tmp_path, monkeypatch):
    import threading

    import lvjiang.core.simulation.recording as recording

    entered, release = threading.Event(), threading.Event()
    real_encode = recording.cv2.imencode

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return real_encode(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(recording.cv2, "imencode", blocked)
        tape = FullTaskRecorder(
            tmp_path / "overflow", max_pending=1, provenance="synthetic"
        )
        image = np.zeros((16, 16, 3), np.uint8)
        assert tape.capture(image)
        assert entered.wait(2)
        assert tape.capture(image)
        assert tape.capture(image) is False
        assert tape.status["reason"] == "queue_overflow"
        release.set()
        tape.close()
    assert not json.loads(
        (tape.directory / "manifest.json").read_text(encoding="utf-8")
    )["complete"]
    disk = FullTaskRecorder(
        tmp_path / "disk", max_disk_bytes=16_384, provenance="synthetic"
    )
    image = np.random.default_rng(7).integers(0, 256, (128, 128, 3), dtype=np.uint8)
    disk.capture(image)
    disk.close()
    assert "disk_budget" in disk.status["reason"]
    assert not json.loads(
        (disk.directory / "manifest.json").read_text(encoding="utf-8")
    )["complete"]


def test_post_recording_annotations_bind_selected_frames_without_rewriting_tape(
    tmp_path,
):
    from lvjiang.core.simulation.recording import annotation_template

    wf = tmp_path / "task.wf"
    wf.write_text("return 0\n", encoding="utf-8")
    tape = FullTaskRecorder(tmp_path / "raw", workflow=wf, provenance="synthetic")
    for intensity in [0, 30, 60]:
        assert tape.capture(np.full((16, 16, 3), intensity, np.uint8))
        assert tape.flush(2)
    tape.close()
    raw = (tape.directory / "events.jsonl").read_bytes()
    sidecar = tmp_path / "annotations.json"
    annotation_template(tape.directory, sidecar)
    annotations = json.loads(sidecar.read_text(encoding="utf-8"))
    annotations.update(
        frames={"1": "start", "3": "done"},
        start="start",
        edges=[
            {
                "source": "start",
                "target": "done",
                "action": "click",
                "match": {"box": [0, 0, 10, 10], "button": "left"},
            }
        ],
    )
    sidecar.write_text(json.dumps(annotations), encoding="utf-8")
    compile_tape(
        tape.directory,
        tmp_path / "selected",
        workflow=wf,
        terminal=["done"],
        annotations=sidecar,
    )
    scenario = Scenario.load(tmp_path / "selected")
    assert scenario.spec["evidence"]["full_frames"] == 3
    assert scenario.spec["evidence"]["selected_state_count"] == 2
    assert scenario.spec["evidence"]["annotation_sha256"]
    assert (tape.directory / "events.jsonl").read_bytes() == raw
    annotations["events_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(annotations), encoding="utf-8")
    with pytest.raises(SimulationError, match="annotation_identity_mismatch"):
        compile_tape(
            tape.directory,
            tmp_path / "no",
            workflow=wf,
            terminal=["done"],
            annotations=sidecar,
        )
