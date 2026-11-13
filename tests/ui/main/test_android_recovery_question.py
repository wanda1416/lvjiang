"""设备恢复询问必须非阻塞且只停止对应执行链。"""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

from lvjiang.ui.main.execution_runs import RunState
from lvjiang.ui.main.run_control import RunControlMixin


def test_recovery_question_waits_for_yes_or_no_without_affecting_other_target(qtbot):
    class Host(RunControlMixin, QWidget):
        def append_log(self, text: str) -> None:
            self.log_text.append(text)
    host = Host()
    qtbot.addWidget(host)
    context = SimpleNamespace(task_run_id='test-run', stop_event=threading.Event(), pause_event=None)
    other = SimpleNamespace(stop_event=threading.Event())
    contexts = {'test-target': context, 'other-target': other}
    host._run_manager = SimpleNamespace(run_for_target=contexts.get,
                                       set_state=lambda *args: None)
    host._emit_run_instance_state = lambda *args: None
    host.log_text = SimpleNamespace(append=lambda message: None)
    target = SimpleNamespace(id='test-target', display_name='测试设备', resume_event=threading.Event())
    host._show_connection_recovery_question(target, context, '重试耗尽')
    box = host._connection_recovery_dialogs[target.id]
    assert box.windowModality() == Qt.WindowModality.NonModal
    assert box.isVisible() and not target.resume_event.is_set()
    box.done(QMessageBox.StandardButton.Yes)
    assert target.resume_event.is_set() and not context.stop_event.is_set()
    target.resume_event.clear()
    host._show_connection_recovery_question(target, context, '仍未恢复')
    box = host._connection_recovery_dialogs[target.id]
    states = []
    host._run_manager.set_state = lambda run, state: states.append(state)
    box.done(QMessageBox.StandardButton.No)
    assert context.stop_event.is_set() and target.resume_event.is_set()
    assert states == [RunState.STOPPING]
    assert not other.stop_event.is_set()
    qtbot.waitUntil(lambda: not host._connection_recovery_dialogs)


def test_recheck_does_not_resume_task_and_verified_recovery_preserves_user_pause(qtbot):
    class Host(RunControlMixin, QWidget):
        def append_log(self, text: str) -> None:
            self.log_text.append(text)
    host = Host()
    qtbot.addWidget(host)
    resume = threading.Event()
    pause = threading.Event()  # clear 表示用户主动暂停。
    target = SimpleNamespace(id='test-target', resume_event=resume,
                             input_ctrl=SimpleNamespace(kind='agent'))
    context = SimpleNamespace(target_id=target.id, target_snapshot=target,
                              state=RunState.WAITING_TARGET, pause_event=pause,
                              stop_event=threading.Event(), task_run_id='test-run')
    host._current_run_context = context
    host._run_manager = SimpleNamespace(run_for_target=lambda _id: context, set_state=Mock())
    host._execution_targets = SimpleNamespace(get=lambda _id: target, active_target_id='other-target')
    host.log_text = SimpleNamespace(append=Mock())
    host.statusBar = lambda: SimpleNamespace(showMessage=Mock())
    host._emit_run_instance_state = Mock()
    host._refresh_execution_targets_ui = Mock()
    host._resume_adb()
    assert resume.is_set()
    host._run_manager.set_state.assert_not_called()
    host._on_adb_connection_restored(target.id)
    host._run_manager.set_state.assert_called_once_with(context.task_run_id, RunState.PAUSED)
    assert not pause.is_set()


class _Banner:
    """够用的横幅替身：只关心显隐与文案。"""

    def __init__(self) -> None:
        self.visible = True
        self.text = ""

    def setVisible(self, value: bool) -> None:  # noqa: N802 - Qt 接口
        self.visible = bool(value)

    def isHidden(self) -> bool:  # noqa: N802 - Qt 接口
        return not self.visible


def _idle_host(qtbot, capture):
    """空闲（没有运行实例）的目标宿主，横幅按钮全指向「重新检查」。"""
    class Host(RunControlMixin, QWidget):
        def append_log(self, text: str) -> None:
            self.logs.append(text)

    host = Host()
    qtbot.addWidget(host)
    host.logs = []
    host.statusBar = lambda: SimpleNamespace(showMessage=Mock())
    host._current_run_context = None
    banner = _Banner()
    host._adb_banner = banner
    host._adb_banner_label = SimpleNamespace(setText=lambda text: setattr(banner, "text", text))
    host._adb_banner_btn = Mock()
    binding = SimpleNamespace(capture=capture)
    target = SimpleNamespace(id="idle-target", handle=SimpleNamespace(binding=lambda: binding))
    host._execution_targets = SimpleNamespace(
        get=lambda _tid: target, active_target_id=target.id)
    # 横幅由真实入口设置：连同归属一起记录，否则「收起」找不到该收哪一条。
    host._show_channel_banner(target.id, "⚠ 画面流已中断")
    return host, banner


def _fake_stream_capture(*, restart_ok: bool, reason: str = ""):
    from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture

    capture = object.__new__(AndroidStreamCapture)   # 不跑 __init__：只要能 restart
    capture.restart = lambda: restart_ok
    capture._last_error = reason
    capture._fault_detail = ""
    return capture


def test_idle_recheck_rebuilds_stream_and_clears_banner(qtbot):
    """空闲时点「重新检查」必须真的重建画面流。

    回归：空闲分支只往一个没人等待的 Event 上 set 一次，横幅永远消不掉——用户
    明明已经重连好了，界面还挂着「画面流已中断」，只能重启应用。
    """
    host, banner = _idle_host(qtbot, _fake_stream_capture(restart_ok=True))

    host._resume_adb()

    qtbot.waitUntil(lambda: host._stream_recheck_bridge is None)
    assert not banner.visible
    assert any("重新检查画面流" in line for line in host.logs)


def test_idle_recheck_failure_keeps_banner_with_reason(qtbot):
    """重建失败要保留横幅并写清原因，允许再试——不能假装恢复也不留死提示。"""
    host, banner = _idle_host(
        qtbot, _fake_stream_capture(restart_ok=False, reason="协议头读取超时"))

    host._resume_adb()

    qtbot.waitUntil(lambda: host._stream_recheck_bridge is None)
    assert banner.visible
    assert "协议头读取超时" in banner.text


# ─── 横幅归属：断开与切换目标都不能留下点不掉的提示 ───────────


class _Target:
    def __init__(self, target_id: str, name: str) -> None:
        self.id = target_id
        self.kind = "adb"
        self.display_name = name
        self.status = "connected"
        self.capture = object()
        self.input_ctrl = object()
        self.agent = object()
        self.capture_method = "scrcpy"
        self.serial = name
        self.width = 2800
        self.height = 1260

    @property
    def ready(self) -> bool:
        return self.status == "connected"


class _Registry:
    """够用的目标注册表替身：只实现横幅与断开路径用到的方法。"""

    def __init__(self, *targets: _Target) -> None:
        self._items = {t.id: t for t in targets}
        self.active_target_id = targets[0].id if targets else None

    def all(self):
        return list(self._items.values())

    def get(self, target_id):
        return self._items.get(target_id)

    def select(self, target_id):
        target = self._items[target_id]
        self.active_target_id = target_id
        return target

    def remove(self, target_id):
        self.active_target_id = None if self.active_target_id == target_id \
            else self.active_target_id
        return self._items.pop(target_id, None)


def _banner_host(qtbot, registry):
    """带横幅的宿主：横幅显隐由 _Banner 记录，其余协作者都是空实现。"""
    from lvjiang.ui.main.run_control import RunControlMixin
    from lvjiang.ui.main.window_ops import WindowOpsMixin

    class Host(RunControlMixin, WindowOpsMixin, QWidget):
        def append_log(self, text: str) -> None:
            self.logs.append(text)

    host = Host()
    qtbot.addWidget(host)
    host.logs = []
    host.statusBar = lambda: SimpleNamespace(showMessage=Mock())
    host._current_run_context = None
    host._execution_targets = registry
    host._run_manager = SimpleNamespace(run_for_target=lambda _tid: None)
    banner = _Banner()
    host._adb_banner = banner
    host._adb_banner_label = SimpleNamespace(
        setText=lambda text: setattr(banner, "text", text))
    host._adb_banner_btn = Mock()
    for name in ("_abort_recording_for_target", "_dispose_execution_target",
                 "_refresh_execution_targets_ui", "_restore_active_target_view",
                 "_sync_active_target_compat", "_refresh_active_target_ui",
                 "_refresh_run_button", "_ensure_target_plan"):
        setattr(host, name, lambda *a, **k: None)
    return host, banner


def test_disconnect_drops_the_stale_channel_banner(qtbot, monkeypatch):
    """主动断开空闲目标后横幅必须消失。

    回归：断开是「点掉横幅」之外唯一的主动选择，而它从不碰横幅；目标被移除后
    active_target_id 变了，之后再按 id 收也被守卫挡掉，只能等重新连上。
    """
    monkeypatch.setattr(
        "lvjiang.core.app_controller.remove_connected_target", lambda _tid: None)
    target = _Target("t1", "设备A")
    host, banner = _banner_host(qtbot, _Registry(target))

    host._show_channel_banner(target.id, "⚠ 画面流已中断")
    assert banner.visible

    host._disconnect_execution_target(target.id)

    assert not banner.visible
    assert target.id not in host._channel_banner_texts()


def test_banner_follows_the_viewed_target(qtbot):
    """切到别的目标不能继续挂着上一台的提示，切回来要能恢复。"""
    left = _Target("t1", "设备A")
    right = _Target("t2", "设备B")
    registry = _Registry(left, right)
    host, banner = _banner_host(qtbot, registry)

    host._show_channel_banner(left.id, "⚠ A 的画面流已中断")
    assert banner.visible and "A" in banner.text

    registry.select(right.id)
    host._sync_channel_banner()
    assert not banner.visible

    registry.select(left.id)
    host._sync_channel_banner()
    assert banner.visible and "A" in banner.text

    host._hide_channel_banner(left.id)
    assert not banner.visible
    registry.select(right.id)
    host._sync_channel_banner()
    assert not banner.visible, "已收起的提示不能因为切目标又冒出来"
