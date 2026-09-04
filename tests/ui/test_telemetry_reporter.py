"""TelemetryReporter(QThread)：worker 线程绝不写 SessionStore、响应体
被忽略、关闭状态下 build_job() 绝不触碰 identity。

放 tests/ui 而非 tests/core：TelemetryReporter 继承 QThread，需要 QApplication
（qtbot fixture）才能安全实例化/start()，与项目里其它 QThread 测试同规矩。
"""
from __future__ import annotations

import threading

import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import consent
from lvjiang.core.telemetry import reporter as reporter_mod
from lvjiang.core.telemetry import spool as spool_mod
from lvjiang.core.telemetry.schema import EventSchema, FieldSpec


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    monkeypatch.setattr("lvjiang.core.telemetry.consent._is_dev_build", lambda: False)
    reset_session_store()
    yield
    reset_session_store()


def _run_reporter(qtbot, rep):
    """启动上报线程并等它真正退出，返回 finished_ok 的载荷。

    waitSignal 在 run() 里 emit 的瞬间就返回，那一刻线程还没退出。用例一
    结束 rep 的最后一个引用消失，PyQt 就地销毁一个仍在运行的 QThread，
    进程直接崩——并发满载下稳定复现，串行只是靠运气躲过。生产侧不会
    踩到：那里线程有 parent，还被 self._startup_telemetry_reporter 强引用着。
    """
    with qtbot.waitSignal(rep.finished_ok, timeout=3000) as blocker:
        rep.start()
    assert rep.wait(5000), "上报线程未在 5s 内退出"
    return blocker.args[0]


class TestBuildJobGatesOnConsent:
    def test_disabled_job_is_empty_and_touches_no_identity(self):
        job = reporter_mod.build_job()
        assert job.is_empty
        from lvjiang.core.telemetry.identity import identity_path
        assert not identity_path().exists()

    def test_enabled_with_nothing_pending_still_has_heartbeat(self):
        consent.record_consent_choice(True)
        job = reporter_mod.build_job()
        assert job.heartbeat is not None
        assert job.batches == ()

    def test_enabled_after_heartbeat_already_sent_today_has_no_heartbeat(self):
        from lvjiang.core.telemetry import heartbeat as heartbeat_mod
        consent.record_consent_choice(True)
        heartbeat_mod.mark_attempt(success=True)
        job = reporter_mod.build_job()
        assert job.heartbeat is None
        assert job.is_empty


class TestReporterThreadNeverTouchesSessionStore(object):
    def test_worker_thread_makes_zero_sessionstore_calls(self, qtbot, monkeypatch):
        consent.record_consent_choice(True)
        schema = EventSchema(name="t", version=1, fields=(FieldSpec("x", str, choices=("a",)),))
        spool_mod.append(schema.validate({"x": "a"}))
        spool_mod.flush()

        job = reporter_mod.build_job()
        assert job.batches  # 确认真的有待发批次

        calls_from_worker = []
        from lvjiang.core.config.session import SessionStore
        original_mutate = SessionStore.mutate_node

        def _spy_mutate(self, *a, **k):
            if threading.current_thread() is not threading.main_thread():
                calls_from_worker.append(1)
            return original_mutate(self, *a, **k)

        monkeypatch.setattr(SessionStore, "mutate_node", _spy_mutate)
        monkeypatch.setattr(reporter_mod, "post_report", lambda envelope: True)

        rep = reporter_mod.TelemetryReporter(job)
        qtbot.addWidget(rep) if hasattr(rep, "show") else None
        outcome = _run_reporter(qtbot, rep)
        assert not calls_from_worker  # 唯一断言：worker 线程零次触碰 SessionStore

        # apply_outcome 在主线程执行，这里才允许写 SessionStore
        reporter_mod.apply_outcome(outcome)
        assert spool_mod.take_batches(10) == []  # 成功上报的批次已被清理


class TestResponseBodyNeverActedOn:
    def test_post_report_return_value_is_the_only_signal(self, qtbot, monkeypatch):
        """就算 transport 层本该忽略 body，这里再确认 reporter 侧同样只
        依据 post_report() 的布尔返回值决定结果，不做任何二次解析。"""
        consent.record_consent_choice(True)
        job = reporter_mod.build_job()
        monkeypatch.setattr(reporter_mod, "post_report", lambda envelope: True)
        rep = reporter_mod.TelemetryReporter(job)
        outcome = _run_reporter(qtbot, rep)
        assert outcome.heartbeat_ok is True


class TestEmptyJobShortCircuits:
    def test_empty_job_emits_immediately_without_network(self, qtbot, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("空 job 不应该发起任何网络请求")
        monkeypatch.setattr(reporter_mod, "post_report", _boom)
        job = reporter_mod.ReportJob(heartbeat=None, batches=())
        rep = reporter_mod.TelemetryReporter(job)
        outcome = _run_reporter(qtbot, rep)
        assert outcome.heartbeat_attempted is False
        assert outcome.sent_batches == ()
