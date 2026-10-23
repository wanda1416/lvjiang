"""主窗口关闭链：单步失败不能阻断后续清理。

历史事故：只读实例关闭时保存脚本参数抛 ``AccessDeniedError``（RuntimeError
子类），异常从 ``closeEvent`` 冒出去，ProfileEngine 停止、录屏转正、连接释放
全部没有执行，退出变成一个没有收尾的半关闭。
"""
from __future__ import annotations

import types

from loguru import logger

from lvjiang.ui.main.window import MainWindow


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
