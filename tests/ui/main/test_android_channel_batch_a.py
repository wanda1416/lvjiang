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


def test_wait_device_thread_restores_scan_buttons_and_skips_cancelling_connect(qapp):
    """扫描被非扫描操作顶替时恢复扫描按钮；等待连接线程时不得静默 cancel() 他目标连接。"""
    from PyQt6.QtWidgets import QPushButton

    host = _FakeHost()
    host.btn_scan_window = QPushButton()
    host.btn_scan_device = QPushButton("取消扫描")
    host.btn_scan_window.setEnabled(False)
    host.btn_scan_device.setEnabled(False)

    # 1. 扫描 worker 被非扫描操作顶替：立即恢复扫描按钮并取消旧扫描
    scan_release = threading.Event()
    scan_cancelled = threading.Event()

    class _ScanWorker(QObject):
        _task = "scan"

        def cancel(self):
            scan_cancelled.set()

        def run(self):
            scan_release.wait(5.0)

    scan_thread = QThread()
    scan_worker = _ScanWorker()
    scan_worker.moveToThread(scan_thread)
    scan_thread.started.connect(scan_worker.run)
    host._device_thread = scan_thread
    host._device_worker = scan_worker
    scan_thread.start()
    time.sleep(0.05)

    host._wait_device_thread(replacing_with_scan=False)
    assert scan_cancelled.is_set()
    assert host.btn_scan_window.isEnabled() is True
    assert host.btn_scan_device.isEnabled() is True
    assert host.btn_scan_device.text() == "扫描设备"

    # 2. 连接 worker（_task == "connect"）不得被 cancel() 吞掉错误
    conn_release = threading.Event()
    conn_cancelled = threading.Event()

    class _ConnWorker(QObject):
        _task = "connect"

        def cancel(self):
            conn_cancelled.set()

        def run(self):
            conn_release.wait(5.0)

    conn_thread = QThread()
    conn_worker = _ConnWorker()
    conn_worker.moveToThread(conn_thread)
    conn_thread.started.connect(conn_worker.run)
    host._device_thread = conn_thread
    host._device_worker = conn_worker
    conn_thread.start()
    time.sleep(0.05)

    host._wait_device_thread(replacing_with_scan=False)
    assert not conn_cancelled.is_set()

    scan_release.set()
    conn_release.set()
    host._device_thread = None
    host._device_worker = None
    MainWindow._stop_running_qthreads(host, timeout_ms=2000)
    qapp.processEvents()


def test_candidate_connect_restores_locate_button_after_background_reconnect(qapp, monkeypatch):
    """候选区连接被右键重连顶替后，原候选连接收尾（含过期分支）仍必须恢复 btn_locate。"""
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import QPushButton

    monkeypatch.setattr(
        "lvjiang.ui.main.adb_device_ops.load_user_config",
        lambda: SimpleNamespace(
            android_recovery=SimpleNamespace(allow_adb_fallback=True, auto_start_shizuku=False),
        ),
    )
    monkeypatch.setattr(
        "lvjiang.core.android.create_input_backend",
        lambda **_kw: SimpleNamespace(kind="adb"),
    )
    monkeypatch.setattr(
        "lvjiang.core.app_controller.record_connected_android",
        lambda *_a, **_kw: {},
    )

    gate_fg = threading.Event()
    gate_bg = threading.Event()
    workers: list[QObject] = []

    class _StubDeviceWorker(QObject):
        scan_finished = pyqtSignal(list)
        wireless_finished = pyqtSignal(list)
        wireless_progress = pyqtSignal(str, int, int)
        connect_finished = pyqtSignal(object, object, str, int, int, object)
        notice = pyqtSignal(str)
        error = pyqtSignal(str)
        finished = pyqtSignal()

        def __init__(self, task: str, serial: str = "", **_kw):
            super().__init__()
            self._task = task
            self._serial = serial
            self._cancelled = False
            workers.append(self)

        def cancel(self):
            self._cancelled = True

        def is_cancelled(self):
            return self._cancelled

        def run(self):
            if len(workers) == 1:
                gate_fg.wait(5.0)
                dev = _FakeDevice("10.0.0.1:5555", "id-same")
                cap = _FakeCapture("fg-stale")
            else:
                gate_bg.wait(5.0)
                dev = _FakeDevice("10.0.0.2:5555", "id-same")
                cap = _FakeCapture("bg-fresh")
            self.connect_finished.emit(dev, cap, "screencap", 1280, 720, None)
            self.finished.emit()

    monkeypatch.setattr("lvjiang.ui.main.adb_device_ops._DeviceWorker", _StubDeviceWorker)

    host = _FakeHost()
    locate_states: list[bool] = []
    host.btn_locate = QPushButton("连接")
    host._set_locate_enabled = lambda en: locate_states.append(bool(en))
    host._apply_backend_ui = lambda _mode: None
    host._android_connection_draft = SimpleNamespace(capture_method="screencap", device_execution=False)
    host.log_text = SimpleNamespace(append=lambda _m: None)

    host._start_device_connection({"serial": "10.0.0.1:5555"}, update_candidate_ui=True)
    worker_fg = host._device_worker
    assert host._candidate_connect_worker is worker_fg
    assert host.btn_locate.text() == "连接中..."
    assert locate_states[-1] is False

    host._start_device_connection({"serial": "10.0.0.2:5555"}, update_candidate_ui=False)
    worker_bg = host._device_worker
    assert worker_bg is not worker_fg
    assert host._candidate_connect_worker is worker_fg

    gate_bg.set()
    deadline = time.monotonic() + 2.0
    while not host._execution_targets.all() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert host._execution_targets.all()[0].serial == "10.0.0.2:5555"
    assert host.btn_locate.text() == "连接中..."  # 右键重连不得提前复位前台候选按钮

    gate_fg.set()
    deadline = time.monotonic() + 2.0
    while host._candidate_connect_worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    assert host._candidate_connect_worker is None
    assert host.btn_locate.text() == "连接"
    assert locate_states[-1] is True
    MainWindow._stop_running_qthreads(host, timeout_ms=2000)


def test_scrcpy_step7_interrupted_by_stop_fails_cleanly_even_with_frame(monkeypatch):
    """步骤 7 等待首帧时被 stop() 打断：绝不误报息屏错，即使已写入首帧也不得返回 True。"""
    import numpy as np

    monkeypatch.setattr("lvjiang.core.android.scrcpy_capture.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        "lvjiang.core.android.scrcpy_capture.subprocess.run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    class _FakeAv:
        class CodecContext:
            @staticmethod
            def create(*_a):
                return object()

    monkeypatch.setitem(__import__("sys").modules, "av", _FakeAv)

    for write_frame_before_stop in (False, True):
        entered_wait = threading.Event()
        release_decode = threading.Event()

        device = SimpleNamespace(
            serial="dev-1",
            _base=lambda: ["adb", "-s", "dev-1"],
            get_resolution=lambda: (1280, 720),
            forward_dynamic=lambda _spec: 27183,
            remove_forward=lambda _spec: None,
        )
        cap = AndroidStreamCapture(device=device, max_size=1280)
        cap._push_jar = lambda: True  # type: ignore[method-assign]
        cap._start_server = lambda: True  # type: ignore[method-assign]
        cap._connect_socket = lambda: True  # type: ignore[method-assign]
        cap._read_protocol_header = lambda: True  # type: ignore[method-assign]

        def _fake_decode(c=cap, ev_in=entered_wait, ev_out=release_decode, wf=write_frame_before_stop):
            if wf:
                c._latest_frame = np.zeros((4, 4, 3), dtype=np.uint8)
            ev_in.set()
            ev_out.wait(5.0)

        cap._decode_loop = _fake_decode  # type: ignore[method-assign]

        result: list[bool] = []
        t = threading.Thread(target=lambda r=result, c=cap: r.append(c.restart()), daemon=True)
        t.start()
        assert entered_wait.wait(2.0)

        try:
            cap.stop()
        finally:
            release_decode.set()
            t.join(timeout=3.0)
        assert not t.is_alive()
        assert result == [False]
        assert cap.last_error == ""


def test_scrcpy_concurrent_stop_safe_and_preserves_stuck_decoder(monkeypatch):
    """并发 stop() 无 TOCTOU 崩溃、不删 tcp:None，卡住的 decoder 在线程退出前保持保留。"""
    monkeypatch.setattr(
        "lvjiang.core.android.scrcpy_capture.subprocess.run",
        lambda *_a, **_kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    removed_forwards: list[str] = []
    device = SimpleNamespace(
        serial="dev-1",
        _base=lambda: ["adb", "-s", "dev-1"],
        remove_forward=lambda spec: removed_forwards.append(spec),
    )
    cap = AndroidStreamCapture(device=device, max_size=1280)
    cap._video_port = 27183
    sentinel_decoder = object()
    cap._decoder = sentinel_decoder  # type: ignore[assignment]

    join_calls = 0
    join_lock = threading.Lock()
    blocked_in_join = threading.Event()
    first_stop_done = threading.Event()

    class _FakeDecodeThread:
        def __init__(self):
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, timeout: float | None = None) -> None:
            nonlocal join_calls
            with join_lock:
                join_calls += 1
                call_index = join_calls
            if call_index == 1:
                # 线程 A 进入 join() 后等待线程 B 先完成 stop() 并把 _decode_thread 置 None，
                # 线程 A 再返回 join() 复检存活状态，确定性复现原 TOCTOU 空指针路径。
                blocked_in_join.set()
                assert first_stop_done.wait(2.0)

    fake_thread = _FakeDecodeThread()
    cap._decode_thread = fake_thread  # type: ignore[assignment]

    errors: list[BaseException] = []

    def _run_stop(mark_done: bool) -> None:
        try:
            cap.stop()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            if mark_done:
                first_stop_done.set()

    t_other = threading.Thread(target=_run_stop, args=(False,), daemon=True)
    t_other.start()
    assert blocked_in_join.wait(2.0)

    _run_stop(mark_done=True)
    t_other.join(timeout=2.0)
    assert not t_other.is_alive()

    assert errors == []
    assert removed_forwards == ["tcp:27183"]
    assert cap._decode_thread is None
    assert cap._decoder is sentinel_decoder

    # 卡住线程仍存活期间再次调用 stop()，不得误清 _decoder
    cap.stop()
    assert cap._decoder is sentinel_decoder

    # 线程真正退出后再调用 stop()，应正常清空 _decoder
    fake_thread.alive = False
    cap.stop()
    assert cap._decoder is None
