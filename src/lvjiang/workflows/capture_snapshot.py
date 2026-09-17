"""Workflow-scoped capture snapshots.

Every workflow engine owns its latest capture.  Domain builtins may inspect
that exact frame after an OCR/recognition statement without triggering a
second device capture.  The cache is deliberately per-engine: a module-level
cache would leak frames between concurrent tasks and users.
"""

from __future__ import annotations

import time

import numpy as np


class CaptureSnapshotMixin:
    """Record and expose the latest frame captured by a workflow run."""

    _last_capture_frame: np.ndarray | None
    _last_capture_time_ns: int
    _last_capture_source: str
    _last_capture_seq: int

    def _init_capture_snapshot(self) -> None:
        self._last_capture_frame = None
        self._last_capture_time_ns = 0
        self._last_capture_source = ""
        self._last_capture_seq = 0

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

        frame = self._capture.capture()
        if frame is None:
            self.clear_capture_snapshot()
            return None
        self._last_capture_frame = frame
        self._last_capture_time_ns = time.perf_counter_ns()
        self._last_capture_source = str(source or "")
        self._last_capture_seq = getattr(self, "_last_capture_seq", 0) + 1
        return frame

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
