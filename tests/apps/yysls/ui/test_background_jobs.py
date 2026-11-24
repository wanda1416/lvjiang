"""JobController：代次丢弃过期结果、取消不覆盖旧结果、失败单通道、进度轮询。"""
from __future__ import annotations

import threading

from lvjiang.apps.yysls.ui.loadout.background import JobContext, JobController


def _wait(qtbot, predicate, timeout=5000):
    qtbot.waitUntil(predicate, timeout=timeout)


def test_finished_only_for_latest_generation(qtbot):
    jobs = JobController()
    finished: list[object] = []
    jobs.finished.connect(finished.append)
    gate = threading.Event()

    def slow(_ctx: JobContext):
        gate.wait(5)
        return "old"

    jobs.start(slow)
    jobs.start(lambda _ctx: "new")
    _wait(qtbot, lambda: "new" in finished)
    gate.set()
    qtbot.wait(200)
    assert finished == ["new"]
    assert not jobs.running


def test_cancel_drops_result_and_emits_cancelled(qtbot):
    jobs = JobController()
    finished: list[object] = []
    cancelled: list[int] = []
    jobs.finished.connect(finished.append)
    jobs.cancelled.connect(lambda: cancelled.append(1))
    started = threading.Event()
    gate = threading.Event()

    def slow(ctx: JobContext):
        started.set()
        gate.wait(5)
        return "partial" if ctx.is_cancelled() else "done"

    jobs.start(slow)
    _wait(qtbot, started.is_set)
    jobs.cancel()
    assert cancelled == [1] and not jobs.running
    gate.set()
    qtbot.wait(200)
    assert finished == []


def test_failure_goes_through_failed_signal(qtbot):
    jobs = JobController()
    failed: list[str] = []
    jobs.failed.connect(failed.append)

    def boom(_ctx: JobContext):
        raise ValueError("bad input")

    jobs.start(boom)
    _wait(qtbot, lambda: bool(failed))
    assert failed == ["bad input"]


def test_progress_is_polled_on_the_main_thread(qtbot):
    jobs = JobController(poll_interval_ms=20)
    ticks: list[tuple[int, int, str]] = []
    jobs.progress.connect(lambda e, t, m: ticks.append((e, t, m)))
    finished: list[object] = []
    jobs.finished.connect(finished.append)

    def work(ctx: JobContext):
        ctx.progress.total = 10
        for i in range(10):
            ctx.progress.evaluated = i + 1
            ctx.progress.message = f"step {i + 1}"
        return "ok"

    jobs.start(work)
    _wait(qtbot, lambda: bool(finished))
    assert ticks and ticks[-1] == (10, 10, "step 10")
