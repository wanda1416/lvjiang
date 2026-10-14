"""同 id 重连换绑：旧设备的恢复回调要跟新 bridge 走，且不得替用户放行确认。

断线时那条在飞命令一直持有断线前解析出来的旧 `AdbDevice`（输入对象在一次
调用里只解析一次），重连只把它的 transport 改指了新连接。旧目标随即被
`_dispose_execution_target` 释放（含旧 bridge `deleteLater`），如果旧设备的
`on_connection_restored` 还指着旧 bridge，命令重试成功时就会打到已销毁的 Qt
对象上，把一次正常恢复变成任务级异常——批量会记该任务失败并继续下一个。

第二个契约是「已发出、结果不确定」的动作必须由用户点「继续」确认后才重放：
重连修好通道不等于用户确认，也不等于任务已经恢复。
"""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lvjiang.core.android.device import AdbDevice
from lvjiang.core.config import InputSimConfig
from lvjiang.ui.main.execution_runs import RunState
from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.window_ops import (
    WindowOpsMixin,
    _AdbConnSignalBridge,
    _pending_uncertain,
)

STABLE_ID = 'stable-device-id'
TARGET_ID = android_target_id(STABLE_ID)
NEW_SERIAL = '192.168.0.100:5555'


class _Backend:
    """可区分身份、能记录是否被停掉的假后端。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.kind = name
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append(self, text: str) -> None:
        self.lines.append(text)


class _Host(RunControlMixin, WindowOpsMixin):
    """只提供被测路径真正用到的协作者；宿主槽用真实实现，便于断言行为。"""

    def __init__(self, registry: ExecutionTargetRegistry, run_manager) -> None:
        self._execution_targets = registry
        self._run_manager = run_manager
        self.log_text = _Log()
        self._user_config = SimpleNamespace(
            input_sim=InputSimConfig(),
            hotkeys=SimpleNamespace(start='F9', stop='F10'))
        self._preview_enabled = False
        self._screen_recorder = None
        self._record_target_id = None
        self._adb_banner = None
        self.lost_calls: list[tuple] = []
        self.restored_calls: list[str] = []
        self._emit_run_instance_state = Mock()

    def _on_adb_connection_lost(self, target_id, error_msg, device=None):
        self.lost_calls.append((target_id, error_msg, device))
        super()._on_adb_connection_lost(target_id, error_msg, device)

    def _on_adb_connection_restored(self, target_id):
        self.restored_calls.append(target_id)
        super()._on_adb_connection_restored(target_id)

    def statusBar(self):  # noqa: N802 - 模仿 QMainWindow 接口
        return SimpleNamespace(showMessage=Mock())

    def _hotkey_status(self, message: str, *bindings) -> str:
        return message

    def _sync_active_target_compat(self) -> None:
        pass

    def _ensure_target_plan(self) -> None:
        pass

    def _refresh_execution_targets_ui(self) -> None:
        pass

    def _refresh_active_target_ui(self) -> None:
        pass

    def _refresh_run_button(self) -> None:
        pass

    def _capture_preview(self) -> None:
        pass


class _Case:
    """一次「同目标重连」的完整场景。"""

    def __init__(self, monkeypatch) -> None:
        self.resume_event = threading.Event()
        self.old_bridge = _AdbConnSignalBridge(TARGET_ID)
        self.old_device = AdbDevice(serial='old-host:5555', adb_path='test-adb')
        self.old_capture = _Backend('scrcpy')
        self.new_device = AdbDevice(serial=NEW_SERIAL, adb_path='test-adb')
        self.new_capture = _Backend('screencap')
        monkeypatch.setattr(
            self.old_device, 'shell', Mock(return_value=STABLE_ID))
        monkeypatch.setattr(
            self.new_device, 'shell', Mock(return_value=STABLE_ID))
        monkeypatch.setattr(
            'lvjiang.core.app_controller.record_connected_android',
            lambda *args, **kwargs: {})

        self.run_context = SimpleNamespace(
            task_run_id='run-1', target_id=TARGET_ID,
            state=RunState.WAITING_TARGET, pause_event=None,
            stop_event=threading.Event())
        self.run_manager = SimpleNamespace(
            run_for_target=lambda tid: (
                self.run_context if tid == TARGET_ID else None),
            set_state=Mock())

        self.registry = ExecutionTargetRegistry()
        self.old_device.on_connection_lost = (
            lambda message, dev=self.old_device:
            self.old_bridge.notify_lost(message, dev))
        self.old_device.on_connection_restored = self.old_bridge.notify_restored
        self.old_target = ExecutionTarget(
            id=TARGET_ID, kind='adb', display_name='旧连接',
            capture=self.old_capture, input_ctrl=_Backend('agent'),
            device=self.old_device, serial='old-host:5555',
            capture_method='scrcpy', streaming=True,
            resume_event=self.resume_event, connection_bridge=self.old_bridge)
        self.registry.put(self.old_target)
        # 运行实例冻结快照（handle 在这里创建，重连要复用它）
        self.run_snapshot = self.old_target.snapshot()

        self.host = _Host(self.registry, self.run_manager)

    def mark_waiting(self, kind: str, *, active: bool = True) -> None:
        """让旧设备看起来正阻塞在人工确认上（kind 决定是哪一类等待）。"""
        coordinator = self.old_device.recovery_coordinator
        coordinator.recovering = True
        coordinator.waiting = active
        coordinator.waiting_kind = kind

    def connect(self) -> None:
        self.host._on_connect_done(
            {'serial': NEW_SERIAL}, self.new_device, self.new_capture,
            'screencap', 2800, 1260, None, update_candidate_ui=False)


@pytest.fixture
def reconnect_case(qtbot, monkeypatch):
    # qtbot 在这里只为提供 QApplication：信号桥是 QObject。
    del qtbot
    return _Case(monkeypatch)


def test_pending_uncertain_requires_an_active_wait() -> None:
    """只有「此刻有人等在不确定确认上」才算，recovering 单独为真不算。"""
    coordinator = SimpleNamespace(
        waiting=False, recovering=True, waiting_kind='uncertain_action')
    device = SimpleNamespace(recovery_coordinator=coordinator)
    # recovering 只说明传输在重建（透明重连也置位），不代表有人在等确认
    assert not _pending_uncertain(device)
    coordinator.waiting = True
    assert _pending_uncertain(device)
    coordinator.waiting_kind = 'connection'
    assert not _pending_uncertain(device)


def test_reconnect_repoints_old_device_recovery_callbacks(reconnect_case) -> None:
    """重连后旧设备的恢复回调必须指向新 bridge，旧 bridge 可以安全释放。"""
    case = reconnect_case
    case.mark_waiting('connection')

    case.connect()

    new_target = case.registry.get(TARGET_ID)
    assert new_target is not case.old_target
    assert new_target.device is case.new_device
    assert new_target.handle is case.old_target.handle, '运行快照的 handle 必须被复用'
    assert new_target.connection_bridge is not case.old_bridge
    assert case.old_capture.stopped, '旧目标的后端应被释放'
    assert case.old_device.serial == NEW_SERIAL, '在飞命令要重试在新 transport 上'

    assert case.old_device.on_connection_restored != case.old_bridge.notify_restored
    # 旧设备回调仍要能正常通知宿主（旧 bridge 已经 deleteLater）
    case.old_device.on_connection_restored()
    assert case.host.restored_calls == [TARGET_ID]
    assert new_target.status == 'connected'


def test_replaced_device_notification_keeps_target_ready(reconnect_case) -> None:
    """被换掉的旧设备再断线不能把目标标成离线，否则 ready 门禁会误挡启动。"""
    case = reconnect_case
    case.connect()
    new_target = case.registry.get(TARGET_ID)
    assert new_target is not None
    case.run_manager.run_for_target = lambda _tid: None  # 只看目标行，不弹恢复询问

    new_target.status = 'connected'
    case.old_device.on_connection_lost('旧连接又断了')

    assert case.host.lost_calls[-1][2] is case.old_device, '通知必须带上是哪台设备'
    assert new_target.status == 'connected', '旧设备的断线不属于当前连接'
    assert new_target.ready, 'ready 被误清会让开始与批量启动都被挡下'

    # 当前连接自己断线时照旧反映到目标行
    new_target.device.on_connection_lost('当前连接断了')
    assert new_target.status == 'offline'


def test_reconnect_keeps_uncertain_action_waiting_for_user(reconnect_case) -> None:
    """结果不确定的动作不能被重连放行，也不得提前宣告任务已恢复。"""
    case = reconnect_case
    case.mark_waiting('uncertain_action')

    case.connect()

    assert not case.resume_event.is_set(), '必须等用户点「继续」再重放当前动作'
    case.run_manager.set_state.assert_not_called()
    assert case.run_context.state == RunState.WAITING_TARGET


def test_reconnect_releases_connection_wait(reconnect_case) -> None:
    """等的是通道本身时，重连成功后照旧唤醒该执行链并复位状态。"""
    case = reconnect_case
    case.mark_waiting('connection')

    case.connect()

    assert case.resume_event.is_set()
    case.run_manager.set_state.assert_called_once_with(
        case.run_context.task_run_id, RunState.RUNNING)
    case.host._emit_run_instance_state.assert_called_once_with(
        case.run_context, RunState.RUNNING.value)
