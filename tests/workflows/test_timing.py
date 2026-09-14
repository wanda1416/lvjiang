"""Tests for the shared high-precision workflow wait primitive."""

from lvjiang.workflows.timing import precise_wait


class _FakeTimer:
    def __init__(self, *, clock_step_ns: int = 100_000):
        self.now_ns = 0
        self.clock_step_ns = clock_step_ns
        self.sleeps: list[float] = []

    def clock_ns(self) -> int:
        value = self.now_ns
        self.now_ns += self.clock_step_ns
        return value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_ns += int(seconds * 1_000_000_000)


def test_precise_wait_uses_coarse_fine_and_spin_stages():
    timer = _FakeTimer()

    assert precise_wait(
        0.020,
        _clock_ns=timer.clock_ns,
        _sleep=timer.sleep,
    )

    assert 0.005 in timer.sleeps
    assert 0.001 in timer.sleeps
    assert max(timer.sleeps) == 0.005
    assert timer.now_ns >= 20_000_000
    # The fake clock advances by 0.1 ms per observation, bounding the final
    # spin's deterministic overshoot to one clock step.
    assert timer.now_ns <= 20_100_000


def test_precise_wait_checks_pause_and_can_stop_early():
    timer = _FakeTimer()
    pause_calls = 0
    stop_calls = 0

    def pause_check() -> None:
        nonlocal pause_calls
        pause_calls += 1

    def stop_check() -> bool:
        nonlocal stop_calls
        stop_calls += 1
        return stop_calls == 3

    assert not precise_wait(
        1.0,
        pause_check=pause_check,
        stop_check=stop_check,
        _clock_ns=timer.clock_ns,
        _sleep=timer.sleep,
    )
    assert pause_calls == 3
    assert stop_calls == 3
    assert timer.now_ns < 1_000_000_000


def test_precise_wait_zero_duration_still_honours_stop():
    assert not precise_wait(0, stop_check=lambda: True)

