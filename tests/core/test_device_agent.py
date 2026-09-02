"""设备端代理通道（core/android/agent.py）PC 侧测试

没有真机：起一个本地 TCP 假服务端按同一线协议应答，替代 `adb forward` 之后的那一段；
AdbDevice 换成记录 forward/remove_forward 调用的桩。覆盖：
- 帧编解码（长度前缀 / JSON 头 / 二进制负载）
- 握手：协议版本校验、连不上/假连接（连上即关）→ connect() 返回 False 且撤 forward
- AgentCapture：RGBA → BGR、PNG 解码、节流 retryable 重试、尺寸缓存
- AgentInput：tap / swipe / hold_move / key 分派与参数换算，设备端失败向工作流传播
- 传输断开后自动重连一次
- 工厂：有代理用 AgentInput/AgentCapture，无代理退回 Adb*
"""
from __future__ import annotations

import json
import socket
import socketserver
import struct
import threading

import numpy as np
import pytest

from lvjiang.core.android import agent as agent_mod
from lvjiang.core.android.agent import (
    PROTOCOL_VERSION,
    AgentCapture,
    AgentClient,
    AgentInput,
    AgentOpError,
    AgentTransportError,
    decode_frame,
    encode_request,
    read_response,
)
from lvjiang.core.config import InputSimConfig

_FRAME = struct.Struct(">I")


# ─── 假服务端 ──────────────────────────────────────────────

class _FakeAgent:
    """按线协议应答的本地 TCP 服务；handler(req: dict) -> (header: dict, payload: bytes)"""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[dict] = []
        self.connections = 0
        outer = self

        class H(socketserver.BaseRequestHandler):
            def handle(self):
                outer.connections += 1
                sock = self.request
                while True:
                    raw = _recv(sock, _FRAME.size)
                    if raw is None:
                        return
                    (n,) = _FRAME.unpack(raw)
                    body = _recv(sock, n)
                    if body is None:
                        return
                    req = json.loads(body.decode("utf-8"))
                    outer.requests.append(req)
                    result = outer.handler(req)
                    if result == "close":
                        return
                    header, payload = result
                    header = dict(header)
                    if payload:
                        header["bin"] = len(payload)
                    hb = json.dumps(header).encode("utf-8")
                    sock.sendall(_FRAME.pack(len(hb)) + hb + (payload or b""))

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _recv(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def _status(**over):
    base = {"ok": True, "protocol": PROTOCOL_VERSION, "app": "0.5.0", "sdk": 34,
            "a11y": True, "shizuku": False, "shizuku_granted": False}
    base.update(over)
    return base


class _FakeDevice:
    """只记录 forward 调用的 AdbDevice 桩"""

    def __init__(self):
        self.forwards: list[tuple[str, str]] = []
        self.removed: list[str] = []

    def forward(self, local, remote):
        self.forwards.append((local, remote))
        return True

    def remove_forward(self, local):
        self.removed.append(local)

    def get_resolution(self):
        return (1080, 1920)


@pytest.fixture
def fake(monkeypatch):
    """起假服务端；把客户端的端口选择钉到它的端口；返回 (server, device) 工厂"""
    servers: list[_FakeAgent] = []

    def make(handler):
        srv = _FakeAgent(handler)
        servers.append(srv)
        monkeypatch.setattr(agent_mod, "_PORT_RANGE", (srv.port, srv.port))
        return srv, _FakeDevice()

    yield make
    for s in servers:
        s.close()


def _ok_handler(extra=None):
    def handler(req):
        if req["op"] in ("ping", "status"):
            return _status(), b""
        if extra is not None:
            return extra(req)
        return {"ok": True, "via": "a11y"}, b""
    return handler


# ─── 线协议 ────────────────────────────────────────────────

def test_encode_and_read_roundtrip():
    raw = encode_request("tap", x=1, y=2)
    (n,) = _FRAME.unpack(raw[:4])
    assert json.loads(raw[4:]) == {"op": "tap", "x": 1, "y": 2} and n == len(raw) - 4

    a, b = socket.socketpair()
    try:
        header = json.dumps({"ok": True, "bin": 3}).encode()
        a.sendall(_FRAME.pack(len(header)) + header + b"xyz")
        got, payload = read_response(b)
        assert got["ok"] is True and payload == b"xyz"
        header = json.dumps({"ok": False, "error": "e"}).encode()
        a.sendall(_FRAME.pack(len(header)) + header)
        got, payload = read_response(b)
        assert got["error"] == "e" and payload == b""
        a.close()
        with pytest.raises(AgentTransportError):
            read_response(b)
    finally:
        b.close()


# ─── 握手 ──────────────────────────────────────────────────

def test_connect_handshake_and_close(fake):
    srv, dev = fake(_ok_handler())
    client = AgentClient(dev)
    assert client.connect() is True
    assert client.connected and client.a11y_ready and not client.shell_ready
    assert dev.forwards == [(f"tcp:{srv.port}", "localabstract:lvjiang-agent")]
    assert "无障碍" in client.describe()
    client.close()
    assert not client.connected and dev.removed == [f"tcp:{srv.port}"]


def test_connect_rejects_protocol_mismatch(fake):
    srv, dev = fake(lambda req: (_status(protocol=PROTOCOL_VERSION + 1), b""))
    client = AgentClient(dev)
    assert client.connect() is False
    assert not client.connected and dev.removed  # forward 已撤


def test_connect_rejects_agent_without_usable_input_channel(fake):
    """App 在线不等于手势可用：无障碍和 Shizuku 都不可用时必须回退 ADB。"""
    _, dev = fake(lambda req: (_status(a11y=False, shizuku_granted=False), b""))
    client = AgentClient(dev)
    assert client.connect() is False
    assert not client.connected and dev.removed


def test_connect_fake_connection_closed_immediately(fake):
    """adb forward 到不存在的目标：连上即关 → 握手读到 EOF → False"""
    srv, dev = fake(lambda req: "close")
    client = AgentClient(dev)
    assert client.connect() is False
    assert dev.removed


def test_connect_refused(monkeypatch):
    dev = _FakeDevice()
    # 挑一个没人听的端口
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    monkeypatch.setattr(agent_mod, "_PORT_RANGE", (port, port))
    assert AgentClient(dev).connect() is False
    assert dev.removed == [f"tcp:{port}"]


def test_forward_failure(monkeypatch):
    dev = _FakeDevice()
    dev.forward = lambda local, remote: False
    assert AgentClient(dev).connect() is False


# ─── call 语义 ─────────────────────────────────────────────

def test_call_raises_op_error_with_retryable(fake):
    def handler(req):
        if req["op"] == "ping":
            return _status(), b""
        return {"ok": False, "error": "节流", "retryable": True}, b""
    _, dev = fake(handler)
    client = AgentClient(dev)
    assert client.connect()
    with pytest.raises(AgentOpError) as ei:
        client.call("screenshot")
    assert ei.value.retryable and "节流" in str(ei.value)
    client.close()


def test_call_reconnects_once_after_server_drops(fake):
    state = {"drops": 1}

    def handler(req):
        if req["op"] == "ping":
            return _status(), b""
        if state["drops"] > 0:
            state["drops"] -= 1
            return "close"
        return {"ok": True, "echo": req["op"]}, b""
    srv, dev = fake(handler)
    client = AgentClient(dev)
    assert client.connect()
    header, _ = client.call("tap", x=1, y=1)
    assert header["echo"] == "tap" and srv.connections == 2
    client.close()


# ─── 截图 ──────────────────────────────────────────────────

def _rgba_frame(w=4, h=2):
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 0] = 10   # R
    rgba[..., 1] = 20   # G
    rgba[..., 2] = 30   # B
    rgba[..., 3] = 255
    return rgba


def test_decode_frame_rgba_and_png():
    import cv2

    rgba = _rgba_frame()
    img = decode_frame({"fmt": "rgba", "w": 4, "h": 2}, rgba.tobytes())
    assert img.shape == (2, 4, 3) and tuple(img[0, 0]) == (30, 20, 10)  # BGR
    assert decode_frame({"fmt": "rgba", "w": 4, "h": 2}, b"short") is None

    ok, buf = cv2.imencode(".png", img)
    assert ok
    back = decode_frame({"fmt": "png"}, buf.tobytes())
    assert back.shape == (2, 4, 3) and tuple(back[1, 3]) == (30, 20, 10)
    assert decode_frame({"fmt": "png"}, b"not png") is None
    assert decode_frame({"fmt": "jpeg"}, b"") is None


def test_agent_capture_retries_throttle_then_succeeds(fake, monkeypatch):
    monkeypatch.setattr(agent_mod.time, "sleep", lambda *_: None)
    state = {"fail": 2}
    rgba = _rgba_frame(6, 3)

    def shot(req):
        assert req["op"] == "screenshot" and req["via"] == "auto"
        if state["fail"] > 0:
            state["fail"] -= 1
            return {"ok": False, "error": "节流", "retryable": True}, b""
        return {"ok": True, "fmt": "rgba", "w": 6, "h": 3, "via": "a11y"}, rgba.tobytes()
    srv, dev = fake(_ok_handler(shot))
    client = AgentClient(dev)
    assert client.connect()
    cap = AgentCapture(client)
    assert cap.start() is True
    assert cap.get_capture_size() == (6, 3)
    assert sum(1 for r in srv.requests if r["op"] == "screenshot") == 3
    cap.stop()
    assert client.connected  # stop 不关客户端
    client.close()


def test_agent_capture_gives_up_on_non_retryable(fake, monkeypatch):
    monkeypatch.setattr(agent_mod.time, "sleep", lambda *_: None)
    srv, dev = fake(_ok_handler(lambda req: ({"ok": False, "error": "无障碍服务未连接"}, b"")))
    client = AgentClient(dev)
    assert client.connect()
    cap = AgentCapture(client)
    assert cap.capture() is None
    assert sum(1 for r in srv.requests if r["op"] == "screenshot") == 1
    # 没截成功时尺寸退回设备分辨率
    assert cap.get_capture_size() == (1080, 1920)
    client.close()


# ─── 输入 ──────────────────────────────────────────────────

def _cfg():
    return InputSimConfig(mouse_move_duration=(0.2, 0.2), click_random_offset=0,
                          before_click_wait=(0, 0), after_click_wait=(0, 0))


def _ops(srv):
    return [r for r in srv.requests if r["op"] not in ("ping", "status")]


def test_agent_input_dispatch(fake, monkeypatch):
    monkeypatch.setattr(agent_mod.time, "sleep", lambda *_: None)
    srv, dev = fake(_ok_handler())
    client = AgentClient(dev)
    assert client.connect()
    inp = AgentInput(client, _cfg())
    assert inp.background_mode is True and inp.target_hwnd is None

    inp.click_screen(10, 20, "btn")
    inp.drag_screen(1, 2, 3, 4)                       # 无 hold → swipe 200ms
    inp.drag_screen(1, 2, 3, 4, hold=0.5)             # hold → hold_move 200+500
    inp.drag_screen(1, 2, 3, 4, duration=1.0)         # 固定时长
    inp.scroll_screen(50, 60, "up", 2)                # 200px 向上
    for key in ("ESC", "HOME", "ENTER"):
        inp.key_down(key)
        inp.key_up(key)
    inp.move_screen(1, 1)                             # 无请求
    with pytest.raises(ValueError):
        inp.key_down("NOSUCHKEY")

    assert _ops(srv) == [
        {"op": "tap", "x": 10, "y": 20},
        {"op": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 200},
        {"op": "hold_move", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "move_ms": 200, "hold_ms": 500},
        {"op": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 1000},
        {"op": "swipe", "x1": 50, "y1": 60, "x2": 50, "y2": -140, "duration_ms": 100},
        {"op": "key", "name": "BACK"},
        {"op": "key", "name": "HOME"},
        {"op": "key", "keycode": 66},
    ]
    client.close()


def test_agent_input_failure_is_propagated(fake, monkeypatch):
    monkeypatch.setattr(agent_mod.time, "sleep", lambda *_: None)
    srv, dev = fake(_ok_handler(lambda req: ({"ok": False, "error": "手势被取消"}, b"")))
    client = AgentClient(dev)
    assert client.connect()
    inp = AgentInput(client, _cfg())
    with pytest.raises(AgentOpError, match="手势被取消"):
        inp.click_screen(1, 1)
    assert _ops(srv) == [{"op": "tap", "x": 1, "y": 1}]
    client.close()


# ─── 工厂 ──────────────────────────────────────────────────

def test_factories_prefer_agent_when_connected(fake):
    from lvjiang.core.android import (
        AdbCapture,
        AdbInput,
        create_capture_backend,
        create_input_backend,
    )

    srv, dev = fake(_ok_handler())
    client = AgentClient(dev)
    assert client.connect()
    assert isinstance(create_input_backend(dev, None, agent=client), AgentInput)
    assert isinstance(create_capture_backend(dev, "agent", agent=client), AgentCapture)
    assert isinstance(create_capture_backend(dev, "screencap", agent=client), AdbCapture)
    client.close()
    # 断开后退回 adb
    assert isinstance(create_input_backend(dev, None, agent=client), AdbInput)
    with pytest.raises(ValueError):
        create_capture_backend(dev, "agent", agent=client)
    assert isinstance(create_input_backend(dev, None), AdbInput)
