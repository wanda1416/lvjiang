"""批量条目被跳过时必须说清原因，不能只留一个「跳过」。

用户锁是进程级的、不知道目标；并发以后「这个用户正忙」可能是另一台设备上
的任务占着。报告里只写「跳过」，事后无法分辨是自己没勾、是被别的目标占用，
还是条目准备失败。
"""
from __future__ import annotations

from lvjiang.core.access import AccessDeniedError
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import BatchContext, BatchWorker
from lvjiang.ui.main.execution_runs import ExecutionRunManager
from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin


def _report() -> BatchReport:
    report = BatchReport(
        config_name="日常批量",
        scripts=[("daily", "日常任务")],
        workflows={},
        total_rows=1,
        batch_run_id="batch-1",
    )
    report.start_batch()
    return report


def _worker_stub(describer):
    ctx = BatchContext(
        capture=None, ocr=None, input_ctrl=None, layout=None,
        user_conflict_describer=describer,
    )
    return type("Stub", (), {"_ctx": ctx})()


def test_report_renders_the_skip_reason() -> None:
    report = _report()
    report.start_entry("甲", "甲")
    report.record_prepare("跳过")
    report.record_skip_reason("用户「甲」正在执行任务（占用目标 设备 A，运行 3f2a1b9c）")
    report.end_entry()
    report.end_batch()

    text = report.render()

    assert "跳过原因：" in text
    assert "设备 A" in text
    assert "3f2a1b9c" in text


def test_report_omits_the_line_when_there_is_no_reason() -> None:
    """正常执行的条目不该多出一行空的「跳过原因」。"""
    report = _report()
    report.start_entry("甲", "甲")
    report.record_prepare("成功")
    report.end_entry()
    report.end_batch()

    assert "跳过原因" not in report.render()


def test_conflict_description_names_target_and_run() -> None:
    worker = _worker_stub(
        lambda username: f"占用目标 设备 A，运行 3f2a1b9c（{username}）")

    reason = BatchWorker._describe_user_conflict(
        worker, "甲", AccessDeniedError("用户「甲」正在执行任务，请稍后重试"))

    assert "正在执行任务" in reason
    assert "设备 A" in reason
    assert "3f2a1b9c" in reason


def test_conflict_description_falls_back_to_the_raw_error() -> None:
    """宿主没接描述器、查不到占用或查询本身抛错时，退回异常原文。

    宁可少一段上下文，也不能凭空编一个目标名出来。
    """
    exc = AccessDeniedError("用户「甲」正在执行任务，请稍后重试")

    assert BatchWorker._describe_user_conflict(
        _worker_stub(None), "甲", exc) == str(exc)
    assert BatchWorker._describe_user_conflict(
        _worker_stub(lambda username: ""), "甲", exc) == str(exc)

    def boom(username: str) -> str:
        raise RuntimeError("RunManager 不可用")

    assert BatchWorker._describe_user_conflict(
        _worker_stub(boom), "甲", exc) == str(exc)
    # 属性单元在条目准备失败时还不知道用户名，也只能给原文
    assert BatchWorker._describe_user_conflict(
        _worker_stub(lambda username: "不该被调用"), "", exc) == str(exc)

# ─── 占用归属由宿主回答 ─────────────────────────────────

def _registry() -> tuple[ExecutionTargetRegistry, ExecutionTarget, ExecutionTarget]:
    registry = ExecutionTargetRegistry()
    window = ExecutionTarget(
        id=WINDOW_TARGET_ID, kind="windows", display_name="游戏窗口",
        capture=object(), input_ctrl=object(),
        window={"hwnd": 1, "left": 0, "top": 0},
    )
    device = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="设备 A",
        capture=object(), input_ctrl=object(), device=object(),
    )
    registry.put(window)
    registry.put(device)
    return registry, window, device


def _host(registry, manager, *, current=None):
    host = type("Host", (RunControlMixin,), {})()
    host._run_manager = manager
    host._execution_targets = registry
    host._current_run_context = current
    return host


def test_user_lock_conflict_names_the_occupying_target_and_run() -> None:
    """批量跳过不能只显示「跳过」：要说清被哪个目标的哪个运行实例占着。"""
    registry, window, device = _registry()
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    _, run = manager.try_begin(
        target=device.snapshot(), username="甲", name="设备任务")
    assert run is not None
    host = _host(registry, manager)

    described = host._describe_running_user("甲")

    assert "设备 A" in described
    assert run.task_run_id[:8] in described
    assert host._describe_running_user("乙") == ""
