"""批量开始前的设备执行占用预检。

设备端的执行占用只在每个条目准备时获取，手机端正在跑任务时整批会逐条失败并
重复同一句原因。预检必须先取一次占用、成功再立刻释放：漏掉释放会让整批第一项
就被自己占住，而窗口模式没有该能力，不能被这个预检拦住。
"""

from types import SimpleNamespace

from lvjiang.core.android.agent import AgentOpError
from lvjiang.ui.main.run_control import RunControlMixin


class _FakeInput:
    """记录 begin/end 调用；reason 非空时模拟设备端拒绝。"""

    def __init__(self, reason: str = ""):
        self.reason = reason
        self.calls: list[str] = []

    def begin_execution(self) -> None:
        self.calls.append("begin")
        if self.reason:
            raise AgentOpError(self.reason)

    def end_execution(self) -> None:
        self.calls.append("end")


def _host(kind: str, input_ctrl) -> RunControlMixin:
    target = SimpleNamespace(kind=kind, input_ctrl=input_ctrl)

    class Host(RunControlMixin):
        _execution_targets = SimpleNamespace(active=lambda: target)

    return Host()


def test_busy_device_returns_reason_without_taking_lease():
    fake = _FakeInput("设备正在执行其他任务")
    host = _host("adb", fake)

    assert host.device_execution_busy() == "设备正在执行其他任务"
    assert fake.calls == ["begin"]


def test_free_device_releases_the_lease_it_probed():
    fake = _FakeInput()
    host = _host("adb", fake)

    assert host.device_execution_busy() == ""
    assert fake.calls == ["begin", "end"]


def test_window_target_is_never_blocked():
    fake = _FakeInput()
    host = _host("window", fake)

    assert host.device_execution_busy() == ""
    assert fake.calls == []
