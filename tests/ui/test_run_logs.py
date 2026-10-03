"""实时日志按结构化执行目标过滤。"""
from types import SimpleNamespace

from lvjiang.ui.main.run_logs import RunLogEvent
from lvjiang.ui.main.window import MainWindow


def test_target_scope_uses_event_target_id_instead_of_message_text() -> None:
    host = SimpleNamespace(
        _log_scope_combo=SimpleNamespace(currentData=lambda: "target"),
        _execution_targets=SimpleNamespace(active_target_id="target-a"),
    )
    matching = RunLogEvent(
        20, "正文里没有目标名", target_id="target-a")
    other = RunLogEvent(
        20, "即使正文写 target-a 也不属于它", target_id="target-b")

    assert MainWindow._log_event_visible(host, matching)
    assert not MainWindow._log_event_visible(host, other)


def test_run_event_display_marks_target_and_short_run_id() -> None:
    event = RunLogEvent(
        20, "任务消息", task_run_id="1234567890abcdef",
        target_id="android:device-a", target_label="手机 A",
    )

    assert event.display_text() == "[手机 A · 12345678] 任务消息"
