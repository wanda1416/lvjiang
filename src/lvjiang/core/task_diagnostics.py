"""Opt-in recent recognition frames. No Qt, device wrappers, disk I/O or simulation imports.

Limits apply per task. Only the latest started task is retained by the host;
concurrent active tasks own independent rings until their execution scopes exit.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class DiagnosticOptions:
    enabled: bool = False
    max_frames: int = 100
    max_bytes: int = 50_000_000

    def __post_init__(self) -> None:
        if self.max_frames < 1 or self.max_bytes < 1:
            raise ValueError("Diagnostic limits must be positive")


@dataclass(frozen=True)
class DiagnosticFrame:
    """Immutable compressed pixels and bounded UTF-8 metadata (not total RSS)."""

    webp: bytes
    metadata: bytes

    @property
    def nbytes(self) -> int:
        return len(self.webp) + len(self.metadata)


def _encode_webp(image: Any) -> bytes:
    # Import/encode only inside an explicitly enabled task's single worker.
    import cv2

    ok, encoded = cv2.imencode(".webp", image, [cv2.IMWRITE_WEBP_QUALITY, 80])
    if not ok:
        raise ValueError("WebP encoding failed")
    return encoded.tobytes()


class RecentFrameHistory:
    """Single encoder, non-waiting admission, frame AND compressed-byte FIFO.

    At most four raw frames, including the one being encoded, and 32 MB of raw
    copies are admitted. Oversized inputs/encoded frames and queue overflow are
    counted, not allowed to block/abort the task. Codec work is outside the lock.
    """

    RAW_LIMIT = 32_000_000
    PENDING_LIMIT = 4

    def __init__(
        self,
        options: DiagnosticOptions,
        identity: dict[str, str],
        *,
        on_frame: Callable[[DiagnosticFrame], Any] | None = None,
    ):
        if not options.enabled:
            raise ValueError("Frame history requires explicit opt-in")
        self.options = options
        self.identity = {key: str(value)[:256] for key, value in identity.items()}
        self.identity["capture_scope"] = uuid.uuid4().hex
        self._on_frame = on_frame
        self._condition = threading.Condition()
        self._frames: deque[DiagnosticFrame] = deque()
        self._pending: deque[tuple[bytes, Any]] = deque()
        self._bytes = self._raw_bytes = self._inflight = 0
        self._last_seq = 0
        self._closing = self._discard_results = False
        self._state = "recording"
        self._task_status = "running"
        self._drops = dict.fromkeys(
            (
                "queue_full",
                "raw_oversize",
                "encoded_oversize",
                "codec",
                "close_timeout",
            ),
            0,
        )
        self._evicted = 0
        self._worker = threading.Thread(
            target=self._encode_loop, name="task-diagnostic-encoder", daemon=True
        )
        self._worker.start()

    def observe(self, meta: dict[str, Any], image: Any) -> None:
        # UI previews/backend refreshes never enter this observer. Explicit
        # screenshot export is not recognition; a later from-last recognition
        # can still admit that seq because filtering precedes deduplication.
        source = str(meta.get("source", ""))[:256]
        if source.split("+", 1)[0] == "screenshot":
            return
        seq = int(meta["frame_seq"])
        with self._condition:
            if self._closing or seq <= self._last_seq:
                return
            self._last_seq = seq
            if image.dtype.name != "uint8" or image.ndim != 3 or image.shape[2] != 3:
                self._drops["codec"] += 1
                return
            size = int(image.nbytes)
            if size > self.RAW_LIMIT:
                self._drops["raw_oversize"] += 1
                return
            if (
                self._inflight >= self.PENDING_LIMIT
                or self._raw_bytes + size > self.RAW_LIMIT
            ):
                self._drops["queue_full"] += 1
                return
            metadata = json.dumps(
                {
                    **self.identity,
                    "frame_seq": seq,
                    "source": source,
                    "captured_ns": int(meta["captured_ns"]),
                    "shape": list(image.shape),
                },
                ensure_ascii=False,
            ).encode("utf-8")
            # Borrowed backend pixels are stable only during the callback.
            pixels = image.copy(order="C")
            self._pending.append((metadata, pixels))
            self._inflight += 1
            self._raw_bytes += size
            self._condition.notify()

    def _encode_loop(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._pending) or self._closing)
                if not self._pending:
                    self._state = "incomplete" if self._discard_results else "closed"
                    self._condition.notify_all()
                    return
                metadata, image = self._pending.popleft()
            frame = None
            published = None
            try:
                frame = DiagnosticFrame(_encode_webp(image), metadata)
            except Exception:
                with self._condition:
                    self._drops["codec"] += 1
            finally:
                size = int(image.nbytes)
                del image  # Release pixels before allowing another raw admission.
                with self._condition:
                    self._inflight -= 1
                    self._raw_bytes -= size
                    if frame is not None and not self._discard_results:
                        if frame.nbytes > self.options.max_bytes:
                            self._drops["encoded_oversize"] += 1
                        else:
                            self._frames.append(frame)
                            self._bytes += frame.nbytes
                            while (
                                len(self._frames) > self.options.max_frames
                                or self._bytes > self.options.max_bytes
                            ):
                                self._bytes -= self._frames.popleft().nbytes
                                self._evicted += 1
                            published = frame
                        self._condition.notify_all()
                    else:
                        self._condition.notify_all()
                if published is not None and self._on_frame is not None:
                    try:
                        self._on_frame(published)
                    except Exception:
                        pass
                # Do not keep a raw reference alive while waiting for the next frame.
                del frame, published

    def close(self, task_status: str, *, timeout: float = 2.0) -> None:
        with self._condition:
            if self._closing:
                return
            self._closing = True
            self._task_status = task_status
            self._state = "draining"
            self._condition.notify_all()
        self._worker.join(timeout=timeout)
        with self._condition:
            if self._worker.is_alive():
                self._drops["close_timeout"] += 1
                self._discard_results = True
                for _, image in self._pending:
                    self._raw_bytes -= int(image.nbytes)
                    self._inflight -= 1
                self._pending.clear()
                self._state = "incomplete"

    def frames(self) -> tuple[DiagnosticFrame, ...]:
        """Explicit internal access; callers retaining this tuple own its memory."""
        with self._condition:
            return tuple(self._frames)

    def snapshot(self) -> dict[str, Any]:
        with self._condition:
            return {
                **self.identity,
                "state": self._state,
                "task_status": self._task_status,
                "frames": len(self._frames),
                "retained_bytes": self._bytes,
                "pending": self._inflight,
                "raw_bytes": self._raw_bytes,
                "evicted": self._evicted,
                "dropped": dict(self._drops),
                "max_frames": self.options.max_frames,
                "max_bytes": self.options.max_bytes,
            }


@dataclass(frozen=True)
class DiagnosticRequest:
    """Frozen at a desktop task/batch launch, not at a later worker execution."""

    service: TaskDiagnostics
    options: DiagnosticOptions

    def scope(
        self,
        *,
        task_run_id: str,
        batch_run_id: str = "",
        target_scope: str = "",
        previous: Any = None,
    ):
        identity = {
            "task_run_id": task_run_id,
            "batch_run_id": batch_run_id,
            "target_scope": target_scope,
        }

        @contextmanager
        def capture_scope(engine: Any, source: Any):
            if getattr(engine, "execution_scope", None) is capture_scope:
                engine.execution_scope = (
                    previous  # Never carry this diagnostic identity into reuse.
                )
            original = engine.capture_observer
            history = None
            installed = None
            status = "failed"
            try:
                try:
                    history = RecentFrameHistory(
                        self.options, identity, on_frame=self.service._emit_frame
                    )
                    self.service._remember(history)

                    def observe(meta, pixels):
                        # Isolate diagnostics failures without swallowing a preexisting observer's behavior.
                        try:
                            history.observe(meta, pixels)
                        except Exception as exc:
                            logger.warning(
                                "开发诊断记录失败，不影响任务: {}", type(exc).__name__
                            )
                        if original is not None:
                            original(meta, pixels)

                    installed = observe
                    engine.capture_observer = observe
                except Exception as exc:
                    logger.warning(
                        "开发诊断启动失败，不影响任务: {}", type(exc).__name__
                    )
                with (
                    previous(engine, source) if previous is not None else nullcontext()
                ):
                    yield
                from ..workflows.result_protocol import return_failure_message

                status = (
                    "failed"
                    if return_failure_message(engine.return_value)
                    else "completed"
                )
            finally:
                if installed is not None and engine.capture_observer is installed:
                    engine.capture_observer = original
                try:
                    if engine._stop_check():
                        status = "cancelled"
                except Exception:
                    pass
                if history is not None:
                    try:
                        history.close(status)
                        summary = history.snapshot()
                        logger.info(
                            "开发诊断封口: task_run_id={}, capture_scope={}, frames={}, bytes={}, dropped={}, task_status={}",
                            summary["task_run_id"],
                            summary["capture_scope"],
                            summary["frames"],
                            summary["retained_bytes"],
                            summary["dropped"],
                            status,
                        )
                    except Exception as exc:
                        logger.warning(
                            "开发诊断收尾失败，不影响任务: {}", type(exc).__name__
                        )

        return capture_scope


class TaskDiagnostics:
    """Process-local UI settings; retains only the latest started task's ring.

    Active scopes own their rings independently. No task list/history accumulation
    and no disk writes. Opening/closing the dialog never changes a running scope.
    """

    def __init__(self) -> None:
        self.options = DiagnosticOptions()
        self._last: RecentFrameHistory | None = None
        self._frame_listener: Callable[[DiagnosticFrame], Any] | None = None
        self._lock = threading.Lock()

    def set_frame_listener(
        self, listener: Callable[[DiagnosticFrame], Any] | None,
    ) -> None:
        """注册/清除已编码帧的实时监听回调（用于可选的外部落盘，默认 None）。"""
        with self._lock:
            self._frame_listener = listener

    def _emit_frame(self, frame: DiagnosticFrame) -> None:
        with self._lock:
            listener = self._frame_listener
        if listener is not None:
            listener(frame)

    def freeze(self) -> DiagnosticRequest | None:
        return DiagnosticRequest(self, self.options) if self.options.enabled else None

    def _remember(self, history: RecentFrameHistory) -> None:
        with self._lock:
            self._last = history

    @property
    def latest(self) -> RecentFrameHistory | None:
        with self._lock:
            return self._last
