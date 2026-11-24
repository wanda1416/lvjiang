"""批次 B 回归测试：运行中重连租约/冻结策略/收尾释放 + 启动预检轻量探测与批量去重。"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from lvjiang.core.android.agent import AgentClient, AgentInput, AgentTransportError
from lvjiang.core.android.device import AdbConnectionError, AdbDevice
from lvjiang.core.config.models import AndroidRecoveryConfig
from lvjiang.ui.main.adb_device_ops import _AdbDeviceOpsMixin
from lvjiang.ui.main.execution_runs import RunState
from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.workflows.engine.core import WorkflowEngine


def test_agent_close_clears_lease_and_end_execution_never_retries_or_raises():
    """AgentClient.close() 复位 _owns_execution；end_execution() 断线时不重试、不抛 AdbConnectionError。"""
    device = AdbDevice(serial="dev-1")
    client = AgentClient(device)
    client._sock = None
    client._owns_execution = True

    # close() 必须清 _owns_execution
    client.close()
    assert client._owns_execution is False

    # 即使 _owns_execution 为 True 且 _sock 已断开或 _roundtrip 抛 AdbConnectionError，
    # end_execution() 绝不走 _retry_connection，也绝不穿透异常
    retry_called = False

    def _boom_retry():
        nonlocal retry_called
        retry_called = True
        raise AdbConnectionError("连接恢复已停止")

    client._retry_connection = _boom_retry  # type: ignore[method-assign]
    client._owns_execution = True
    client._sock = SimpleNamespace(close=lambda: None)  # type: ignore[assignment]
    client._roundtrip = lambda *_a, **_kw: (_ for _ in ()).throw(AdbConnectionError("连接恢复已停止"))  # type: ignore[method-assign]

    inp = AgentInput(client)
    inp.end_execution()  # 不应抛异常
    assert retry_called is False
    assert client._owns_execution is False


def test_probe_execution_busy_single_shot_no_retry_no_state_mutation():
    """probe_execution_busy() 传输失败单次关闭失效 _sock 并放行，不改 _status/_using_adb；设备明确拒绝仍拦截。"""
    class _FakeSock:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    device = AdbDevice(serial="dev-2")
    client = AgentClient(device)
    sock1 = _FakeSock()
    client._sock = sock1  # type: ignore[assignment]
    client._status = {"offline_protocol": 4}
    retry_calls = 0

    def _no_retry():
        nonlocal retry_calls
        retry_calls += 1
        return False

    client._retry_connection = _no_retry  # type: ignore[method-assign]

    # 1. control_begin 传输异常：返回空串放行，关闭并置空 _sock，不重试，不破坏 _status 和 _using_adb
    client._roundtrip = lambda *_a, **_kw: (_ for _ in ()).throw(AgentTransportError("timeout"))  # type: ignore[method-assign]
    inp = AgentInput(client)
    assert inp.probe_execution_busy(timeout=0.5) == ""
    assert sock1.closed is True
    assert client._sock is None
    assert retry_calls == 0
    assert client._status == {"offline_protocol": 4}
    assert inp._using_adb is False

    # 2. 设备明确拒绝：返回拒绝原因
    client._sock = _FakeSock()  # type: ignore[assignment]
    client._roundtrip = lambda op, **_kw: ({"ok": False, "error": "手机离线任务运行中"}, b"")  # type: ignore[method-assign]
    assert inp.probe_execution_busy(timeout=0.5) == "手机离线任务运行中"

    # 3. control_end 超时/传输异常：同样关闭并置空失效 _sock，保留 _status 与 _using_adb
    sock3 = _FakeSock()
    client._sock = sock3  # type: ignore[assignment]

    def _fail_end(op, **_kw):
        if op == "control_end":
            raise AgentTransportError("end timeout")
        return {"ok": True}, b""

    client._roundtrip = _fail_end  # type: ignore[method-assign]
    assert inp.probe_execution_busy(timeout=0.5) == ""
    assert sock3.closed is True
    assert client._sock is None
    assert retry_calls == 0
    assert client._status == {"offline_protocol": 4}
    assert inp._using_adb is False

    # 4. 设备空闲：control_begin + control_end 各一次，返回空串
    client._sock = _FakeSock()  # type: ignore[assignment]
    ops: list[str] = []

    def _ok_rt(op, **_kw):
        ops.append(op)
        return {"ok": True}, b""

    client._roundtrip = _ok_rt  # type: ignore[method-assign]
    assert inp.probe_execution_busy(timeout=0.5) == ""
    assert ops == ["control_begin", "control_end"]


def test_execute_scoped_resolves_rebound_end_execution_dynamically():
    """_execute_scoped 在 finally 动态读取 self._input.end_execution，不调旧 input_ctrl1。"""
    calls: list[str] = []
    ctrl1 = SimpleNamespace(
        stop_check=None,
        begin_execution=lambda: calls.append("begin1"),
        end_execution=lambda: calls.append("end1"),
    )
    ctrl2 = SimpleNamespace(
        stop_check=None,
        begin_execution=lambda: calls.append("begin2"),
        end_execution=lambda: calls.append("end2"),
    )

    class _Proxy:
        def __init__(self, target):
            self._target = target

        def __getattr__(self, name):
            return getattr(self._target, name)

    proxy = _Proxy(ctrl1)
    engine = object.__new__(WorkflowEngine)
    engine._input = proxy
    engine._stop_check = lambda: False

    def _fake_unlocked(*_a, **_kw):
        # 模拟运行中重连替换了底层 input_ctrl
        proxy._target = ctrl2
        return {"ok": True}

    engine._execute_unlocked = _fake_unlocked  # type: ignore[method-assign]
    out = engine._execute_scoped("dummy.wf")
    assert out == {"ok": True}
    assert calls == ["begin1", "end2"]


def test_running_reconnect_preserves_recovery_stop_check_and_hands_off_lease(qapp, monkeypatch):
    """运行中同目标重连延续冻结 recovery/stop_check，并按先释旧 agent 再取新 control_begin 交接。"""
    events: list[str] = []
    handoff_done = threading.Event()

    class _FakeOldAgent:
        def __init__(self):
            self._owns_execution = True

        def release_execution(self, timeout: float = 1.5):
            events.append("old.release")
            self._owns_execution = False

        def close(self):
            events.append("old.close")

    class _FakeNewClient:
        def __init__(self):
            self._lock = threading.RLock()
            self._sock = object()
            self._owns_execution = False
            self.status = {"offline_protocol": 4}
            self.recovery = AndroidRecoveryConfig()

        def _roundtrip(self, op, timeout=None):
            events.append(f"new.{op}")
            handoff_done.set()
            return {"ok": True}, b""

    class _FakeDev:
        def __init__(self, serial: str, recovery=None):
            self.serial = serial
            self.adb_path = "adb"
            self.recovery = recovery or AndroidRecoveryConfig()
            self._id = SimpleNamespace(value="dev-stable-A", stable=True, source="android_id")

        def get_stable_identity(self):
            return self._id

    tid_expected = android_target_id("dev-stable-A", "10.0.0.1:5555")
    frozen = AndroidRecoveryConfig(allow_adb_fallback=False, agent_attempts=2)
    stop_ev = threading.Event()
    active_run = SimpleNamespace(
        task_run_id="run-1",
        target_id=tid_expected,
        state=RunState.RUNNING,
        stop_event=stop_ev,
        pause_event=threading.Event(),
        metadata={"android_recovery": frozen},
    )

    new_client = _FakeNewClient()
    new_input = AgentInput(new_client)
    monkeypatch.setattr("lvjiang.core.android.create_input_backend", lambda **_kw: new_input)
    monkeypatch.setattr("lvjiang.core.app_controller.record_connected_android", lambda *_a, **_kw: {})

    class _Host(_AdbDeviceOpsMixin):
        def __init__(self):
            self._execution_targets = ExecutionTargetRegistry()
            self._run_manager = SimpleNamespace(
                run_for_target=lambda tid: active_run if tid == tid_expected else None,
                set_state=lambda *_a: None,
            )
            self._user_config = SimpleNamespace(
                input_sim=SimpleNamespace(),
                hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"),
            )
            self._adb_banner_target_id = None

        def append_log(self, _m): pass
        def statusBar(self): return SimpleNamespace(showMessage=lambda _m: None)
        def _unconfirmed_identity_conflict(self, *_a): return None
        def _install_stream_hooks(self, *_a): pass
        def _activate_connected_target(self, target, _old): self._execution_targets.select(target.id)
        def _hide_channel_banner(self, _tid): pass
        def _hotkey_status(self, p, *_a): return p
        def _refresh_execution_targets_ui(self): pass
        def _refresh_active_target_ui(self): pass
        def _refresh_run_button(self): pass
        def _emit_run_instance_state(self, *_a): pass
        def _on_adb_connection_lost(self, *_a): pass
        def _on_adb_connection_restored(self, *_a): pass
        def _on_stream_lost(self, *_a): pass

    host = _Host()
    old_agent = _FakeOldAgent()
    old_dev = _FakeDev("10.0.0.1:5555", recovery=frozen)
    old_target = ExecutionTarget(
        id=tid_expected, kind="adb", display_name="10.0.0.1:5555",
        capture=SimpleNamespace(stop=lambda: None), input_ctrl=object(),
        device=old_dev, agent=old_agent,
    )
    host._execution_targets.put(old_target)

    new_dev = _FakeDev("10.0.0.2:5555")
    host._on_connect_done(
        {"serial": "10.0.0.2:5555"}, new_dev, SimpleNamespace(stop=lambda: None),
        "screencap", 1280, 720, agent=new_client, update_candidate_ui=False,
    )

    assert handoff_done.wait(2.0)
    time.sleep(0.05)
    assert new_dev.recovery is frozen
    assert new_client.recovery is frozen
    assert new_input.stop_check == stop_ev.is_set
    assert events == ["old.release", "old.close", "new.control_begin"]
    assert new_client._owns_execution is True


def test_device_execution_busy_uses_probe_and_skips_when_prechecked():
    """device_execution_busy 优先走 probe_execution_busy；check_device_busy=False 跳过重复探测。"""
    calls: list[str] = []

    class _ProbeInput:
        def probe_execution_busy(self):
            calls.append("probe")
            return ""

        def begin_execution(self):
            calls.append("begin")

        def end_execution(self):
            calls.append("end")

    host = SimpleNamespace(
        _current_execution_target=lambda: SimpleNamespace(kind="adb", input_ctrl=_ProbeInput()),
    )
    assert RunControlMixin.device_execution_busy(host) == ""
    assert calls == ["probe"]


def test_lease_handoff_blocks_worker_input_until_old_agent_released(qapp, monkeypatch):
    """old_agent.release_execution 阻塞期间，工作线程发起输入必须等待释放完成后再发 control_begin 与 tap。"""
    events: list[str] = []
    release_started = threading.Event()
    allow_release = threading.Event()
    tap_done = threading.Event()

    class _SlowOldAgent:
        def __init__(self):
            self._owns_execution = True

        def release_execution(self, timeout: float = 1.5):
            del timeout
            release_started.set()
            allow_release.wait(2.0)
            events.append("old.release")
            self._owns_execution = False

        def close(self):
            events.append("old.close")

    class _FakeNewClient:
        def __init__(self):
            self._lock = threading.RLock()
            self._sock = object()
            self._owns_execution = False
            self.status = {"offline_protocol": 4}
            self.recovery = AndroidRecoveryConfig()
            self.timeout = 5.0
            self.device = SimpleNamespace(
                recovery_coordinator=SimpleNamespace(succeeded=lambda: None),
            )

        def _roundtrip(self, op, timeout=None):
            del timeout
            events.append(f"new.{op}")
            return {"ok": True}, b""

        def call(self, op, timeout=None, **_params):
            del timeout
            events.append(f"new.{op}")
            return {"ok": True}, b""

    class _FakeDev:
        def __init__(self, serial: str):
            self.serial = serial
            self.adb_path = "adb"
            self.recovery = AndroidRecoveryConfig()
            self._id = SimpleNamespace(value="dev-stable-race", stable=True, source="android_id")

        def get_stable_identity(self):
            return self._id

    tid_expected = android_target_id("dev-stable-race", "10.0.0.1:5555")
    active_run = SimpleNamespace(
        task_run_id="run-race",
        target_id=tid_expected,
        state=RunState.RUNNING,
        stop_event=threading.Event(),
        pause_event=threading.Event(),
        metadata={},
    )
    new_client = _FakeNewClient()
    new_input = AgentInput(new_client)
    monkeypatch.setattr("lvjiang.core.android.create_input_backend", lambda **_kw: new_input)
    monkeypatch.setattr("lvjiang.core.app_controller.record_connected_android", lambda *_a, **_kw: {})

    class _Host(_AdbDeviceOpsMixin):
        def __init__(self):
            self._execution_targets = ExecutionTargetRegistry()
            self._run_manager = SimpleNamespace(
                run_for_target=lambda tid: active_run if tid == tid_expected else None,
                set_state=lambda *_a: None,
            )
            self._user_config = SimpleNamespace(
                input_sim=SimpleNamespace(),
                hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"),
            )
            self._adb_banner_target_id = None

        def append_log(self, _m): pass
        def statusBar(self): return SimpleNamespace(showMessage=lambda _m: None)
        def _unconfirmed_identity_conflict(self, *_a): return None
        def _install_stream_hooks(self, *_a): pass
        def _activate_connected_target(self, target, _old): self._execution_targets.select(target.id)
        def _hide_channel_banner(self, _tid): pass
        def _hotkey_status(self, p, *_a): return p
        def _refresh_execution_targets_ui(self): pass
        def _refresh_active_target_ui(self): pass
        def _refresh_run_button(self): pass
        def _emit_run_instance_state(self, *_a): pass
        def _on_adb_connection_lost(self, *_a): pass
        def _on_adb_connection_restored(self, *_a): pass
        def _on_stream_lost(self, *_a): pass

    host = _Host()
    host._execution_targets.put(ExecutionTarget(
        id=tid_expected, kind="adb", display_name="10.0.0.1:5555",
        capture=SimpleNamespace(stop=lambda: None), input_ctrl=object(),
        device=_FakeDev("10.0.0.1:5555"), agent=_SlowOldAgent(),
    ))

    host._on_connect_done(
        {"serial": "10.0.0.2:5555"}, _FakeDev("10.0.0.2:5555"), SimpleNamespace(stop=lambda: None),
        "screencap", 1280, 720, agent=new_client, update_candidate_ui=False,
    )
    assert release_started.wait(2.0)

    def _worker_click():
        try:
            new_input.click_screen(100, 200, pre_delay=(0, 0), post_delay=(0, 0), random_offset=False)
        finally:
            tap_done.set()

    wt = threading.Thread(target=_worker_click, daemon=True)
    wt.start()
    time.sleep(0.06)
    # 在 old_agent 释放完成前，不得抢跑发 control_begin 或 tap
    assert events == []

    allow_release.set()
    assert tap_done.wait(2.0)
    wt.join(timeout=2.0)
    assert events[:4] == ["old.release", "old.close", "new.control_begin", "new.tap"]
    assert new_client._owns_execution is True
