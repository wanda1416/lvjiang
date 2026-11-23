"""Workflow-scoped capture snapshots.

Every workflow engine owns its latest capture.  Domain builtins may inspect
that exact frame after an OCR/recognition statement without triggering a
second device capture.  The cache is deliberately per-engine: a module-level
cache would leak frames between concurrent tasks and users.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
from loguru import logger

from ..core.input.capture_base import capture_for_run, unwrap_capture
from .errors import WorkflowUserError

#: from screen 直取超时：页面校验不该被设备卡顿拖到 capture_lossless 的 10s 默认值。
_SCREEN_CAPTURE_TIMEOUT = 3.0
#: 直取帧与流方向/尺寸不一致时的重取等待。
_SCREEN_RETRY_DELAY = 0.15
#: 宽高比允许的轻微偏差（DPR/裁边）；方向对调直接判不一致。
_SCREEN_SIZE_TOLERANCE = 0.05


class CaptureSnapshotMixin:
    """Record and expose the latest frame captured by a workflow run."""

    capture_observer: Callable[[dict[str, Any], np.ndarray], None] | None = None
    _last_capture_frame: np.ndarray | None
    _last_capture_time_ns: int
    _last_capture_source: str
    _last_capture_seq: int
    _last_capture_fallback: bool
    _reused_capture_frame: np.ndarray | None
    _screen_capture_scope: bool
    _snapshot_generation: int

    def _init_capture_snapshot(self) -> None:
        self._last_capture_frame = None
        self._last_capture_time_ns = 0
        self._last_capture_source = ""
        self._last_capture_seq = 0
        self._last_capture_fallback = False
        self._reused_capture_frame = None
        self._screen_capture_scope = False
        self._snapshot_generation = 0

    def clear_capture_snapshot(self) -> None:
        """Invalidate the cached frame at a workflow lifecycle boundary."""
        self._last_capture_frame = None
        self._last_capture_time_ns = 0
        self._last_capture_source = ""
        self._last_capture_fallback = False
        self._reused_capture_frame = None
        self._snapshot_generation = getattr(self, "_snapshot_generation", 0) + 1

    def capture_frame(self, *, source: str = "") -> np.ndarray | None:
        """Capture a frame and make it the engine's latest snapshot.

        A failed capture invalidates the previous snapshot.  Falling back to
        an older successful frame would silently associate stale pixels with
        the current OCR result.  ``from screen`` 作用域内的首次取帧走直取通道，
        之后的裁剪复用这一帧，保证同一语句看到的是同一张画面。
        """
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.capture_frame(source=source)

        if getattr(self, "_reused_capture_frame", None) is not None:
            scope = "from screen" if getattr(self, "_screen_capture_scope", False) else "from last"
            logger.debug(
                f"capture: frame_seq={self.last_capture_seq} source={source} {scope}")
            assert self._reused_capture_frame is not None
            self._notify_capture_observer(self._reused_capture_frame, source, reused=True)
            return self._reused_capture_frame

        from_screen = bool(getattr(self, "_screen_capture_scope", False))
        fell_back = False
        try:
            if from_screen:
                result = self._capture_screen_frame()
                if result is None:
                    frame = None
                else:
                    frame, fell_back = result
            else:
                frame = capture_for_run(self._capture)
        except Exception:
            self.clear_capture_snapshot()
            raise
        if frame is None:
            self.clear_capture_snapshot()
            return None
        self._last_capture_frame = frame
        self._last_capture_time_ns = time.perf_counter_ns()
        self._last_capture_source = (
            self._screen_source_label(source, fell_back) if from_screen
            else str(source or ""))
        self._last_capture_fallback = bool(from_screen and fell_back)
        self._last_capture_seq = getattr(self, "_last_capture_seq", 0) + 1
        if from_screen:
            # 后续区域裁剪、panel 与模板匹配必须复用这张直取帧
            self._reused_capture_frame = frame
        logger.debug(
            f"capture: frame_seq={self.last_capture_seq} "
            f"source={self._last_capture_source}")
        self._notify_capture_observer(frame, self._last_capture_source, reused=False)
        return frame

    def _notify_capture_observer(self, frame: np.ndarray, source: str, *, reused: bool) -> None:
        """Optional host observer; borrowed pixels are valid only during this call.

        The observer must obtain its own stable copy for asynchronous work.
        Recording failures never replace recognition results or hardware errors.
        """
        observer = getattr(self, "capture_observer", None)
        if observer is None:
            return
        try:
            observer({"frame_seq": self.last_capture_seq, "source": str(source),
                      "captured_ns": self._last_capture_time_ns, "reused": reused}, frame)
        except Exception as exc:
            self.capture_observer = None
            logger.warning("截图观察器失败，已停用记录（不影响识别）：{}", type(exc).__name__)

    def _capture_screen_frame(self) -> tuple[np.ndarray, bool] | None:
        """``from screen``：跳过视频流直取当下画面。

        返回 ``(frame, fell_back)``；直取失败或方向/尺寸不可信时回退流最新帧，
        第二个元素标记这次回退，供日志与上层区分。直取通道由后端能力决定
        （Android scrcpy 走 screencap，PC 后端基类等价于普通截图）。
        """
        capture = unwrap_capture(self._capture)
        lossless = getattr(type(capture), "capture_lossless", None)
        reason = ""
        if callable(lossless):
            frame: np.ndarray | None = None
            try:
                frame = capture.capture_lossless(timeout=_SCREEN_CAPTURE_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - 直取失败按约定回退
                reason = f"直取异常：{exc}"
            if frame is not None and not self._screen_frame_matches_stream(frame):
                # 把旋转中/尺寸不符的帧喂给按比例裁剪和模板 scale 会误判，
                # 等一拍重取一次；仍不一致就按失败回退。
                time.sleep(_SCREEN_RETRY_DELAY)
                try:
                    retried = capture.capture_lossless(
                        timeout=_SCREEN_CAPTURE_TIMEOUT)
                except Exception as exc:  # noqa: BLE001
                    retried = None
                    reason = f"重取异常：{exc}"
                if retried is not None:
                    frame = retried
                if not self._screen_frame_matches_stream(frame):
                    reason = (
                        f"直取帧 {frame.shape[1]}x{frame.shape[0]} 与视频流 "
                        f"{self._stream_size_text()} 方向/尺寸不一致")
                    frame = None
            if frame is not None:
                logger.debug(
                    f"capture: from screen 直取帧 {frame.shape[1]}x{frame.shape[0]}")
                return frame, False
            reason = reason or "未取到画面"
        else:
            reason = "后端没有直取通道"
        logger.warning(f"capture: from screen {reason}，回退视频流最新帧")
        fallback = capture_for_run(capture)
        if fallback is None:
            return None
        return fallback, True

    def _screen_frame_matches_stream(self, frame: np.ndarray) -> bool:
        """直取帧与流尺寸/宽高比一致才认为方向可信。"""
        stream = self._stream_size()
        if stream is None:
            return True
        sw, sh = stream
        h, w = frame.shape[:2]
        if (w, h) == (sw, sh):
            return True
        if (w, h) == (sh, sw):
            return False
        if sh <= 0:
            return True
        frame_ratio = w / h if h else 0.0
        return abs(frame_ratio / (sw / sh) - 1.0) <= _SCREEN_SIZE_TOLERANCE

    def _stream_size(self) -> tuple[int, int] | None:
        try:
            stream = self._capture.get_capture_size()
            if not stream:
                return None
            sw, sh = int(stream[0]), int(stream[1])
        except Exception:  # noqa: BLE001 - 尺寸只用于诊断
            return None
        if sw <= 0 or sh <= 0:
            return None
        return sw, sh

    def _stream_size_text(self) -> str:
        stream = self._stream_size()
        return "未知" if stream is None else f"{stream[0]}x{stream[1]}"

    @staticmethod
    def _screen_source_label(source: str, fell_back: bool) -> str:
        suffix = "screen_fallback" if fell_back else "screen"
        return f"{source}+{suffix}" if source else suffix

    @contextmanager
    def capture_source(self, *, from_last: bool = False, screen: bool = False) -> Iterator[None]:
        """Scope an explicit DSL image source across all region/panel paths.

        Panel alignment and recognition can request multiple crops; every
        request in a reused observation must use the same original frame.
        ``screen`` 让作用域内首次取帧走直取通道，之后同样复用那张帧。
        The scope is restored even when recognition raises.
        """
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            with owner.capture_source(from_last=from_last, screen=screen):
                yield
            return
        previous = getattr(self, "_reused_capture_frame", None)
        previous_screen = getattr(self, "_screen_capture_scope", False)
        start_gen = getattr(self, "_snapshot_generation", 0)
        frame = None
        if from_last:
            frame = self.get_last_capture_frame()
            if frame is None:
                raise WorkflowUserError("from last: 没有可复用截图，请先执行截图或识别指令")
        self._reused_capture_frame = frame
        self._screen_capture_scope = bool(screen and not from_last)
        try:
            yield
        finally:
            if getattr(self, "_snapshot_generation", 0) == start_gen:
                self._reused_capture_frame = previous
            else:
                self._reused_capture_frame = None
            self._screen_capture_scope = previous_screen

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
    def last_capture_fallback(self) -> bool:
        """最近一帧是否因 from screen 直取失败而回退到视频流。"""
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.last_capture_fallback
        return getattr(self, "_last_capture_fallback", False)

    @property
    def last_capture_age_seconds(self) -> float | None:
        owner = getattr(self, "_engine", None)
        if owner is not None and owner is not self:
            return owner.last_capture_age_seconds
        if self._last_capture_frame is None:
            return None
        return (time.perf_counter_ns() - self._last_capture_time_ns) / 1_000_000_000
