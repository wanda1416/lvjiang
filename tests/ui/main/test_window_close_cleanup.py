"""主窗口关闭链：单步失败不能阻断后续清理，且不能留下运行中的 QThread。

历史事故一：只读实例关闭时保存脚本参数抛 ``AccessDeniedError``（RuntimeError
子类），异常从 ``closeEvent`` 冒出去，ProfileEngine 停止、录屏转正、连接释放
全部没有执行，退出变成一个没有收尾的半关闭。

历史事故二：启动期网络线程（公告、版本检查、在线配置、统计上报）没有停止就
销毁窗口，Qt 打印「QThread: Destroyed while thread '' is still running」后
进程直接崩掉，没有任何收尾。
"""
from __future__ import annotations

import time
import types

from loguru import logger
from PyQt6.QtCore import QThread

from lvjiang.ui.main.window import MainWindow


class _Cooperative(QThread):
    """收到中断请求就退出：验证正常路径不需要 terminate。"""

    def run(self) -> None:
        while not self.isInterruptionRequested():
            time.sleep(0.01)


class _Stubborn(QThread):
    """不响应中断：验证超时后的兜底终止。"""

    def run(self) -> None:
        time.sleep(5.0)


def _host(*threads, device_worker=None):
    """只提供 _stop_running_qthreads 用到的宿主表面。"""
    return types.SimpleNamespace(
        findChildren=lambda _cls: list(threads),
        _device_worker=device_worker,
    )


def test_running_qthreads_are_stopped_before_destruction() -> None:
    """协作中断线程应在超时前正常结束，不触发强制终止。"""
    thread = _Cooperative()
    thread.start()
    records, sink = _capture()
    try:
        MainWindow._stop_running_qthreads(_host(thread), timeout_ms=3000)
    finally:
        logger.remove(sink)

    assert not thread.isRunning()
    assert not any(
        record["level"].name == "WARNING" for record in records
    ), "协作线程不该走到强制终止"


def test_stubborn_qthread_is_terminated_after_timeout() -> None:
    """超时仍不退出时兜底 terminate，避免带着运行中的线程销毁并崩溃。"""
    thread = _Stubborn()
    thread.start()
    records, sink = _capture()
    try:
        MainWindow._stop_running_qthreads(_host(thread), timeout_ms=50)
    finally:
        logger.remove(sink)

    assert not thread.isRunning()
    assert any(
        record["level"].name == "WARNING" and "强制终止" in record["message"]
        for record in records
    )


def test_device_worker_is_cancelled_before_waiting() -> None:
    """设备扫描循环靠取消标记退出，等待前必须先把标记置上。"""
    cancelled: list[bool] = []
    worker = types.SimpleNamespace(cancel=lambda: cancelled.append(True))

    MainWindow._stop_running_qthreads(_host(device_worker=worker), timeout_ms=50)

    assert cancelled == [True]


def test_finished_threads_are_left_alone() -> None:
    """已经结束的线程直接跳过，不做多余等待或终止。"""
    thread = _Cooperative()
    assert not thread.isRunning()

    MainWindow._stop_running_qthreads(_host(thread), timeout_ms=50)

    assert not thread.isRunning()


def _capture(level: str = "DEBUG"):
    """收集 loguru 记录；调用方负责 remove。"""
    records: list = []
    sink = logger.add(lambda message: records.append(message.record), level=level)
    return records, sink


def test_failed_close_step_is_swallowed_so_next_steps_still_run() -> None:
    calls: list[str] = []
    records, sink = _capture()

    def boom() -> None:
        calls.append("boom")
        raise RuntimeError("用户「测试」正在执行任务，请稍后重试")

    try:
        MainWindow._run_close_step("保存脚本参数", boom)
        calls.append("next")
    finally:
        logger.remove(sink)

    assert calls == ["boom", "next"]
    assert any(
        record["level"].name == "WARNING" and "保存脚本参数" in record["message"]
        for record in records
    )


def test_successful_close_step_is_traced() -> None:
    records, sink = _capture()
    try:
        MainWindow._run_close_step("保存界面状态", lambda: None)
    finally:
        logger.remove(sink)

    assert any(
        record["level"].name == "DEBUG" and "保存界面状态" in record["message"]
        for record in records
    )


def test_one_broken_cleanup_callback_does_not_block_the_rest() -> None:
    """插件回调逐个执行，一个抛异常不能取消后面插件的清理。"""
    ran: list[str] = []
    host = types.SimpleNamespace(_cleanup_callbacks=[
        lambda: (_ for _ in ()).throw(RuntimeError("坏回调")),
        lambda: ran.append("ok"),
    ])

    MainWindow._run_cleanup_callbacks(host)

    assert ran == ["ok"]
