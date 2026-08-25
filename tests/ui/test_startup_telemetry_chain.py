"""启动链收尾：更新检查（成功/失败均可） → 首启同意提示 → 统计上报。

公告→更新的既有顺序测试见 test_startup_announcements.py；本文件补上
更新检查之后新增的两段，且断言两条路径（更新弹窗 / 检查失败）都必须
走到底，不能被中途的异常吞掉（对照现有 try/finally 收口写法）。
"""
from __future__ import annotations

from types import SimpleNamespace

from lvjiang.ui.main_window import MainWindow


class FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, value):
        for callback in self._callbacks:
            callback(value)


def _host(events):
    return SimpleNamespace(
        _continue_after_update_check=lambda: events.append("continue"))


class TestUpdateCheckAlwaysContinues:
    def test_finished_without_dialog_still_continues(self, monkeypatch):
        import lvjiang.core.update as update_mod
        events = []

        class FakeChecker:
            def __init__(self, parent):
                self.finished = FakeSignal()
                self.error = FakeSignal()

            def start(self):
                self.finished.emit(SimpleNamespace(version="1.0.0"))

        monkeypatch.setattr(update_mod, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(update_mod, "should_prompt_update", lambda v: False)
        MainWindow._start_update_check_on_startup(_host(events))
        assert events == ["continue"]

    def test_finished_with_dialog_shown_still_continues(self, monkeypatch):
        import lvjiang.core.update as update_mod
        import lvjiang.ui.update_dialog as dialog_mod
        events = []

        class FakeChecker:
            def __init__(self, parent):
                self.finished = FakeSignal()
                self.error = FakeSignal()

            def start(self):
                self.finished.emit(SimpleNamespace(version="9.9.9"))

        class FakeDialog:
            def __init__(self, release, parent):
                events.append("dialog")

            def exec(self):
                pass

        monkeypatch.setattr(update_mod, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(update_mod, "should_prompt_update", lambda v: True)
        monkeypatch.setattr(dialog_mod, "UpdateDialog", FakeDialog)
        MainWindow._start_update_check_on_startup(_host(events))
        assert events == ["dialog", "continue"]

    def test_dialog_exception_does_not_swallow_continuation(self, monkeypatch):
        """更新弹窗抛异常时，统计上报不能被一并吞掉——这正是把 continue
        放进 try/finally 而不是 try 尾部的原因。"""
        import lvjiang.core.update as update_mod
        import lvjiang.ui.update_dialog as dialog_mod
        events = []

        class FakeChecker:
            def __init__(self, parent):
                self.finished = FakeSignal()
                self.error = FakeSignal()

            def start(self):
                self.finished.emit(SimpleNamespace(version="9.9.9"))

        class BoomDialog:
            def __init__(self, release, parent):
                raise RuntimeError("boom")

        monkeypatch.setattr(update_mod, "UpdateChecker", FakeChecker)
        monkeypatch.setattr(update_mod, "should_prompt_update", lambda v: True)
        monkeypatch.setattr(dialog_mod, "UpdateDialog", BoomDialog)
        try:
            MainWindow._start_update_check_on_startup(_host(events))
        except RuntimeError:
            pass
        assert events == ["continue"]

    def test_error_path_still_continues(self, monkeypatch):
        import lvjiang.core.update as update_mod
        events = []

        class FakeChecker:
            def __init__(self, parent):
                self.finished = FakeSignal()
                self.error = FakeSignal()

            def start(self):
                self.error.emit("network down")

        monkeypatch.setattr(update_mod, "UpdateChecker", FakeChecker)
        MainWindow._start_update_check_on_startup(_host(events))
        assert events == ["continue"]


class TestContinueAfterUpdateCheck:
    def test_prompts_consent_then_starts_report(self, monkeypatch):
        import lvjiang.ui.telemetry_consent_dialog as consent_dialog_mod
        events = []
        monkeypatch.setattr(consent_dialog_mod, "maybe_prompt_and_record",
                            lambda parent: events.append("consent"))
        host = SimpleNamespace(
            _start_telemetry_report_on_startup=lambda: events.append("report"))
        MainWindow._continue_after_update_check(host)
        assert events == ["consent", "report"]


class TestStartTelemetryReportOnStartup:
    def test_empty_job_starts_no_reporter(self, monkeypatch):
        import lvjiang.core.telemetry.reporter as reporter_mod
        monkeypatch.setattr(reporter_mod, "build_job",
                            lambda: reporter_mod.ReportJob(heartbeat=None, batches=()))
        host = SimpleNamespace()
        MainWindow._start_telemetry_report_on_startup(host)
        assert not hasattr(host, "_startup_telemetry_reporter")

    def test_nonempty_job_starts_and_pins_reporter(self, monkeypatch, qtbot):
        import lvjiang.core.telemetry.reporter as reporter_mod

        job = reporter_mod.ReportJob(heartbeat={"x": 1}, batches=())
        monkeypatch.setattr(reporter_mod, "build_job", lambda: job)

        started = []

        class FakeReporter:
            def __init__(self, job, parent=None):
                self.finished_ok = FakeSignal()
                self.failed = FakeSignal()

            def start(self):
                started.append(1)

        monkeypatch.setattr(reporter_mod, "TelemetryReporter", FakeReporter)
        host = SimpleNamespace()
        MainWindow._start_telemetry_report_on_startup(host)
        assert started == [1]
        assert hasattr(host, "_startup_telemetry_reporter")  # 防止被 GC
