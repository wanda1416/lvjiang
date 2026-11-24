"""批次 C 回归测试：WAITING_TARGET 投影与订阅方统一、通道横幅归属收口与恢复弹窗静默关闭。"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton, QWidget

from lvjiang.ui.batch.batch_execution import _BatchExecutionMixin
from lvjiang.ui.main.execution_runs import RunState
from lvjiang.ui.main.execution_targets import ExecutionTarget, ExecutionTargetRegistry
from lvjiang.ui.main.run_android_recovery import _AndroidRecoveryMixin
from lvjiang.ui.main.run_control import STATE_WAITING_TARGET, RunControlMixin
from lvjiang.ui.main.tray_ops import TrayOpsMixin


def test_waiting_target_treated_as_active_in_batch_and_tray(qapp):
    """WAITING_TARGET 在批量页与托盘均视为活跃运行态（禁用开始、允许停止、暂停不可用）。"""
    # 1. BatchTab
    config_enabled: list[bool] = []
    fake_host = SimpleNamespace(
        _user_config=SimpleNamespace(hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11")),
        _running=True,
        _run_state=STATE_WAITING_TARGET,
        _current_run_context=SimpleNamespace(name="批量", metadata={"execution_scope": "batch"}),
        _backend_ready=lambda: True,
        _plan_allows_backend=lambda: True,
        start_denied_label=lambda: "",
    )
    batch = SimpleNamespace(
        _host=fake_host,
        _running=False,
        _btn_run=QPushButton(),
        _btn_pause_resume=QPushButton(),
        _set_config_enabled=lambda en: config_enabled.append(en),
    )
    batch._refresh_run_button = lambda state: _BatchExecutionMixin._refresh_run_button(batch, state)
    _BatchExecutionMixin._on_automation_state(batch, STATE_WAITING_TARGET)

    assert batch._running is True
    assert config_enabled == [False]
    assert "停止" in batch._btn_run.text()
    assert batch._btn_run.isEnabled() is True
    assert batch._btn_pause_resume.isEnabled() is False

    # 2. TrayOpsMixin
    tooltips: list[str] = []
    start_en: list[bool] = []
    pause_en: list[bool] = []
    stop_en: list[bool] = []
    tray = SimpleNamespace(
        _tray_icon=SimpleNamespace(setIcon=lambda _i: None, setToolTip=lambda t: tooltips.append(t)),
        _tray_state=STATE_WAITING_TARGET,
        _tray_run_count=1,
        _run_manager=SimpleNamespace(summary_state=lambda: RunState.WAITING_TARGET),
        windowTitle=lambda: "律匠",
        _tray_action_start=SimpleNamespace(setEnabled=lambda v: start_en.append(v)),
        _tray_action_pause=SimpleNamespace(
            setEnabled=lambda v: pause_en.append(v), setText=lambda _t: None),
        _tray_action_stop=SimpleNamespace(
            setEnabled=lambda v: stop_en.append(v), setText=lambda _t: None),
    )
    tray._aggregate_tray_state = lambda cur: TrayOpsMixin._aggregate_tray_state(tray, cur)
    tray._tray_icon_state = lambda cur: TrayOpsMixin._tray_icon_state(tray, cur)
    TrayOpsMixin._apply_tray_state(tray)

    assert start_en == [False]
    assert pause_en == [False]
    assert stop_en == [True]
    assert "另有" not in tooltips[-1]


def test_sync_projected_run_state_updates_only_active_run_context():
    """仅当前查看目标恢复时回写 _run_state 与按钮；后台目标恢复绝不污染当前查看目标。"""
    refreshes = 0
    ctx_a = SimpleNamespace(task_run_id="run-a", target_id="adb:A")
    ctx_b = SimpleNamespace(task_run_id="run-b", target_id="adb:B")

    host = SimpleNamespace(
        _current_run_context=ctx_b,
        _run_state="idle",
    )

    def _refresh():
        nonlocal refreshes
        refreshes += 1

    host._refresh_run_button = _refresh

    # 后台目标 A 恢复：不得改写当前查看目标 B 的 _run_state
    RunControlMixin._sync_projected_run_state(host, ctx_a, "running")
    assert host._run_state == "idle"
    assert refreshes == 0

    # 当前查看目标 B 恢复：立即回写 _run_state 并刷新按钮
    RunControlMixin._sync_projected_run_state(host, ctx_b, "running")
    assert host._run_state == "running"
    assert refreshes == 1


def test_channel_banner_single_restore_background_register_and_stop_cleanup(qapp):
    """单目标断线恢复正确隐藏红条；后台断线切回可见横幅；停止/结束不残留且不误伤他目标。"""
    banner_visible = [False]
    banner_text = [""]
    status_msgs: list[str] = []

    class _Host(RunControlMixin):
        def __init__(self):
            self._execution_targets = ExecutionTargetRegistry()
            self._runs: dict[str, SimpleNamespace] = {}
            self._run_manager = SimpleNamespace(
                run_for_target=lambda tid: self._runs.get(tid),
                set_state=lambda tid_or_id, st: self._set_state(tid_or_id, st),
                finish=lambda *_a: None,
            )
            self._current_run_context = None
            self._run_state = "idle"
            self._stop_requested = False
            self._ui_helper = None
            self._pause_event = None
            self._current_worker = None
            self._running_target_id = None
            self._running_target_snapshot = None
            self._adb_banner_target_id = None
            self._stream_recheck_bridge = None
            self._adb_banner = SimpleNamespace(setVisible=lambda v: banner_visible.__setitem__(0, v))
            self._adb_banner_label = SimpleNamespace(setText=lambda s: banner_text.__setitem__(0, s))
            self._adb_banner_btn = None
            self.logs: list[str] = []

        def _set_state(self, run_id, st):
            for rc in self._runs.values():
                if rc.task_run_id == run_id:
                    rc.state = st

        def append_log(self, msg: str): self.logs.append(msg)
        def statusBar(self): return SimpleNamespace(showMessage=lambda m: status_msgs.append(m))
        def _refresh_execution_targets_ui(self): pass
        def _emit_run_instance_state(self, *_a): pass
        def _emit_concurrency_changed(self): pass
        def _refresh_run_button(self): pass
        def _refresh_pause_button(self): pass
        def _set_context_controls_locked(self, *_a): pass
        def _show_connection_recovery_question(self, *_a, **_kw): pass

    host = _Host()
    ta = ExecutionTarget(id="adb:A", kind="adb", display_name="A", capture=object(), input_ctrl=object())
    tb = ExecutionTarget(id="adb:B", kind="adb", display_name="B", capture=object(), input_ctrl=object())
    host._execution_targets.put(ta)
    host._execution_targets.put(tb)
    host._execution_targets.select("adb:A")

    run_a = SimpleNamespace(
        task_run_id="rA", target_id="adb:A", state=RunState.RUNNING,
        stop_event=threading.Event(), pause_event=threading.Event(),
        ui_helper=None, engine=SimpleNamespace(run_finished_callbacks=()),
        metadata={},
    )
    run_a.pause_event.set()
    host._runs["adb:A"] = run_a
    host._current_run_context = run_a
    host._run_state = "running"

    # 1. 单目标断线 → 恢复：横幅先显后隐，_run_state 回到 running
    host._on_adb_connection_lost("adb:A", "disconnected")
    assert banner_visible[0] is True
    assert host._adb_banner_target_id == "adb:A"
    assert host._run_state == STATE_WAITING_TARGET

    host._on_adb_connection_restored("adb:A")
    assert banner_visible[0] is False
    assert host._adb_banner_target_id is None
    assert host._run_state == "running"

    # 2. 切到 B 查看时，后台 A 断线：不覆盖 B 的 statusBar，但切回 A 时横幅立现
    host._execution_targets.select("adb:B")
    status_msgs.clear()
    host._on_adb_connection_lost("adb:A", "bg-lost")
    assert status_msgs == []
    assert "adb:A" in host._channel_banner_texts()
    host._execution_targets.select("adb:A")
    host._sync_channel_banner()
    assert banner_visible[0] is True

    # 3. 给 B 也挂一条横幅；结束后台 A 只清 A 的横幅，不误伤 B，切回 A 也不复活
    host._show_channel_banner("adb:B", "B 画面流中断")
    host._execution_targets.select("adb:B")
    host._sync_channel_banner()
    host._current_run_context = None
    host._end_automation("task-A", run_context=run_a)
    assert "adb:A" not in host._channel_banner_texts()
    assert "adb:B" in host._channel_banner_texts()
    assert banner_visible[0] is True  # B 的横幅未受影响
    host._execution_targets.select("adb:A")
    host._sync_channel_banner()
    assert banner_visible[0] is False  # A 横幅不再复活


def test_recovery_dialog_dismissed_silently_on_restore(qapp):
    """恢复成功后静默关闭恢复弹窗：不置位 stop_event、不打印误导等待日志。"""
    class _Host(QWidget, _AndroidRecoveryMixin):
        def __init__(self):
            super().__init__()
            self._execution_targets = ExecutionTargetRegistry()
            self._runs: dict[str, SimpleNamespace] = {}
            self._run_manager = SimpleNamespace(
                run_for_target=lambda tid: self._runs.get(tid),
                set_state=lambda *_a: None,
            )
            self._adb_banner_target_id = None
            self._stream_recheck_bridge = None
            self.logs: list[str] = []

        def append_log(self, msg: str): self.logs.append(msg)
        def _emit_run_instance_state(self, *_a): pass
        def _refresh_execution_targets_ui(self): pass

    host = _Host()
    target = ExecutionTarget(id="adb:A", kind="adb", display_name="A", capture=object(), input_ctrl=object())
    host._execution_targets.put(target)
    pause_ev = threading.Event()
    pause_ev.set()
    run_a = SimpleNamespace(
        task_run_id="rA", target_id="adb:A", state=RunState.WAITING_TARGET,
        stop_event=threading.Event(), pause_event=pause_ev,
    )
    host._runs["adb:A"] = run_a

    host._show_connection_recovery_question(target, run_a, "断开")
    assert "adb:A" in host._connection_recovery_dialogs

    host._on_adb_connection_restored("adb:A")
    qapp.processEvents()

    assert "adb:A" not in host._connection_recovery_dialogs
    assert not run_a.stop_event.is_set()
    assert not any("仍在等待通道恢复" in line for line in host.logs)
    host.close()


def test_switching_android_to_screencap_hides_channel_banner(qapp, monkeypatch):
    """Android 目标空闲断流后通过右键切回单帧 screencap，必须收起残留的断流横幅。"""
    from lvjiang.ui.main.adb_device_ops import _AdbDeviceOpsMixin

    banner_visible = [False]
    banner_text = [""]
    monkeypatch.setattr(
        "lvjiang.core.android.create_capture_backend",
        lambda **_kw: SimpleNamespace(start=lambda: True, stop=lambda: None),
    )

    class _Host(_AdbDeviceOpsMixin, _AndroidRecoveryMixin):
        def __init__(self):
            self._execution_targets = ExecutionTargetRegistry()
            self._run_manager = SimpleNamespace(run_for_target=lambda _tid: None)
            self._adb_banner_target_id = None
            self._adb_banner = SimpleNamespace(setVisible=lambda v: banner_visible.__setitem__(0, v))
            self._adb_banner_label = SimpleNamespace(setText=lambda s: banner_text.__setitem__(0, s))
            self._adb_banner_btn = None
            self._preview_enabled = False
            self.logs: list[str] = []

        def append_log(self, msg: str): self.logs.append(msg)
        def _target_has_active_run(self, _tid: str) -> bool: return False
        def _abort_recording_for_target(self, *_a): pass
        def _rebind_target_resources(self, _target): pass
        def _sync_active_target_compat(self): pass
        def _apply_rec_state(self): pass
        def _refresh_execution_targets_ui(self): pass

    host = _Host()
    target = ExecutionTarget(
        id="adb:A", kind="adb", display_name="A",
        capture=SimpleNamespace(stop=lambda: None),
        input_ctrl=object(), device=object(),
        capture_method="scrcpy", streaming=True,
    )
    host._execution_targets.put(target)
    host._execution_targets.select(target.id)
    host._show_channel_banner(target.id, "⚠ 画面流已中断")
    assert banner_visible[0] is True
    assert target.id in host._channel_banner_texts()

    host._set_android_target_streaming(target.id, False)

    assert banner_visible[0] is False
    assert host._adb_banner_target_id is None
    assert target.id not in host._channel_banner_texts()
