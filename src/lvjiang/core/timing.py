"""Shared high-precision timing primitives.

Input backends and workflow execution both use this module so user-visible
durations do not drift merely because an action crosses abstraction layers.
"""

from __future__ import annotations

import time
from collections.abc import Callable

NS_PER_SECOND = 1_000_000_000
_COARSE_THRESHOLD_NS = 10_000_000
_DEFAULT_SPIN_TAIL_NS = 2_000_000
_COARSE_SLICE_SECONDS = 0.005
_FINE_SLICE_SECONDS = 0.001


def precise_wait_until(
    deadline_ns: int,
    *,
    stop_check: Callable[[], bool] | None = None,
    pause_check: Callable[[], None] | None = None,
    spin_tail_ns: int = _DEFAULT_SPIN_TAIL_NS,
    _clock_ns: Callable[[], int] = time.perf_counter_ns,
    _sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait until an absolute monotonic deadline.

    Returns ``False`` if ``stop_check`` requests cancellation.  ``spin_tail_ns``
    may be set to zero for dense timelines such as smooth mouse movement; this
    avoids paying a busy-spin tail at every intermediate sample.
    """
    spin_tail_ns = max(0, int(spin_tail_ns))
    while True:
        if pause_check is not None:
            pause_check()
        if stop_check is not None and stop_check():
            return False

        remaining_ns = int(deadline_ns) - _clock_ns()
        if remaining_ns <= 0:
            return True

        if remaining_ns > _COARSE_THRESHOLD_NS:
            _sleep(_COARSE_SLICE_SECONDS)
        elif remaining_ns > spin_tail_ns:
            # Leave the optional precision tail unslept.  With no spin tail,
            # sleep no longer than the actual remaining duration.
            available_ns = remaining_ns - spin_tail_ns
            _sleep(min(_FINE_SLICE_SECONDS, available_ns / NS_PER_SECOND))
        # Otherwise intentionally spin for the final precision tail.


def precise_wait(
    seconds: float,
    *,
    stop_check: Callable[[], bool] | None = None,
    pause_check: Callable[[], None] | None = None,
    spin_tail_ns: int = _DEFAULT_SPIN_TAIL_NS,
    _clock_ns: Callable[[], int] = time.perf_counter_ns,
    _sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wait at least ``seconds`` using a monotonic absolute deadline."""
    duration_ns = max(0, int(float(seconds) * NS_PER_SECOND))
    deadline_ns = _clock_ns() + duration_ns
    return precise_wait_until(
        deadline_ns,
        stop_check=stop_check,
        pause_check=pause_check,
        spin_tail_ns=spin_tail_ns,
        _clock_ns=_clock_ns,
        _sleep=_sleep,
    )
