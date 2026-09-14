"""High-precision, interruptible timing primitives for workflow actions."""

from __future__ import annotations

import time
from collections.abc import Callable

_NS_PER_SECOND = 1_000_000_000
_COARSE_THRESHOLD_NS = 10_000_000
_FINE_THRESHOLD_NS = 2_000_000
_COARSE_SLICE_SECONDS = 0.005
_FINE_SLICE_SECONDS = 0.001


def precise_wait(
    seconds: float,
    *,
    stop_check: Callable[[], bool] | None = None,
    pause_check: Callable[[], None] | None = None,
    _clock_ns: Callable[[], int] = time.perf_counter_ns,
    _sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait at least *seconds* with a short high-precision tail.

    Long sleeps are split into 5 ms slices so stop and pause requests remain
    responsive.  The final 10 ms uses 1 ms slices, and the final 2 ms is a
    short spin against ``perf_counter_ns``.  This avoids handing the most
    timing-sensitive part of every workflow delay back to the OS scheduler.

    Returns ``False`` when ``stop_check`` requests cancellation, otherwise
    ``True`` after the deadline is reached.  A blocking ``pause_check`` does
    not move the deadline, matching the existing keyboard-hold behaviour.
    """
    duration_ns = max(0, int(float(seconds) * _NS_PER_SECOND))
    deadline_ns = _clock_ns() + duration_ns

    while True:
        if pause_check is not None:
            pause_check()
        if stop_check is not None and stop_check():
            return False

        remaining_ns = deadline_ns - _clock_ns()
        if remaining_ns <= 0:
            return True

        if remaining_ns > _COARSE_THRESHOLD_NS:
            _sleep(_COARSE_SLICE_SECONDS)
        elif remaining_ns > _FINE_THRESHOLD_NS:
            _sleep(_FINE_SLICE_SECONDS)
        # The final 2 ms intentionally spins.  It is short enough not to
        # monopolize a core for a meaningful period, while avoiding the
        # several-millisecond wake-up overshoot visible in real workflows.

