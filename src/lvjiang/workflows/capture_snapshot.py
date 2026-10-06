"""Workflow-scoped capture snapshots.

Every workflow engine owns its latest capture.  Domain builtins may inspect
that exact frame after an OCR/recognition statement without triggering a
second device capture.  The cache is deliberately per-engine: a module-level
cache would leak frames between concurrent tasks and users.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
from loguru import logger

from .errors import WorkflowUserError


class CaptureSnapshotMixin:
    """Record and expose the latest frame captured by a workflow run."""

    _last_capture_frame: np.ndarray | None
    _last_capture_time_ns: int
    _last_capture_source: str
    _last_capture_seq: int
    _reused_capture_frame: np.ndarray | None

    def _init_capture_snapshot(self) -> None:
        self._last_capture_frame = None
        self._last_capture_time_ns = 0
        self._last_capture_source = ""
        self._last_capture_seq = 0
        self._reused_capture_frame = None

    def clear_capture_snapshot(self) -> None:
        """Invalidate the cached frame at a workflow lifecycle boundary."""
        self._last_capture_frame = None
        self._last_capture_time_ns = 0
        self._last_capture_source = ""

    def capture_frame(self, *, source: str = "") -> np.ndarray | None:
        """Capture a frame and make it the engine's latest snapshot.

        A failed capture invalidates the previous snapshot.  Falling back to
        an older successful frame would silently associate stale pixels with
        the current OCR result.
        """
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.capture_frame(source=source)

        if getattr(self, "_reused_capture_frame", None) is not None:
            logger.debug(
                f"capture: frame_seq={self.last_capture_seq} source={source} from last")
            return self._reused_capture_frame

        try:
            frame = self._capture.capture()
        except Exception:
            self.clear_capture_snapshot()
            raise
        if frame is None:
            self.clear_capture_snapshot()
            return None
        self._last_capture_frame = frame
        self._last_capture_time_ns = time.perf_counter_ns()
        self._last_capture_source = str(source or "")
        self._last_capture_seq = getattr(self, "_last_capture_seq", 0) + 1
        logger.debug(f"capture: frame_seq={self.last_capture_seq} source={source}")
        return frame

    @contextmanager
    def capture_source(self, *, from_last: bool = False) -> Iterator[None]:
        """Scope an explicit DSL image source across all region/panel paths.

        Panel alignment and recognition can request multiple crops; every
        request in a reused observation must use the same original frame.
        The scope is restored even when recognition raises.
        """
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            with owner.capture_source(from_last=from_last):
                yield
            return
        previous = getattr(self, "_reused_capture_frame", None)
        frame = None
        if from_last:
            frame = self.get_last_capture_frame()
            if frame is None:
                raise WorkflowUserError("from last: 没有可复用截图，请先执行截图或识别指令")
        self._reused_capture_frame = frame
        try:
            yield
        finally:
            self._reused_capture_frame = previous

    def get_last_capture_frame(self) -> np.ndarray | None:
        """Return the latest frame without capturing or copying it."""
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.get_last_capture_frame()
        return getattr(self, "_last_capture_frame", None)

    @property
    def last_capture_seq(self) -> int:
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.last_capture_seq
        return getattr(self, "_last_capture_seq", 0)

    @property
    def last_capture_source(self) -> str:
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.last_capture_source
        return self._last_capture_source

    @property
    def last_capture_age_seconds(self) -> float | None:
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.last_capture_age_seconds
        if self._last_capture_frame is None:
            return None
        return (time.perf_counter_ns() - self._last_capture_time_ns) / 1_000_000_000
