"""后台任务：代次、取消、进度，一份实现供备战方案面板与分析对话框共用。

语义统一为：
- 每次 ``start`` 递增代次，旧任务的回调一律丢弃（对话框关闭、用户/方案切换、
  重新计算都不会让过期结果冒出来）；
- ``cancel`` 之后不再发 ``finished``——**不覆盖上一次完成的结果**，只发
  ``cancelled`` 让界面复位；
- 失败统一走 ``failed(message)``；
- 进度是线程共享的三个计数（evaluated / total / message），由主线程定时轮询后
  以 ``progress`` 信号发出，后台线程不碰任何 Qt 控件。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal


@dataclass
class JobProgress:
    """线程共享的进度计数；GIL 保证单个赋值原子。"""

    evaluated: int = 0
    total: int = 0
    message: str = ""


@dataclass(frozen=True)
class JobContext:
    """交给后台函数的上下文：是否已取消、进度计数。"""

    is_cancelled: Callable[[], bool]
    progress: JobProgress = field(default_factory=JobProgress)


JobFn = Callable[[JobContext], Any]


class _JobSignals(QObject):
    finished = pyqtSignal(int, object, object)  # generation, result, error


class BackgroundJob(QRunnable):
    def __init__(self, generation: int, fn: JobFn, context: JobContext) -> None:
        super().__init__()
        self.signals = _JobSignals()
        self._generation = generation
        self._fn = fn
        self._context = context

    def run(self) -> None:  # type: ignore[override]
        try:
            result = self._fn(self._context)
        except Exception as exc:  # noqa: BLE001 - 经信号回主线程处理
            logger.exception("后台任务失败")
            self.signals.finished.emit(self._generation, None, exc)
            return
        self.signals.finished.emit(self._generation, result, None)


class JobController(QObject):
    """一个界面区域的后台任务控制器：同一时间只认最新一次 start。"""

    finished = pyqtSignal(object)          # result
    failed = pyqtSignal(str)               # error message
    cancelled = pyqtSignal()
    progress = pyqtSignal(int, int, str)   # evaluated, total, message

    def __init__(self, parent: QObject | None = None, *,
                 poll_interval_ms: int = 0) -> None:
        super().__init__(parent)
        self._generation = 0
        self._cancelled = False
        self._running = False
        self._progress: JobProgress | None = None
        self._timer: QTimer | None = None
        if poll_interval_ms > 0:
            self._timer = QTimer(self)
            self._timer.setInterval(poll_interval_ms)
            self._timer.timeout.connect(self._poll)

    # ── 状态 ──

    @property
    def running(self) -> bool:
        return self._running

    @property
    def generation(self) -> int:
        return self._generation

    # ── 控制 ──

    def start(self, fn: JobFn) -> int:
        """启动新任务并返回其代次；之前的任务即使跑完也不会再回调。"""
        self._generation += 1
        generation = self._generation
        self._cancelled = False
        self._running = True
        progress = JobProgress()
        self._progress = progress
        context = JobContext(
            is_cancelled=lambda: self._cancelled or generation != self._generation,
            progress=progress,
        )
        job = BackgroundJob(generation, fn, context)
        # 信号从后台线程发出，QueuedConnection 保证槽在主线程执行
        job.signals.finished.connect(  # type: ignore[call-arg]
            self._on_job_finished, Qt.ConnectionType.QueuedConnection)
        pool = QThreadPool.globalInstance()
        assert pool is not None
        pool.start(job)
        if self._timer is not None:
            self._timer.start()
        return generation

    def cancel(self) -> None:
        if not self._running:
            return
        self._cancelled = True
        self._running = False
        if self._timer is not None:
            self._timer.stop()
        self.cancelled.emit()

    def invalidate(self) -> None:
        """静默作废在途任务（用户/方案切换）：不发 cancelled。"""
        self._generation += 1
        self._running = False
        if self._timer is not None:
            self._timer.stop()

    # ── 回调 ──

    def _poll(self) -> None:
        if self._progress is None or not self._running:
            return
        p = self._progress
        self.progress.emit(p.evaluated, p.total, p.message)

    def _on_job_finished(self, generation: int, result, error) -> None:
        if generation != self._generation or self._cancelled:
            return
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._poll_final()
        if error is not None:
            self.failed.emit(str(error))
            return
        self.finished.emit(result)

    def _poll_final(self) -> None:
        if self._progress is not None:
            p = self._progress
            self.progress.emit(p.evaluated, p.total, p.message)
