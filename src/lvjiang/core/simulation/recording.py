"""Explicit full-task tape, bounded RAM/disk, never a diagnostic FIFO.

PNG preserves recognition pixels. Queue/quota/codec failure marks the recording
incomplete rather than silently rotating away the beginning of the task.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import queue
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..input.capture_base import CaptureBackend, capture_for_run, ensure_capture_channel
from .scenario import Scenario, SimulationError, confined, png_size


def _json(data: Any) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    with temporary.open("xb") as stream:
        stream.write(data)
    os.replace(temporary, path)


def _bounded(value: Any, depth: int = 0, budget: list[int] | None = None) -> None:
    budget = [256] if budget is None else budget
    budget[0] -= 1
    if budget[0] < 0 or depth > 6:
        raise ValueError("metadata nodes/depth")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("metadata nonfinite number")
    if isinstance(value, str) and len(value.encode("utf-8")) > 1024:
        raise ValueError("metadata string")
    if isinstance(value, dict):
        if len(value) > 32:
            raise ValueError("metadata keys")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata key type")
            _bounded(key, depth + 1, budget)
            _bounded(item, depth + 1, budget)
    elif isinstance(value, (list, tuple)):
        if len(value) > 32:
            raise ValueError("metadata array")
        for item in value:
            _bounded(item, depth + 1, budget)
    elif value is not None and not isinstance(value, (str, bool, int, float)):
        raise ValueError("metadata type")


class FullTaskRecorder:
    """One explicit recording scope; close/flush are for a background host, not Qt.

    Capture callbacks copy before returning. No settings/session/user data is
    discovered. State labels and edges must be supplied or edited by a person.
    """

    def __init__(
        self,
        directory: Path,
        *,
        workflow: Path | None = None,
        max_disk_bytes: int = 512_000_000,
        max_raw_bytes: int = 32_000_000,
        max_pending: int = 32,
        provenance: str = "recorded",
        task_run_id: str | None = None,
        batch_run_id: str | None = None,
        context: dict | None = None,
    ):
        if provenance not in {"recorded", "synthetic"}:
            raise ValueError("provenance must be recorded or synthetic")
        self.provenance = provenance
        self.task_status = "running"
        self.task_run_id = task_run_id
        self.batch_run_id = batch_run_id
        _bounded(batch_run_id)
        if batch_run_id is not None and len(batch_run_id.encode("utf-8")) > 256:
            raise ValueError("batch identity budget")
        self.capture_run_id = uuid.uuid4().hex
        self.context = context or {}
        if set(self.context) - {
            "layout_sha256",
            "configuration_sha256",
            "parameters_sha256",
            "engine_revision",
            "ocr_version",
            "target_kind",
            "target_size",
        }:
            raise ValueError(
                "context accepts only version/hash/target metadata, not config or credentials"
            )
        _bounded(self.context)
        _bounded(task_run_id)
        if len(_json(self.context)) > 4096 or (
            task_run_id is not None and len(task_run_id.encode("utf-8")) > 256
        ):
            raise ValueError("manifest context/identity budget")
        if max_disk_bytes < 16_384 or max_raw_bytes < 1 or max_pending < 1:
            raise ValueError("recording budgets must be positive (disk >= 16384)")
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory / "frames").mkdir()
        self.workflow_sha = (
            hashlib.sha256(workflow.read_bytes()).hexdigest() if workflow else None
        )
        self.max_disk_bytes, self.max_raw_bytes = max_disk_bytes, max_raw_bytes
        self._queue: queue.Queue = queue.Queue(maxsize=max_pending)
        self._lock = threading.RLock()
        self._idle, self._stop = threading.Event(), threading.Event()
        self._idle.set()
        self._raw = self._seq = self._frames = self._bytes = 0
        self._reason = ""
        self._closing = self._closed = False
        self.state_id: str | None = None
        self._started = time.monotonic_ns()
        _atomic(self.directory / "manifest.json", _json(self._manifest(False)))
        self._thread = threading.Thread(
            target=self._worker, name="full-task-recorder", daemon=True
        )
        self._thread.start()

    def _manifest(self, complete: bool) -> dict:
        events = self.directory / "events.jsonl"
        # Hash incrementally; never read a full-task log into RAM.
        sha = hashlib.sha256()
        if events.exists():
            with events.open("rb") as stream:
                for chunk in iter(lambda: stream.read(64_000), b""):
                    sha.update(chunk)
        return {
            "format_version": 1,
            "mode": "full_task",
            "task_status": self.task_status,
            "complete": complete,
            "provenance": self.provenance,
            "reason": self._reason or ("" if complete else "recording"),
            "frames": self._frames,
            "events": self._seq,
            "bytes_written": self._bytes,
            "workflow_sha256": self.workflow_sha,
            "events_sha256": sha.hexdigest(),
            "capture_run_id": self.capture_run_id,
            "task_run_id": self.task_run_id,
            "batch_run_id": self.batch_run_id,
            "context": self.context,
            "environment": {
                "python": platform.python_version(),
                "opencv": cv2.__version__,
                "numpy": np.__version__,
            },
            "max_disk_bytes": self.max_disk_bytes,
            "max_raw_bytes": self.max_raw_bytes,
        }

    def account_artifacts(self, count: int) -> None:
        """Charge bounded, already-written host artifacts before capture admission."""
        with self._lock:
            self._bytes += count
            if self._bytes + 16_384 > self.max_disk_bytes:
                self._reason = self._reason or "disk_budget"

    def _submit(self, event: dict, image: np.ndarray | None = None) -> bool:
        with self._lock:
            if self._reason or self._closing:
                return False
            if self._seq >= 1_000_000:
                self._reason = "event_count_budget"
                return False
            try:
                _bounded(event)
                if len(_json(event)) > 8192:
                    raise ValueError("event bytes")
            except (ValueError, TypeError):
                self._reason = "metadata_budget_or_type"
                return False
            raw = image.nbytes if image is not None else 0
            if raw + self._raw > self.max_raw_bytes:
                self._reason = "raw_budget"
                return False
            if self._queue.full():
                self._reason = "queue_overflow"
                return False
            if image is not None and (
                image.dtype != np.uint8
                or image.ndim != 3
                or image.shape[2] != 3
                or raw == 0
            ):
                self._reason = "unsupported_frame_format"
                return False
            self._seq += 1
            event = {
                "event_seq": self._seq,
                "time_ns": time.monotonic_ns() - self._started,
                **event,
            }
            try:
                owned = image.copy() if image is not None else None
            except Exception:
                self._reason = "copy_failed"
                return False
            self._raw += raw
            self._idle.clear()
            self._queue.put_nowait((event, owned, raw))
            return True

    def capture(
        self,
        image: np.ndarray,
        *,
        state_id: str | None = None,
        capture_seq: int = 0,
        engine_epoch: str = "e1",
        source: str = "",
    ) -> bool:
        return self._submit(
            {
                "kind": "capture",
                "state_id": state_id,
                "capture_seq": capture_seq,
                "engine_epoch": engine_epoch,
                "source": source,
            },
            image,
        )

    def wrap_capture(self, backend: Any, engine_epoch: str = "e1") -> RecordingCapture:
        return RecordingCapture(self, backend, engine_epoch)

    def wrap_input(self, backend: Any, capture: RecordingCapture) -> RecordingInput:
        return RecordingInput(self, backend, capture)

    def observer(self, engine_epoch: str, *, capture: RecordingCapture | None = None):
        """Full mode uses wrap_capture too, to include wait-stable/backend reads.

        Observer alone covers engine snapshots, not every backend getter. Keep
        one binding per engine so from-last after wait-stable still means its
        original frame, not the most recent unrelated backend sample.
        """
        last_binding: int | None = None

        def observe(meta: dict, image: np.ndarray) -> None:
            nonlocal last_binding
            if capture is not None:
                if not meta["reused"]:
                    last_binding = capture.latest_seq
                capture.last_consumed_seq = last_binding
                self.event(
                    "capture_use",
                    engine_epoch=engine_epoch,
                    frame_seq=meta["frame_seq"],
                    device_seq=last_binding,
                    source=meta["source"],
                    reused=meta["reused"],
                )
            elif meta["reused"]:
                self.event(
                    "reuse",
                    capture_seq=meta["frame_seq"],
                    engine_epoch=engine_epoch,
                    source=meta["source"],
                )
            else:
                self.capture(
                    image,
                    state_id=self.state_id,
                    capture_seq=meta["frame_seq"],
                    engine_epoch=engine_epoch,
                    source=meta["source"],
                )

        return observe

    def event(self, kind: str, **fields: Any) -> bool:
        if kind in {"capture", "edge"}:
            raise ValueError("use capture()/edge()")
        return self._submit({**fields, "kind": kind})

    def edge(self, source: str, target: str, action: str, **conditions: Any) -> bool:
        """Explicit annotation, NOT a branch inferred from recorded images."""
        return self._submit(
            {
                "kind": "edge",
                "edge": {
                    "source": source,
                    "target": target,
                    "action": action,
                    **conditions,
                },
            }
        )

    def _worker(self) -> None:
        try:
            with (self.directory / "events.jsonl").open("xb") as stream:
                while not self._stop.is_set() or not self._queue.empty():
                    try:
                        event, image, raw = self._queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    try:
                        data = b""
                        if image is not None:
                            ok, encoded = cv2.imencode(".png", image)
                            if not ok:
                                raise OSError("PNG encoder failed")
                            data = encoded.tobytes()
                            event.update(
                                frame=f"frames/{event['event_seq']:09d}.png",
                                sha256=hashlib.sha256(data).hexdigest(),
                            )
                        line = (
                            json.dumps(event, ensure_ascii=False, allow_nan=False)
                            + "\n"
                        ).encode("utf-8")
                        if (
                            self._bytes + len(data) + len(line)
                            > self.max_disk_bytes - 8192
                        ):
                            raise OSError("disk_budget")
                        if image is not None:
                            with (self.directory / event["frame"]).open("xb") as target:
                                target.write(data)
                            self._frames += 1
                        stream.write(line)
                        stream.flush()
                        self._bytes += len(data) + len(line)
                    except Exception as exc:
                        with self._lock:
                            self._reason = (
                                self._reason
                                or f"write_failed:{type(exc).__name__}:{str(exc)[:128]}"
                            )
                    finally:
                        with self._lock:
                            self._raw -= raw
                            self._queue.task_done()
                            if not self._queue.unfinished_tasks:
                                self._idle.set()
        except Exception as exc:
            with self._lock:
                self._reason = self._reason or f"writer_failed:{type(exc).__name__}"

    @property
    def status(self) -> dict:
        with self._lock:
            return {
                "recording": not self._closing and not self._reason,
                "reason": self._reason,
                "raw_bytes": self._raw,
                "frames": self._frames,
                "bytes_written": self._bytes,
            }

    def flush(self, timeout: float = 5) -> bool:
        return self._idle.wait(timeout)

    def close(self, timeout: float = 5) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
        deadline = time.monotonic() + timeout
        if not self.flush(timeout):
            self._reason = self._reason or "close_timeout"
        self._stop.set()
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            self._reason = self._reason or "writer_still_running"
        self._closed = True
        _atomic(
            self.directory / "manifest.json", _json(self._manifest(not self._reason))
        )


def compile_tape(
    recording: Path,
    destination: Path,
    *,
    workflow: Path,
    terminal: list[str],
    layout: dict | None = None,
    annotations: Path | None = None,
    fixture_context: Path | None = None,
) -> None:
    """Compile only explicitly labelled states/edges; reject unrecorded semantics.

    An animated state with multiple different images needs manual editing before
    compilation; selecting a representative silently would overstate coverage.
    """
    manifest = json.loads((recording / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1 or manifest.get("complete") is not True:
        raise SimulationError("incomplete_recording")
    if not manifest.get("workflow_sha256") or not workflow.is_file():
        raise SimulationError("workflow_identity_missing_or_changed")
    if (
        manifest.get("workflow_sha256")
        != hashlib.sha256(workflow.read_bytes()).hexdigest()
    ):
        raise SimulationError("workflow_identity_missing_or_changed")
    extra = {}
    if fixture_context is not None:
        if fixture_context.stat().st_size > 2_000_000:
            raise SimulationError("fixture_context_budget")
        extra = json.loads(fixture_context.read_text(encoding="utf-8"))
        if not isinstance(extra, dict) or set(extra) - {
            "layout",
            "params",
            "environment",
            "run_env",
        }:
            raise SimulationError("unsupported_fixture_context")
        if layout is not None and "layout" in extra:
            raise SimulationError("ambiguous_fixture_layout")
    recorded_layout = {}
    if (recording / "layout.json").exists():
        raw = confined(recording, "layout.json").read_bytes()
        if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != manifest.get(
            "context", {}
        ).get("layout_sha256"):
            raise SimulationError("recorded_layout_identity_mismatch")
        recorded_layout = json.loads(raw)
    overlay = None
    selected: dict[str, str] = {}
    annotation_sha = None
    if annotations is not None:
        if annotations.stat().st_size > 2_000_000:
            raise SimulationError("annotation_budget")
        raw_annotation = annotations.read_bytes()
        overlay = json.loads(raw_annotation)
        if (
            overlay.get("format_version") != 1
            or overlay.get("events_sha256") != manifest["events_sha256"]
            or overlay.get("workflow_sha256") != manifest["workflow_sha256"]
        ):
            raise SimulationError("annotation_identity_mismatch")
        if set(overlay) - {
            "format_version",
            "events_sha256",
            "workflow_sha256",
            "frames",
            "edges",
            "start",
        }:
            raise SimulationError("unsupported_annotation_fields")
        selected = overlay["frames"]
        if not isinstance(selected, dict) or not selected or len(selected) > 10_000:
            raise SimulationError("invalid_frame_selection")
        annotation_sha = hashlib.sha256(raw_annotation).hexdigest()
    states: dict[str, dict[str, Any]] = {}
    captures = 0
    found_selections: set[str] = set()
    edges: list[dict[str, Any]] = list(overlay["edges"]) if overlay is not None else []
    if len(edges) > 20_000:
        raise SimulationError("edge_count_budget")
    sha = hashlib.sha256()
    total = 0
    with (recording / "events.jsonl").open("rb") as stream:
        while line := stream.readline(8193):
            if len(line) > 8192:
                raise SimulationError("event_metadata_budget")
            sha.update(line)
            event = json.loads(line)
            total += 1
            if total > 1_000_000:
                raise SimulationError("event_count_budget")
            if event.get("event_seq") != total:
                raise SimulationError("event_sequence_incomplete")
            if event["kind"] == "capture":
                captures += 1
                png_size(confined(recording, event["frame"]), event["sha256"])
                if overlay is not None and str(event["event_seq"]) not in selected:
                    continue  # Explicitly omitted, never inferred as a covered state.
                state = (
                    selected[str(event["event_seq"])]
                    if overlay is not None
                    else event.get("state_id")
                )
                found_selections.add(str(event["event_seq"]))
                if not isinstance(state, str) or not re.fullmatch(
                    r"[A-Za-z0-9_.-]{1,80}", state
                ):
                    raise SimulationError("state_annotation_required")
                if state in states and states[state]["sha256"] != event["sha256"]:
                    raise SimulationError("state_visual_variants_require_edit", state)
                states.setdefault(
                    state, {"frame": event["frame"], "sha256": event["sha256"]}
                )
                if len(states) > 10_000:
                    raise SimulationError("state_count_budget")
            elif event["kind"] == "edge" and overlay is None:
                edges.append(event["edge"])
                if len(edges) > 20_000:
                    raise SimulationError("edge_count_budget")
    if sha.hexdigest() != manifest["events_sha256"]:
        raise SimulationError("events_hash_mismatch")
    if total != manifest.get("events") or captures != manifest.get("frames"):
        raise SimulationError("recording_counts_incomplete")
    if overlay is not None and found_selections != set(selected):
        raise SimulationError("annotation_refers_to_nonexistent_capture")
    if not states:
        raise SimulationError("no_states")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        (destination / "frames").mkdir()
        for n, state in enumerate(states.values()):
            name = f"frames/{n:06d}.png"
            shutil.copyfile(confined(recording, state["frame"]), destination / name)
            state["frame"] = name
        shutil.copyfile(workflow, destination / "task.wf")
        spec = {
            "format_version": 1,
            "provenance": manifest.get("provenance", "recorded"),
            "workflow": "task.wf",
            "workflow_sha256": manifest["workflow_sha256"],
            "start": overlay["start"] if overlay is not None else next(iter(states)),
            "states": states,
            "edges": edges,
            "terminal": terminal,
            "layout": layout if layout is not None else recorded_layout,
            "evidence": {
                "manifest_sha256": hashlib.sha256(
                    (recording / "manifest.json").read_bytes()
                ).hexdigest(),
                "events_sha256": manifest["events_sha256"],
                "full_frames": captures,
                "annotation_sha256": annotation_sha,
                "selected_state_count": len(states),
                "capture_run_id": manifest.get("capture_run_id"),
                "context": manifest.get("context", {}),
                "environment": manifest.get("environment", {}),
            },
        }
        spec.update(extra)
        if (recording / "workflow_bundle").is_dir():
            from .bundle import verify_bundle

            bundle = verify_bundle(recording / "workflow_bundle")
            if bundle["files"][bundle["entry"]] != manifest["workflow_sha256"]:
                raise SimulationError("bundle_entry_mismatch")
            shutil.copytree(
                recording / "workflow_bundle", destination / "workflow_bundle"
            )
            spec["workflow_bundle"] = "workflow_bundle"
        _atomic(destination / "scenario.json", _json(spec))
        Scenario.load(destination)
    except Exception:
        # Only this exclusive newly created destination belongs to this attempt.
        shutil.rmtree(destination)
        raise


class RecordingCapture(CaptureBackend):
    """Full backend getter capture, including wait-stable and lossless reads."""

    def __init__(self, recorder: FullTaskRecorder, backend: Any, engine_epoch: str):
        self.recorder, self.backend, self.engine_epoch = recorder, backend, engine_epoch
        self.latest_seq: int | None = None
        self.last_consumed_seq: int | None = None
        self._seq = 0

    @property
    def min_capture_interval(self) -> float:
        return float(self.backend.min_capture_interval)

    @min_capture_interval.setter
    def min_capture_interval(self, value: float) -> None:
        self.backend.min_capture_interval = value

    def _take(self, getter: Any, source: str) -> np.ndarray | None:
        self._seq += 1
        self.latest_seq = None
        try:
            image = getter()
        except Exception as exc:
            self.recorder.event(
                "capture_failed",
                engine_epoch=self.engine_epoch,
                device_seq=self._seq,
                error_type=type(exc).__name__,
            )
            raise
        if image is None:
            self.recorder.event(
                "capture_failed", engine_epoch=self.engine_epoch, device_seq=self._seq
            )
        else:
            self.latest_seq = self._seq
            self.recorder.capture(
                image,
                state_id=self.recorder.state_id,
                capture_seq=self._seq,
                engine_epoch=self.engine_epoch,
                source=source,
            )
        return image

    def capture(self, timeout: float = 5) -> np.ndarray | None:
        return self.capture_blocking(timeout=timeout)

    def capture_blocking(self, *, timeout: float = 5) -> np.ndarray | None:
        return self._take(
            lambda: capture_for_run(self.backend, timeout=timeout), "backend"
        )

    def capture_lossless(self, timeout: float = 10) -> np.ndarray | None:
        method = getattr(type(self.backend), "capture_lossless", None)
        if callable(method):
            return self._take(
                lambda: self.backend.capture_lossless(timeout=timeout), "lossless"
            )
        return self.capture_blocking(timeout=timeout)

    def ensure_channel_healthy(self) -> None:
        ensure_capture_channel(self.backend)

    def get_capture_size(self) -> tuple[int, int]:
        return self.backend.get_capture_size()

    def set_capture_region(self, left: int, top: int, width: int, height: int) -> None:
        self.backend.set_capture_region(left, top, width, height)

    def attach_to_window(self, title_keyword: str) -> bool:
        return self.backend.attach_to_window(title_keyword)

    def stop(self) -> None:
        self.backend.stop()


class RecordingInput:
    """Records requested input and dispatch result, not alleged physical effects.

    No monkeypatch, no global keyboard listener, no input text/password logging.
    Caller keeps ownership of the backend. Unlisted operations are forwarded
    without serializing their arguments, never treated as supported replay edges.
    """

    _ACTIONS = {
        "click_screen",
        "drag_screen",
        "move_screen",
        "move_relative",
        "scroll_screen",
        "key_down",
        "key_up",
        "mouse_button",
    }

    def __init__(
        self, recorder: FullTaskRecorder, backend: Any, capture: RecordingCapture
    ):
        self.recorder, self.backend, self.capture = recorder, backend, capture
        self._action_seq = 0

    @property
    def target_hwnd(self):
        return self.backend.target_hwnd

    @target_hwnd.setter
    def target_hwnd(self, value):
        self.backend.target_hwnd = value

    @property
    def stop_check(self):
        return self.backend.stop_check

    @stop_check.setter
    def stop_check(self, value):
        self.backend.stop_check = value

    def __getattr__(self, name: str):
        method = getattr(self.backend, name)
        if name not in self._ACTIONS:
            return method

        def invoke(*args, **kwargs):
            self._action_seq += 1
            action_id = self._action_seq
            # Named target labels are not physical coordinates. No arbitrary
            # repr, object state, typed text or clipboard content is captured.
            safe_args = [a if isinstance(a, (int, float, bool)) else None for a in args]
            if name in {"key_down", "key_up", "mouse_button"} and args:
                safe_args[0] = str(args[0])[
                    :32
                ]  # control keys/buttons, never paste API
            if name == "scroll_screen" and len(args) > 2:
                safe_args[2] = str(args[2])[:16]
            safe_kwargs = {
                k: v
                for k, v in kwargs.items()
                if k
                in {
                    "button",
                    "duration",
                    "hold",
                    "random_offset",
                    "interval",
                    "pre_delay",
                    "post_delay",
                }
            }
            self.recorder.event(
                "input_requested",
                action_id=action_id,
                action=name,
                engine_epoch=self.capture.engine_epoch,
                device_seq=self.capture.last_consumed_seq,
                requested_args=safe_args,
                requested_kwargs=safe_kwargs,
            )
            try:
                result = method(*args, **kwargs)
            except Exception as exc:
                self.recorder.event(
                    "input_failed",
                    action_id=action_id,
                    engine_epoch=self.capture.engine_epoch,
                    error_type=type(exc).__name__,
                )
                raise
            self.recorder.event(
                "input_dispatched",
                action_id=action_id,
                engine_epoch=self.capture.engine_epoch,
            )
            return result

        return invoke


def annotation_template(recording: Path, destination: Path) -> None:
    """Create a tiny editable sidecar; never silently invent edges/terminal."""
    manifest = json.loads((recording / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("complete") is not True:
        raise SimulationError("incomplete_recording")
    first = None
    with (recording / "events.jsonl").open("rb") as stream:
        while line := stream.readline(8193):
            if len(line) > 8192:
                raise SimulationError("event_metadata_budget")
            event = json.loads(line)
            if event["kind"] == "capture":
                first = str(event["event_seq"])
                break
    if first is None:
        raise SimulationError("no_frames")
    data = {
        "format_version": 1,
        "events_sha256": manifest["events_sha256"],
        "workflow_sha256": manifest["workflow_sha256"],
        "frames": {first: "state_1"},
        "start": "state_1",
        "edges": [],
    }
    with destination.open("xb") as stream:
        stream.write(_json(data))
