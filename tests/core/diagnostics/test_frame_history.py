"""Bounded, stable diagnosis images, never substituted for recognition input."""

import json
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from lvjiang.core import task_diagnostics as diag


def emit(history, seq, image=None, source="ocr_scene"):
    if image is None:
        image = np.full((16, 16, 3), 255, np.uint8)
    history.observe({"frame_seq": seq, "source": source, "captured_ns": seq}, image)


def drain(history):
    with history._condition:
        assert history._condition.wait_for(lambda: history._inflight == 0, timeout=5)


def test_count_and_byte_limits_keep_exact_boundary_and_newest_frames(monkeypatch):
    # Fixed codec isolates accounting; another test exercises actual WebP.
    monkeypatch.setattr(diag, "_encode_webp", lambda image: b"x" * 100)
    probe = diag.RecentFrameHistory(diag.DiagnosticOptions(True), {})
    emit(probe, 1)
    probe.close("completed")
    one = probe.frames()[0].nbytes
    history = diag.RecentFrameHistory(diag.DiagnosticOptions(True, 2, 10_000), {})
    for seq in (1, 2):
        emit(history, seq)
        drain(history)
    assert history.snapshot()["frames"] == 2  # Not 99-of-100 semantics.
    emit(history, 3)
    history.close("failed")
    assert [json.loads(f.metadata)["frame_seq"] for f in history.frames()] == [2, 3]
    # A byte ceiling may evict earlier than the independent frame ceiling.
    history = diag.RecentFrameHistory(diag.DiagnosticOptions(True, 100, one * 2), {})
    for seq in (1, 2, 3):
        emit(history, seq)
        drain(history)
    history.close("completed")
    assert history.snapshot()["retained_bytes"] == one * 2
    assert [json.loads(f.metadata)["frame_seq"] for f in history.frames()] == [2, 3]


def test_stable_copy_real_webp_dedup_and_screenshot_filter(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_encode = diag._encode_webp

    def encode(pixels):
        entered.set()
        assert release.wait(5)
        return real_encode(pixels)

    monkeypatch.setattr(diag, "_encode_webp", encode)
    history = diag.RecentFrameHistory(
        diag.DiagnosticOptions(True), {"task_run_id": "synthetic"}
    )
    image = np.full((16, 16, 3), 255, np.uint8)
    emit(history, 1, image, "screenshot")
    assert not entered.is_set()
    emit(history, 1, image, "ocr_scene")
    assert entered.wait(5)
    image[:] = 0  # Device reuses its buffer immediately after the observer returns.
    emit(history, 1, image)
    release.set()
    history.close("completed")
    history.close("completed")  # Idempotent host cleanup.
    assert history.snapshot()["frames"] == 1
    assert history.snapshot()["state"] == "closed"
    decoded = cv2.imdecode(
        np.frombuffer(history.frames()[0].webp, np.uint8), cv2.IMREAD_COLOR
    )
    assert decoded.mean() > 250  # The stable white input, not the reused black buffer.


def test_queue_raw_and_encoded_overflow_are_bounded_and_counted(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def encode(image):
        entered.set()
        assert release.wait(5)
        return b"x" * 1000

    monkeypatch.setattr(diag, "_encode_webp", encode)
    monkeypatch.setattr(diag.RecentFrameHistory, "RAW_LIMIT", 100)
    history = diag.RecentFrameHistory(diag.DiagnosticOptions(True, 100, 500), {})
    image = np.zeros((2, 2, 3), np.uint8)
    emit(history, 1, image)
    assert entered.wait(5)
    for seq in range(2, 6):
        emit(history, seq, image)
    emit(history, 6, np.zeros((10, 10, 3), np.uint8))
    assert history.snapshot()["pending"] == 4
    assert history.snapshot()["raw_bytes"] == 48
    release.set()
    history.close("completed")
    summary = history.snapshot()
    assert summary["frames"] == 0 and summary["retained_bytes"] == 0
    assert summary["dropped"]["queue_full"] == 1
    assert summary["dropped"]["raw_oversize"] == 1
    assert summary["dropped"]["encoded_oversize"] == 4
    assert summary["raw_bytes"] == 0 and summary["pending"] == 0

    # Independent raw-byte backpressure below the four-item ceiling.
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(diag.RecentFrameHistory, "RAW_LIMIT", 20)
    history = diag.RecentFrameHistory(diag.DiagnosticOptions(True), {})
    emit(history, 1, image)
    assert entered.wait(5)
    emit(history, 2, image)
    assert history.snapshot()["pending"] == 1 and history.snapshot()["raw_bytes"] == 12
    assert history.snapshot()["dropped"]["queue_full"] == 1
    release.set()
    history.close("completed")


def test_codec_error_and_close_timeout_do_not_publish_late_frames(monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def encode(image):
        entered.set()
        assert release.wait(5)
        raise ValueError("synthetic codec failure")

    monkeypatch.setattr(diag, "_encode_webp", encode)
    history = diag.RecentFrameHistory(diag.DiagnosticOptions(True), {})
    emit(history, 1)
    assert entered.wait(5)
    emit(history, 2)
    history.close("cancelled", timeout=0)
    assert history.snapshot()["state"] == "incomplete"
    assert history.snapshot()["pending"] == 1  # Only the uncancellable native call.
    release.set()
    history._worker.join(5)
    assert not history._worker.is_alive()
    assert history.snapshot()["state"] == "incomplete"
    assert history.snapshot()["dropped"]["codec"] == 1
    assert history.snapshot()["frames"] == 0 and history.snapshot()["raw_bytes"] == 0


def test_scopes_isolate_task_batch_target_reuse_and_restore_observer():
    service = diag.TaskDiagnostics()
    assert service.freeze() is None and service.latest is None
    service.options = diag.DiagnosticOptions(True)
    request = service.freeze()
    callbacks = []

    def original(meta, image):
        callbacks.append(meta["frame_seq"])

    engine = SimpleNamespace(
        capture_observer=original, return_value=0, _stop_check=lambda: False
    )
    for task_id in ("first", "second"):
        engine.execution_scope = request.scope(
            task_run_id=task_id, batch_run_id="batch", target_scope="target"
        )
        with engine.execution_scope(engine, None):
            engine.capture_observer(
                {"frame_seq": 1, "source": "ocr_scene", "captured_ns": 1},
                np.zeros((2, 2, 3), np.uint8),
            )
        assert engine.execution_scope is None and engine.capture_observer is original
        summary = service.latest.snapshot()
        assert summary["task_run_id"] == task_id and summary["batch_run_id"] == "batch"
        assert summary["target_scope"] == "target" and summary["frames"] == 1
        assert json.loads(service.latest.frames()[0].metadata)["task_run_id"] == task_id
    assert callbacks == [1, 1]


def test_scope_failure_and_cancel_preserve_exception_and_business_state():
    service = diag.TaskDiagnostics()
    service.options = diag.DiagnosticOptions(True)
    engine = SimpleNamespace(
        capture_observer=None, return_value=-1, _stop_check=lambda: False
    )
    request = service.freeze()
    with request.scope(task_run_id="failed")(engine, None):
        pass
    assert service.latest.snapshot()["task_status"] == "failed"
    with pytest.raises(ValueError, match="original"):
        with request.scope(task_run_id="exception")(engine, None):
            raise ValueError("original")
    assert (
        service.latest.snapshot()["state"] == "closed"
        and engine.capture_observer is None
    )
    engine._stop_check = lambda: True
    with request.scope(task_run_id="cancelled")(engine, None):
        pass
    assert (
        service.latest.snapshot()["task_status"] == "cancelled"
        and engine.return_value == -1
    )


def test_concurrent_scopes_keep_own_limits_target_and_metadata():
    service = diag.TaskDiagnostics()
    service.options = diag.DiagnosticOptions(True, max_frames=2)
    first = service.freeze()
    service.options = diag.DiagnosticOptions(True, max_frames=1)
    second = service.freeze()
    engine_a = SimpleNamespace(
        capture_observer=None, return_value=0, _stop_check=lambda: False
    )
    engine_b = SimpleNamespace(
        capture_observer=None, return_value=0, _stop_check=lambda: False
    )
    with first.scope(task_run_id="task-a", target_scope="target-a")(engine_a, None):
        a = service.latest
        emit(a, 1)
        with second.scope(task_run_id="task-b", target_scope="target-b")(
            engine_b, None
        ):
            b = service.latest
            emit(b, 1)
            emit(a, 2)
            emit(b, 2)
    assert a.snapshot()["frames"] == 2 and b.snapshot()["frames"] == 1
    assert service.latest is b
    assert all(json.loads(f.metadata)["task_run_id"] == "task-a" for f in a.frames())
    assert all(json.loads(f.metadata)["target_scope"] == "target-b" for f in b.frames())
    assert a.snapshot()["capture_scope"] != b.snapshot()["capture_scope"]
    assert not a._worker.is_alive() and not b._worker.is_alive()


def test_existing_scope_and_observer_failure_semantics_are_preserved():
    from contextlib import contextmanager

    service = diag.TaskDiagnostics()
    service.options = diag.DiagnosticOptions(True)
    calls = []

    @contextmanager
    def original_scope(engine, source):
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")

    engine = SimpleNamespace(
        capture_observer=None, return_value=0, _stop_check=lambda: False
    )
    scope = service.freeze().scope(task_run_id="scoped", previous=original_scope)
    engine.execution_scope = scope
    with scope(engine, None):
        assert engine.execution_scope is original_scope
        # CaptureSnapshotMixin disables an observer after a callback failure;
        # closing diagnostics must not resurrect an observer disabled by the engine.
        engine.capture_observer = None
    assert engine.capture_observer is None and engine.execution_scope is original_scope
    assert calls == ["enter", "exit"]
