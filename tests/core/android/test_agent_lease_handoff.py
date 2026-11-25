"""热重连租约交接与 lease_busy 恢复不变式回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

from lvjiang.core.android.agent import (
    LEASE_BUSY_ERROR_CODE,
    AgentClient,
    AgentInput,
    AgentTransportError,
)
from lvjiang.core.android.recovery import RecoveryAction
from lvjiang.ui.main.execution_targets import ExecutionTarget, TargetHandle


class _FakeRecoveryCoordinator:
    def __init__(self, on_failure=None):
        self.recovering = False
        self._on_failure = on_failure
        self.failures: list[str] = []

    def succeeded(self) -> None:
        self.recovering = False

    def retry(self, *_args, **_kwargs) -> bool:
        return False

    def after_agent_failure(self, error, what: str, *, uncertain: bool,
                            fallback_supported: bool):
        self.failures.append(str(error))
        if self._on_failure is not None:
            return self._on_failure(error, what, uncertain, fallback_supported)
        return RecoveryAction.RETRY


class _FakeDevice:
    def __init__(self, coordinator: _FakeRecoveryCoordinator):
        self.recovery_coordinator = coordinator
        self.recovery = SimpleNamespace(agent_attempts=1)

    def forward(self, local: str, remote: str) -> bool:
        return True

    def remove_forward(self, local: str) -> None:
        pass


class _SpySocket:
    def __init__(self, counter: dict[str, int]):
        self._counter = counter
        self._closed = False
        self._counter["active"] += 1
        self._counter["max_active"] = max(
            self._counter["max_active"], self._counter["active"])

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._counter["active"] -= 1


def _make_agent_pair(
    name: str,
    counter: dict[str, int],
    *,
    on_failure=None,
    roundtrip_fn=None,
) -> tuple[AgentClient, AgentInput, list[tuple[str, str, dict]]]:
    calls: list[tuple[str, str, dict]] = []
    coord = _FakeRecoveryCoordinator(on_failure=on_failure)
    device = _FakeDevice(coord)
    client = AgentClient(device, client_id="pc-instance-1")  # type: ignore[arg-type]
    client._status = {"ok": True, "protocol": 1, "offline_protocol": 4}
    client._sock = _SpySocket(counter)  # type: ignore[assignment]

    def _default_roundtrip(op: str, timeout=None, **params):
        if client._sock is None:
            raise AgentTransportError("代理未连接")
        calls.append((name, op, dict(params)))
        if roundtrip_fn is not None:
            return roundtrip_fn(op, params)
        return {"ok": True}, b""

    def _forbid_open(_timeout: float) -> bool:
        # 一旦旧连接尝试私自重连，活跃 socket 计数立刻超限暴露问题
        client._sock = _SpySocket(counter)  # type: ignore[assignment]
        return True

    client._roundtrip = _default_roundtrip  # type: ignore[method-assign]
    client._open_socket = _forbid_open  # type: ignore[method-assign]
    ctrl = AgentInput(client)
    return client, ctrl, calls


def test_rebound_handle_delegates_in_flight_tap_without_reviving_old_socket():
    """热重连后唤醒卡在确认态的旧 AgentInput：活跃 socket 始终 <= 1，且 tap 落在 new_agent。"""
    sock_counter = {"active": 0, "max_active": 0}
    dispatched: list[tuple[str, str, dict]] = []

    old_client: AgentClient | None = None
    new_client: AgentClient | None = None
    handle: TargetHandle | None = None
    new_ctrl: AgentInput | None = None

    def _on_first_failure(_err, _what, _uncertain, _fallback):
        # 模拟用户点击「重新连接」：先关旧连接释放 socket，再建新连接并 rebind TargetHandle
        assert old_client is not None and handle is not None
        old_client.close()
        nonlocal new_client, new_ctrl
        new_client, new_ctrl, new_calls = _make_agent_pair(
            "new", sock_counter,
            roundtrip_fn=lambda op, params: (
                dispatched.append(("new", op, params)) or ({"ok": True}, b"")
            ),
        )
        new_ctrl.mark_execution_pending(lambda: True)
        new_target = ExecutionTarget(
            id="dev-1", kind="adb", display_name="phone",
            input_ctrl=new_ctrl, device=new_client.device, agent=new_client,
        )
        handle.rebind(new_target)
        return RecoveryAction.RETRY

    first_attempt = {"done": False}

    def _old_roundtrip(op: str, params: dict):
        if not first_attempt["done"]:
            first_attempt["done"] = True
            raise AgentTransportError("tap: broken pipe", request_sent=True)
        dispatched.append(("old", op, params))
        return {"ok": True}, b""

    old_client, old_ctrl, _old_calls = _make_agent_pair(
        "old", sock_counter, on_failure=_on_first_failure, roundtrip_fn=_old_roundtrip,
    )
    old_target = ExecutionTarget(
        id="dev-1", kind="adb", display_name="phone",
        input_ctrl=old_ctrl, device=old_client.device, agent=old_client,
    )
    handle = TargetHandle(old_target)

    old_ctrl._call("tap", "点击", x=100, y=200)

    # 不变式 1：恢复全过程活跃 socket 数始终 <= 1（旧连接从未私自重连复活）
    assert sock_counter["max_active"] <= 1
    assert old_client._sock is None
    # 不变式 2：恢复后的 control_begin 与 tap 全部落在 new_agent 上，没有一次落回 old
    assert [who for who, _, _ in dispatched] == ["new", "new"]
    assert [op for _, op, _ in dispatched] == ["control_begin", "tap"]
    assert dispatched[0][2]["client_id"] == "pc-instance-1"
    assert new_client is not None and new_client._owns_execution is True


def test_double_rebind_chain_resolves_to_latest_backend_with_old_sockets_closed():
    """连续两次热替换（A -> B -> C）：解析直达链尾 C，无环且 A/B 均已关闭。"""
    sock_counter = {"active": 0, "max_active": 0}
    dispatched: list[tuple[str, str]] = []
    clients: dict[str, AgentClient] = {}
    handle: TargetHandle | None = None

    def _on_failure(_err, _what, _uncertain, _fallback):
        assert handle is not None
        # 第一次换绑 A -> B
        clients["A"].close()
        client_b, ctrl_b, _ = _make_agent_pair("B", sock_counter)
        clients["B"] = client_b
        handle.rebind(ExecutionTarget(
            id="dev-1", kind="adb", display_name="phone",
            input_ctrl=ctrl_b, device=client_b.device, agent=client_b,
        ))
        # 第二次换绑 B -> C
        client_b.close()
        client_c, ctrl_c, _ = _make_agent_pair(
            "C", sock_counter,
            roundtrip_fn=lambda op, _p: (dispatched.append(("C", op)) or ({"ok": True}, b"")),
        )
        clients["C"] = client_c
        handle.rebind(ExecutionTarget(
            id="dev-1", kind="adb", display_name="phone",
            input_ctrl=ctrl_c, device=client_c.device, agent=client_c,
        ))
        return RecoveryAction.RETRY

    client_a, ctrl_a, _ = _make_agent_pair(
        "A", sock_counter,
        on_failure=_on_failure,
        roundtrip_fn=lambda _op, _p: (_ for _ in ()).throw(
            AgentTransportError("disconnect", request_sent=True)),
    )
    clients["A"] = client_a
    handle = TargetHandle(ExecutionTarget(
        id="dev-1", kind="adb", display_name="phone",
        input_ctrl=ctrl_a, device=client_a.device, agent=client_a,
    ))

    ctrl_a._call("tap", "点击", x=10, y=20)

    assert sock_counter["max_active"] <= 1
    assert clients["A"]._sock is None
    assert clients["B"]._sock is None
    assert clients["C"]._sock is not None
    assert dispatched == [("C", "tap")]


def test_lease_busy_on_pending_control_begin_retries_and_succeeds(monkeypatch):
    """ensure_execution_owned 遇 lease_busy 不吞失败、不丢 pending，重试后成功。"""
    monkeypatch.setattr("lvjiang.core.android.agent.time.sleep", lambda _s: None)
    sock_counter = {"active": 0, "max_active": 0}
    attempts = {"control_begin": 0, "tap": 0}

    def _roundtrip(op: str, _params: dict):
        if op == "control_begin":
            attempts["control_begin"] += 1
            if attempts["control_begin"] < 3:
                return {
                    "ok": False,
                    "error": "设备已被另一条 PC 执行连接占用",
                    "retryable": True,
                    "error_code": LEASE_BUSY_ERROR_CODE,
                }, b""
            return {"ok": True}, b""
        attempts["tap"] += 1
        return {"ok": True}, b""

    client, ctrl, _ = _make_agent_pair("new", sock_counter, roundtrip_fn=_roundtrip)
    ctrl.mark_execution_pending(lambda: True)

    ctrl._call("tap", "点击", x=50, y=60)

    assert attempts == {"control_begin": 3, "tap": 1}
    assert client._owns_execution is True
    assert ctrl._execution_pending is False


def test_probe_execution_busy_sends_client_id_for_same_pc_takeover():
    """预检 control_begin 携带 client_id，同机半开残留可被设备端识别并平滑释放。"""
    sock_counter = {"active": 0, "max_active": 0}
    seen: list[tuple[str, dict]] = []

    def _roundtrip(op: str, params: dict):
        seen.append((op, params))
        return {"ok": True}, b""

    client, _ctrl, _ = _make_agent_pair("probe", sock_counter, roundtrip_fn=_roundtrip)

    busy_reason = client.probe_execution_busy(timeout=1.0)

    assert busy_reason == ""
    assert seen[0][0] == "control_begin"
    assert seen[0][1].get("client_id") == "pc-instance-1"
    assert seen[1][0] == "control_end"
    assert SimpleNamespace(active=sock_counter["active"]).active == 1


def test_default_client_id_reuses_stable_instance_id(monkeypatch):
    """未显式指定 client_id 时复用 config/session/instance.id（stable_instance_id）。"""
    monkeypatch.setattr(
        "lvjiang.core.account.identity.stable_instance_id",
        lambda: "inst-from-session-file",
    )
    coord = _FakeRecoveryCoordinator()
    device = _FakeDevice(coord)
    client = AgentClient(device)  # type: ignore[arg-type]
    assert client._client_id == "inst-from-session-file"
