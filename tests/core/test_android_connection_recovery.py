"""代理/ADB 故障恢复不能丢任务、重放不明输入或伪装并发能力。"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lvjiang.core.android.agent import AgentClient, AgentInput, AgentTransportError
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
