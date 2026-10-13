"""代理/ADB 故障恢复不能丢任务、重放不明输入或伪装并发能力。"""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lvjiang.core.android.agent import (
    AgentCapture,
    AgentClient,
    AgentInput,
    AgentOpError,
    AgentTransportError,
)
from lvjiang.core.android.device import AdbConnectionError, AdbDevice
from lvjiang.core.config.models import AndroidRecoveryConfig


def setup_input(monkeypatch):
    device = AdbDevice(serial='test-device', adb_path='test-adb')
    device.recovery = AndroidRecoveryConfig(retry_interval=0)
    client = AgentClient(device)
    client.recovery = device.recovery
    backend = AgentInput(client)
    shell = Mock(return_value='')
    monkeypatch.setattr(device, 'shell', shell)
    return device, client, backend, shell


def test_agent_three_failures_then_adb_fallback_keeps_following_inputs_running(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    agent = Mock(return_value=False)
    adb = Mock(side_effect=[False, False, True])
    monkeypatch.setattr(client, '_reconnect', agent)
    monkeypatch.setattr(device, '_reconnect_once', adb)
    backend._call('tap', '点击', x=12, y=24)
    backend._call('tap', '点击', x=30, y=40)
    assert agent.call_count == 3 and adb.call_count == 3
    assert shell.call_count == 2
    assert backend._using_adb and backend.kind == 'adb'
    assert device.recovery.allow_adb_fallback


def test_exhausted_retries_wait_for_confirmation_and_repeat_connection_checks(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    agent = Mock(side_effect=[False, False, False, True])
    adb = Mock(return_value=False)
    monkeypatch.setattr(client, '_reconnect', agent)
    monkeypatch.setattr(device, '_reconnect_once', adb)
    def roundtrip(*args, **kwargs):
        if agent.call_count < 4:
            raise AgentTransportError('代理未连接')
        return {'ok': True}, b''
    monkeypatch.setattr(client, '_roundtrip', roundtrip)
    prompts = []
    def confirm(message):
        assert agent.call_count == 3 and adb.call_count == 3
        prompts.append(message)
        device.resume_event.set()
    device.on_connection_lost = confirm
    backend._call('tap', '点击', x=12, y=24)
    assert len(prompts) == 1 and agent.call_count == 4
    shell.assert_not_called()


def test_unconfirmed_input_is_not_automatically_replayed(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    sent = AgentTransportError('响应丢失', request_sent=True)
    call = Mock(side_effect=[sent, ({'ok': True}, b'')])
    monkeypatch.setattr(client, 'call', call)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    def confirm(message):
        assert call.call_count == 1 and '可能已经执行' in message
        shell.assert_not_called()
        device.resume_event.set()
    device.on_connection_lost = confirm
    backend._call('tap', '点击', x=12, y=24)
    assert call.call_count == 2
    shell.assert_not_called()


def test_agent_only_gesture_waits_and_stop_breaks_wait_instead_of_downgrading(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    monkeypatch.setattr(client, '_reconnect', lambda: False)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    stopped = False
    def stop(message):
        nonlocal stopped
        assert '恢复手机代理' in message
        stopped = True
        device.resume_event.set()
    device.on_connection_lost = stop
    device.stop_check = lambda: stopped
    with pytest.raises(AdbConnectionError, match='用户停止'):
        backend._call('gesture', '时间线', strokes=[])
    shell.assert_not_called()
    assert not backend._using_adb


def test_disabled_fallback_waits_even_when_adb_is_available(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    device.recovery.allow_adb_fallback = False
    monkeypatch.setattr(client, '_reconnect', lambda: False)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    stopped = False
    def stop(message):
        nonlocal stopped
        stopped = True
        device.resume_event.set()
    device.on_connection_lost = stop
    device.stop_check = lambda: stopped
    with pytest.raises(AdbConnectionError, match='用户停止'):
        backend._call('tap', '点击', x=12, y=24)
    shell.assert_not_called()


def test_batch_scripts_keep_runtime_fallback_until_next_execution_chain(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    policy = AndroidRecoveryConfig(retry_interval=0)
    backend.configure_recovery(policy)
    monkeypatch.setattr(client, '_reconnect', lambda: False)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    backend._call('tap', '点击', x=1, y=2)
    backend.begin_execution()
    assert backend._using_adb and client.recovery is policy
    # 新一轮执行才使用新的设置并重新优先尝试代理。
    new = AndroidRecoveryConfig(allow_adb_fallback=False, agent_attempts=2)
    backend.configure_recovery(new)
    assert not backend._using_adb and device.recovery is new
    assert client.recovery.agent_attempts == 2


def test_adb_reconnect_restores_only_requested_transport_without_killing_server(monkeypatch):
    device = AdbDevice(serial='test-host:5555', adb_path='test-adb')
    run = Mock(side_effect=[SimpleNamespace(returncode=1, stdout=''),
                            SimpleNamespace(returncode=0, stdout=''),
                            SimpleNamespace(returncode=0, stdout='connected'),
                            SimpleNamespace(returncode=0, stdout='lvjiang-ready\n')])
    monkeypatch.setattr('lvjiang.core.android.device.subprocess.run', run)
    assert device._reconnect_once()
    commands = [call.args[0] for call in run.call_args_list]
    assert commands == [
        ['test-adb', '-s', 'test-host:5555', 'shell', 'echo', 'lvjiang-ready'],
        ['test-adb', 'start-server'],
        ['test-adb', 'connect', 'test-host:5555'],
        ['test-adb', '-s', 'test-host:5555', 'shell', 'echo', 'lvjiang-ready'],
    ]


@pytest.mark.parametrize('retry_safe', [True, False], ids=['read', 'input'])
def test_adb_timeout_replays_only_safe_reads_without_confirmation(monkeypatch, retry_safe):
    device = AdbDevice(serial='test-device', adb_path='test-adb')
    device.recovery.retry_interval = 0
    run = Mock(side_effect=[subprocess.TimeoutExpired('command', 1),
                           SimpleNamespace(returncode=0, stdout='ok', stderr='')])
    monkeypatch.setattr('lvjiang.core.android.device.subprocess.run', run)
    restored = Mock()
    device.on_connection_restored = restored
    def probe():
        restored.assert_not_called()
        return True
    monkeypatch.setattr(device, '_reconnect_once', probe)
    prompts = []
    def confirm(message):
        assert not retry_safe
        assert run.call_count == 1 and '可能已经执行' in message
        restored.assert_not_called()
        prompts.append(message)
        device.resume_event.set()
    device.on_connection_lost = confirm
    assert device.shell('test-operation', retry_safe=retry_safe) == 'ok'
    assert len(prompts) == (0 if retry_safe else 1)
    restored.assert_called_once()


def test_agent_capture_waits_for_recovery_without_empty_frame_or_capture_fallback(monkeypatch):
    device, client, _, shell = setup_input(monkeypatch)
    call = Mock(side_effect=[AgentTransportError('断线', request_sent=True),
                            ({'ok': True, 'fmt': 'rgba', 'w': 1, 'h': 1}, bytes([1, 2, 3, 255]))])
    monkeypatch.setattr(client, 'call', call)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    def confirm(message):
        assert '代理截图' in message
        assert call.call_count == 1
        shell.assert_not_called()
        device.resume_event.set()
    device.on_connection_lost = confirm
    frame = AgentCapture(client).capture()
    assert frame is not None and frame[0, 0].tolist() == [3, 2, 1]
    assert call.call_count == 2
    shell.assert_not_called()


def test_agent_long_press_can_fall_back_to_same_duration_adb_gesture(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    monkeypatch.setattr(client, '_reconnect', lambda: False)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    backend._call('long_press', '长按', x=12, y=24, duration_ms=750)
    assert backend._using_adb
    assert shell.call_args.args == ('input', 'swipe', '12', '24', '12', '24', '750')


def test_inactive_target_reports_failure_instead_of_waiting_forever(monkeypatch):
    device = AdbDevice(serial='test-device', adb_path='test-adb')
    device.on_connection_lost = Mock()
    device.can_wait_for_recovery = lambda: False
    with pytest.raises(AdbConnectionError, match='当前没有运行任务'):
        device.wait_for_connection_recovery(OSError('offline'), '读取预览')
    device.on_connection_lost.assert_called_once()


def test_read_retry_budget_exhausts_even_when_transport_probe_succeeds(monkeypatch):
    device = AdbDevice(serial='test-device', adb_path='test-adb')
    device.recovery.retry_interval = 0
    run = Mock(side_effect=[subprocess.TimeoutExpired('screencap', 1)] * 3 + [
        SimpleNamespace(returncode=0, stdout=b'frame', stderr=b'')])
    monkeypatch.setattr('lvjiang.core.android.device.subprocess.run', run)
    monkeypatch.setattr(device, '_reconnect_once', lambda: True)
    def confirm(message):
        assert run.call_count == 3 and '读取重试耗尽' in message
        device.resume_event.set()
    device.on_connection_lost = confirm
    assert device.shell_bytes('exec-out', 'screencap', '-p', retry_safe=True) == b'frame'
    assert run.call_count == 4


def test_execution_ownership_denial_does_not_bypass_agent_via_adb(monkeypatch):
    device, client, backend, shell = setup_input(monkeypatch)
    monkeypatch.setattr(client, '_reconnect', Mock(side_effect=AgentOpError('手机离线任务正在执行')))
    adb = Mock(return_value=True)
    monkeypatch.setattr(device, '_reconnect_once', adb)
    with pytest.raises(AgentOpError, match='离线任务'):
        backend._call('tap', '点击', x=12, y=24)
    adb.assert_not_called()
    shell.assert_not_called()
    assert not backend._using_adb
