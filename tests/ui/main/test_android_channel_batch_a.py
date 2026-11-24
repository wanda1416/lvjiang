"""批次 A 回归测试：_device_thread 生命周期隔离 + 空闲流重建代次校验与非阻塞打断。"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PyQt6.QtCore import QObject, QThread

from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture
from lvjiang.ui.main.adb_device_ops import _AdbDeviceOpsMixin
from lvjiang.ui.main.execution_targets import ExecutionTarget, ExecutionTargetRegistry
from lvjiang.ui.main.run_android_recovery import _AndroidRecoveryMixin
from lvjiang.ui.main.window import MainWindow


class _FakeCapture:
    def __init__(self, name: str):
        self.name = name
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeAgent:
    def __init__(self, name: str):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class _FakeIdentity:
    def __init__(self, value: str, stable: bool = True):
        self.value = value
        self.stable = stable
        self.source = "android_id"


class _FakeDevice:
    def __init__(self, serial: str, identity: str):
        self.serial = serial
        self.adb_path = "adb"
        self._identity = _FakeIdentity(identity)

    def get_stable_identity(self):
        return self._identity


class _FakeHost(_AdbDeviceOpsMixin, _AndroidRecoveryMixin):
    def __init__(self):
        self._execution_targets = ExecutionTargetRegistry()
        self._run_manager = SimpleNamespace(run_for_target=lambda _tid: None)
        self._user_config = SimpleNamespace(
            input_sim=SimpleNamespace(),
            hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"),
        )
        self.logs: list[str] = []
        self.status_messages: list[str] = []
        self.btn_locate = SimpleNamespace(setText=lambda _t: None)
        self._adb_banner = SimpleNamespace(setVisible=lambda _v: None)
        self._adb_banner_label = SimpleNamespace(setText=lambda _t: None)
        self._adb_banner_btn = None
        self._adb_banner_target_id = None
        self._stream_recheck_bridge = None

    def append_log(self, msg: str):
        self.logs.append(msg)

    def statusBar(self):
        return SimpleNamespace(showMessage=lambda msg: self.status_messages.append(msg))

    def _set_locate_enabled(self, _enabled: bool):
        pass

    def _unconfirmed_identity_conflict(self, _tid, _stable):
        return None

    def _release_rejected_connection(self, capture, agent):
        if capture is not None:
            capture.stop()
        if agent is not None:
            agent.close()

    def _install_stream_hooks(self, *_args, **_kwargs):
        pass

    def _activate_connected_target(self, target, _old):
        self._execution_targets.select(target.id)

    def _hotkey_status(self, prefix, *_args):
        return prefix

    def _refresh_execution_targets_ui(self):
        pass

    def _refresh_active_target_ui(self):
        pass

    def _refresh_run_button(self):
        pass

    def findChildren(self, _cls):
        return []


def test_wait_device_thread_retires_running_thread_without_crashing(qapp):
    """运行中 _device_thread 未在 200ms 退出时移入 retired 集合保活，不触发 qFatal。"""
    host = _FakeHost()
    release = threading.Event()
    cancelled = threading.Event()

    class _BlockingWorker(QObject):
        def cancel(self):
            cancelled.set()

        def run(self):
            release.wait(5.0)

    thread = QThread()
    worker = _BlockingWorker()
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    host._device_thread = thread
    host._device_worker = worker
    thread.start()
    time.sleep(0.05)

    host._wait_device_thread()
    assert cancelled.is_set()
    assert thread in host._retired_device_threads()
    assert worker in host._retired_device_workers()

    # _stop_running_qthreads 必须纳入 retired 线程与 worker
    release.set()
    thread.quit()
    host._device_thread = None
    host._device_worker = None
    MainWindow._stop_running_qthreads(host, timeout_ms=2000)
    assert not thread.isRunning()
    qapp.processEvents()


def test_stale_connect_seq_releases_resources_without_overwriting_newer_target(qapp, monkeypatch):
    """过期连接回调到达时释放旧 capture/agent，绝不覆盖同目标的新连接。"""
    monkeypatch.setattr(
        "lvjiang.core.android.create_input_backend",
        lambda **_kw: SimpleNamespace(kind="adb"),
    )
    monkeypatch.setattr(
        "lvjiang.core.app_controller.record_connected_android",
        lambda *_a, **_kw: {},
    )
    host = _FakeHost()
    seq1 = host._next_device_connect_seq()
    seq2 = host._next_device_connect_seq()

    dev_new = _FakeDevice("10.0.0.2:5555", "id-A")
    cap_new = _FakeCapture("new")
    agent_new = _FakeAgent("new")
    host._on_connect_done(
        {"serial": "10.0.0.2:5555"}, dev_new, cap_new, "screencap", 1280, 720,
        agent=agent_new, update_candidate_ui=False, connect_seq=seq2,
    )

    dev_old = _FakeDevice("10.0.0.1:5555", "id-A")
    cap_old = _FakeCapture("old")
    agent_old = _FakeAgent("old")
    host._on_connect_done(
        {"serial": "10.0.0.1:5555"}, dev_old, cap_old, "screencap", 1280, 720,
        agent=agent_old, update_candidate_ui=False, connect_seq=seq1,
    )

    assert cap_old.stopped is True
    assert agent_old.closed is True
    assert cap_new.stopped is False
    target = host._execution_targets.all()[0]
    assert target.serial == "10.0.0.2:5555"
    assert target.capture is cap_new


def test_stream_recheck_isolated_per_target_and_ignores_stale_capture(qapp, monkeypatch):
    """双目标空闲重建互不阻挡；重建期间切换 capture 后旧失败结果不反写健康目标横幅。"""
    gate_a = threading.Event()
    gate_b = threading.Event()

    class _FakeStreamCapture(AndroidStreamCapture):
        def __init__(self, gate: threading.Event, ok: bool):
            self._gate = gate
            self._ok = ok
            self._last_error = "5 秒内未收到首帧"

        def restart(self) -> bool:
            self._gate.wait(5.0)
            return self._ok

    host = _FakeHost()
    cap_a1 = _FakeStreamCapture(gate_a, ok=False)
    cap_b1 = _FakeStreamCapture(gate_b, ok=True)
    target_a = ExecutionTarget(id="adb:A", kind="adb", display_name="A", capture=cap_a1, input_ctrl=object())
    target_b = ExecutionTarget(id="adb:B", kind="adb", display_name="B", capture=cap_b1, input_ctrl=object())
    host._execution_targets.put(target_a)
    host._execution_targets.put(target_b)

    # 先启动 A 重建，再切到 B 启动 B 重建——两者互不阻挡
    host._execution_targets.select("adb:A")
    assert host._recheck_active_stream() is True
    host._execution_targets.select("adb:B")
    assert host._recheck_active_stream() is True
    assert set(host._stream_recheck_bridges().keys()) == {"adb:A", "adb:B"}

    # 在 A 重建完成前，A 切换为新的健康单帧截图
    cap_a2 = _FakeCapture("screencap-healthy")
    host._execution_targets.put(ExecutionTarget(id="adb:A", kind="adb", display_name="A", capture=cap_a2, input_ctrl=object()))

    # 放行 A（失败）与 B（成功）
    gate_a.set()
    gate_b.set()
    deadline = time.monotonic() + 3.0
    while host._stream_recheck_bridges() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    qapp.processEvents()

    assert not host._stream_recheck_bridges()
    assert "adb:A" not in host._channel_banner_texts()


def test_scrcpy_stop_interrupts_restart_without_blocking():
    """restart() 阻塞期间并发调用 stop() 立即返回，且 restart() 提前退出不启解码线程。"""
    entered_push = threading.Event()
    release_push = threading.Event()

    device = SimpleNamespace(
        serial="dev-1",
        get_resolution=lambda: (1280, 720),
        forward_dynamic=lambda _spec: 27183,
        remove_forward=lambda _spec: None,
    )
    cap = AndroidStreamCapture(device=device, max_size=1280)

    def _slow_push():
        entered_push.set()
        release_push.wait(5.0)
        return True

    cap._push_jar = _slow_push  # type: ignore[method-assign]
    result: list[bool] = []
    t = threading.Thread(target=lambda: result.append(cap.restart()), daemon=True)
    t.start()
    assert entered_push.wait(2.0)

    t0 = time.monotonic()
    cap.stop()
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"stop() 被 _lifecycle_lock 阻塞了 {elapsed:.2f}s"

    release_push.set()
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert result == [False]
    assert cap._decode_thread is None
