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

    # 运行 ID 用 ` - ` 分隔：` · ` 已经是目标名内部（型号 · 连接地址）的分隔符
    assert event.display_text() == "[手机 A - 12345678] 任务消息"


def test_display_keeps_the_run_id_separable_from_the_target_name() -> None:
    """真实目标名自带 ` · `，两级必须用不同符号才看得出哪一段是任务。

    设备名是「型号 · 连接地址」，再用 ` · ` 接运行 ID 的话，一行里三段同级，
    读的人分不出哪段是设备、哪段是任务——重连导致名字变长时尤其明显。
    """
    event = RunLogEvent(
        20, "任务消息", task_run_id="4edcc5ba1234",
        target_id="android:a49d44", target_label="V2415A · 192.168.1.9:5555",
    )

    text = event.display_text()

    assert text == "[V2415A · 192.168.1.9:5555 - 4edcc5ba] 任务消息"
    head, _, run_id = text[1:text.index("]")].rpartition(" - ")
    assert head == "V2415A · 192.168.1.9:5555"
    assert run_id == "4edcc5ba"
