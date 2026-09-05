"""设备端代理通道 — PC 经 adb forward 连接律匠 app 的 AgentServer

PC 原先控制手机只有 `adb shell input tap/swipe` 与 `adb exec-out screencap -p` 两条路：
input 命令是发完就返回（手势没落地就截图会截到旧画面）、swipe 表达不了"推到位停住"，
screencap 每帧要起一次 adb 子进程再 PNG 编解码。设备上的律匠 app 已经有无障碍
（takeScreenshot / dispatchGesture / performGlobalAction）与 Shizuku shell 两条通道，
本模块把它们借到 PC 端来用：

    AgentClient   线协议 + 连接管理（adb forward tcp:<port> localabstract:lvjiang-agent）
    AgentCapture  CaptureBackend：截图经代理（无障碍 RGBA 裸字节，或 Shizuku PNG）
    AgentInput    InputBackend：点击/拖拽/推住/按键经代理（无障碍手势优先）

线协议与 android/.../AgentServer.kt 一一对应（改一边必须同步改另一边）：
    请求：4 字节大端长度 + UTF-8 JSON {"op": ..., ...}
    响应：4 字节大端长度 + JSON 头；头里有 "bin": N 则紧跟 N 字节二进制负载
"""

from __future__ import annotations

import json
import random
import socket
import struct
import threading
import time
from typing import Any

import numpy as np
from loguru import logger

from ...core.config import InputSimConfig
from ..capture_base import CaptureBackend
from ..input_base import InputBackend, InputBackendKind
from .device import AdbDevice
from .input import _KEY_TO_ANDROID_KEYCODE

#: 设备端 LocalServerSocket 的 abstract 名（与 AgentServer.SOCKET_NAME 一致）
AGENT_SOCKET_NAME = "lvjiang-agent"
#: 律匠 app 包名，日志提示用
AGENT_PACKAGE = "com.lvjiang.app"
#: 协议版本：设备端返回不一致时拒绝使用（避免两端静默错位）
PROTOCOL_VERSION = 2

#: PC 本地转发端口范围（与 scrcpy 的 27183 错开）
_PORT_RANGE = (27300, 27399)
_FRAME = struct.Struct(">I")
#: JSON 头上限；二进制负载另算（整屏 RGBA 十几 MB）
_MAX_HEADER_BYTES = 1 << 20
_MAX_PAYLOAD_BYTES = 256 << 20


class AgentError(RuntimeError):
    """代理通道错误基类"""


class AgentTransportError(AgentError):
    """socket 层失败（断线、超时、协议错位）——已尝试重连仍失败"""


class AgentOpError(AgentError):
    """设备端执行失败（ok=false），error 为设备端给的原因"""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


# ─── 线协议（纯函数，便于单测）────────────────────────────

def encode_request(op: str, **params: Any) -> bytes:
    """打包一条请求帧"""
    body = json.dumps({"op": op, **params}, ensure_ascii=False).encode("utf-8")
    return _FRAME.pack(len(body)) + body


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = sock.recv(min(remaining, 1 << 20))
        if not chunk:
            raise AgentTransportError("连接被设备端关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_response(sock: socket.socket) -> tuple[dict, bytes]:
    """读一条响应帧，返回 (JSON 头, 二进制负载)；无负载时为 b\"\" """
    (header_len,) = _FRAME.unpack(_recv_exact(sock, _FRAME.size))
    if header_len <= 0 or header_len > _MAX_HEADER_BYTES:
        raise AgentTransportError(f"响应头长度非法: {header_len}")
    try:
        header = json.loads(_recv_exact(sock, header_len).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise AgentTransportError(f"响应头不是合法 JSON: {e}") from e
    if not isinstance(header, dict):
        raise AgentTransportError("响应头不是 JSON 对象")
    payload = b""
    bin_len = header.get("bin", 0)
    if bin_len:
        if not isinstance(bin_len, int) or bin_len < 0 or bin_len > _MAX_PAYLOAD_BYTES:
            raise AgentTransportError(f"负载长度非法: {bin_len!r}")
        payload = _recv_exact(sock, bin_len)
    return header, payload


# ─── 客户端 ──────────────────────────────────────────────

class AgentClient:
    """与设备端 AgentServer 的单连接客户端（线程安全，调用串行）

    连接流程：挑一个本地端口做 adb forward → TCP 连 127.0.0.1:<port> → ping 校验协议版本。
    传输层失败会就地重连一次（adb forward 仍在，设备端进程没死的话能直接接上）。
    """

    def __init__(self, device: AdbDevice, socket_name: str = AGENT_SOCKET_NAME,
                 timeout: float = 15.0):
        self._device = device
        self._socket_name = socket_name
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._port: int | None = None
        self._lock = threading.RLock()
        self._status: dict = {}

    @property
    def device(self) -> AdbDevice:
        return self._device

    @property
    def connected(self) -> bool:
        return self._sock is not None

    @property
    def status(self) -> dict:
        """最近一次 ping/status 返回的设备端状态（a11y / shizuku 可用性等）"""
        return dict(self._status)

    @property
    def a11y_ready(self) -> bool:
        return bool(self._status.get("a11y"))

    @property
    def shell_ready(self) -> bool:
        return bool(self._status.get("shizuku_granted"))

    @property
    def calib_identity(self) -> bool:
        """设备端屏幕映射是否恒等（旧版 app 没这个字段时视为恒等）"""
        return bool(self._status.get("calib_identity", True))

    def describe(self) -> str:
        """供 UI 标签/日志用的一句话通道描述"""
        if not self.connected:
            return "未连接"
        parts = ["无障碍" if self.a11y_ready else "无障碍未开"]
        if self.shell_ready:
            parts.append("Shizuku")
        if not self.calib_identity:
            parts.append("已标定")
        return "设备端代理(" + "/".join(parts) + ")"

    # ─── 屏幕映射标定（截图坐标 → 输入坐标，设备端 ScreenMap / CalibOverlay）──

    def calib_get(self) -> dict:
        """当前朝向分辨率的映射参数：{key, screen{w,h,rotation}, calib{sx,ox,sy,oy}, identity, stored}"""
        header, _ = self.call("calib_get")
        return header

    def calib_set(self, sx: float = 1.0, ox: float = 0.0, sy: float = 1.0, oy: float = 0.0) -> dict:
        """保存逐轴仿射 input% = shot% * s + o；全恒等等价于 calib_clear"""
        header, _ = self.call("calib_set", sx=float(sx), ox=float(ox), sy=float(sy), oy=float(oy))
        return header

    def calib_clear(self) -> dict:
        header, _ = self.call("calib_clear")
        return header

    def calib_mark(self, x: int, y: int, tap: bool = False, via: str = "auto") -> dict:
        """在 (x, y) 经映射后的落点画准星（需悬浮窗权限）；tap=True 时同点再点一下。返回含 px{x,y}"""
        header, _ = self.call("calib_mark", x=int(x), y=int(y), tap=bool(tap), via=via)
        return header

    def calib_hide(self) -> None:
        self.call("calib_hide")

    def set_float_icon(self, hidden: bool = True) -> bool:
        """动态显隐设备端悬浮球（截图 / 标定前藏起来）。返回悬浮服务是否在运行"""
        header, _ = self.call("float_icon", hidden=bool(hidden))
        return bool(header.get("running"))

    # ─── 连接管理 ─────────────────────────────────────────

    def connect(self, connect_timeout: float = 3.0) -> bool:
        """建立转发并握手；失败返回 False（原因已记日志），不抛异常"""
        with self._lock:
            self.close()
            port = self._setup_forward()
            if port is None:
                return False
            self._port = port
            if not self._open_socket(connect_timeout):
                self._cleanup_forward()
                return False
            try:
                header, _ = self._roundtrip("ping")
            except AgentError as e:
                logger.warning(f"[Agent] 握手失败: {e}")
                self.close()
                return False
            if header.get("protocol") != PROTOCOL_VERSION:
                logger.error(f"[Agent] 协议版本不匹配: 设备端 {header.get('protocol')} / PC {PROTOCOL_VERSION}，"
                             f"请升级手机上的律匠 app")
                self.close()
                return False
            self._status = header
            if not self.a11y_ready and not self.shell_ready:
                logger.warning(
                    "[Agent] app 已连接，但无障碍服务未开启且 Shizuku 未授权，"
                    "设备端没有可用输入通道"
                )
                self.close()
                return False
            logger.info(f"[Agent] 已连接设备端代理 app={header.get('app')} "
                        f"a11y={header.get('a11y')} shizuku_granted={header.get('shizuku_granted')}")
            return True

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
            self._cleanup_forward()
            self._status = {}

    def _setup_forward(self) -> int | None:
        ports = list(range(_PORT_RANGE[0], _PORT_RANGE[1] + 1))
        random.shuffle(ports)
        for port in ports[:5]:
            if self._device.forward(f"tcp:{port}", f"localabstract:{self._socket_name}"):
                return port
        logger.error("[Agent] adb forward 失败（本地端口被占？）")
        return None

    def _cleanup_forward(self) -> None:
        if self._port is not None:
            self._device.remove_forward(f"tcp:{self._port}")
            self._port = None

    def _open_socket(self, connect_timeout: float) -> bool:
        assert self._port is not None
        try:
            sock = socket.create_connection(("127.0.0.1", self._port), timeout=connect_timeout)
        except OSError as e:
            logger.warning(f"[Agent] 连不上设备端代理（{e}）：请确认手机已安装律匠 app 并开启无障碍服务")
            return False
        # forward 目标不存在时 adb 不会在 connect 阶段报错，而是建好连接后立刻关掉；
        # 这种"假连接"由随后的 ping 握手识别（读到 EOF → 传输错误 → connect 返回 False）
        sock.settimeout(connect_timeout)
        self._sock = sock
        return True

    def _reconnect(self) -> bool:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._port is None:
            return False
        return self._open_socket(3.0)

    # ─── 调用 ─────────────────────────────────────────────

    def _roundtrip(self, op: str, timeout: float | None = None, **params: Any) -> tuple[dict, bytes]:
        sock = self._sock
        if sock is None:
            raise AgentTransportError("代理未连接")
        sock.settimeout(timeout if timeout is not None else self._timeout)
        try:
            sock.sendall(encode_request(op, **params))
            return read_response(sock)
        except (OSError, struct.error) as e:
            raise AgentTransportError(f"{op}: {e}") from e

    def call(self, op: str, timeout: float | None = None, **params: Any) -> tuple[dict, bytes]:
        """发一条请求并等结果；ok=false 抛 AgentOpError，传输失败重连一次后仍失败抛 AgentTransportError"""
        with self._lock:
            try:
                header, payload = self._roundtrip(op, timeout, **params)
            except AgentTransportError as first:
                logger.warning(f"[Agent] 传输失败，尝试重连: {first}")
                if not self._reconnect():
                    raise
                header, payload = self._roundtrip(op, timeout, **params)
            if not header.get("ok"):
                raise AgentOpError(str(header.get("error", "设备端未说明原因")),
                                   retryable=bool(header.get("retryable")))
            return header, payload

    def refresh_status(self) -> dict:
        header, _ = self.call("status")
        self._status = header
        return dict(header)


def connect_agent(device: AdbDevice) -> AgentClient | None:
    """便捷入口：连上返回客户端，连不上返回 None（原因已记日志）"""
    client = AgentClient(device)
    return client if client.connect() else None


# ─── 截图后端 ─────────────────────────────────────────────

class AgentCapture(CaptureBackend):
    """经设备端代理截图

    via="auto"：无障碍可用走 takeScreenshot（RGBA 裸字节，免 PNG 编解码），否则 Shizuku screencap。
    takeScreenshot 有数百毫秒级节流，设备端标 retryable 的失败这里退避重试。
    """

    _RETRY_DELAY = 0.4
    _MAX_ATTEMPTS = 3

    def __init__(self, client: AgentClient, via: str = "auto"):
        self._client = client
        self._via = via
        self._size: tuple[int, int] | None = None

    @property
    def client(self) -> AgentClient:
        return self._client

    def start(self) -> bool:
        return self.capture() is not None

    def stop(self) -> None:
        """客户端由连接流程统一持有并关闭，这里不动它"""

    def capture(self, timeout: float = 10.0) -> np.ndarray | None:
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            try:
                header, payload = self._client.call(
                    "screenshot", timeout=timeout + 5.0,
                    via=self._via, timeout_ms=int(timeout * 1000),
                )
            except AgentOpError as e:
                if e.retryable and attempt < self._MAX_ATTEMPTS:
                    logger.debug(f"[Agent] 截图失败（{e}），{self._RETRY_DELAY}s 后重试 {attempt}/{self._MAX_ATTEMPTS - 1}")
                    time.sleep(self._RETRY_DELAY)
                    continue
                logger.error(f"[Agent] 截图失败: {e}")
                return None
            except AgentError as e:
                logger.error(f"[Agent] 截图失败: {e}")
                return None
            img = decode_frame(header, payload)
            if img is None:
                return None
            h, w = img.shape[:2]
            self._size = (w, h)
            return img
        return None

    def get_capture_size(self) -> tuple[int, int]:
        if self._size is None and self.capture() is None:
            return self._client.device.get_resolution()
        return self._size or (0, 0)


def decode_frame(header: dict, payload: bytes) -> np.ndarray | None:
    """把代理返回的帧解成 BGR numpy；格式不对返回 None（已记日志）"""
    import cv2

    fmt = header.get("fmt")
    if fmt == "rgba":
        w, h = int(header.get("w", 0)), int(header.get("h", 0))
        if w <= 0 or h <= 0 or len(payload) != w * h * 4:
            logger.error(f"[Agent] RGBA 帧尺寸不对: {len(payload)} != {w}x{h}x4")
            return None
        rgba = np.frombuffer(payload, dtype=np.uint8).reshape(h, w, 4)
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
    if fmt == "png":
        img = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            logger.error(f"[Agent] PNG 解码失败（{len(payload)} 字节）")
        return img
    logger.error(f"[Agent] 未知帧格式: {fmt!r}")
    return None


# ─── 输入后端 ─────────────────────────────────────────────

class AgentInput(InputBackend):
    """经设备端代理注入手势（替代 adb shell input）

    与 AdbInput 的公开面完全一致，差别只在落地方式：
    - 点击/拖拽走无障碍 dispatchGesture，回调后才返回，紧接着截图不会截到旧画面
    - drag hold 走两段 stroke 真正"推到位停住"（input swipe 只能匀速滑完全程）
    - ESC → 系统 BACK、HOME → 系统 HOME（performGlobalAction）；其它键只有 Shizuku 在时能发
    无障碍没开但 Shizuku 已授权时，设备端自动改走 `input` 命令，行为退化到与 AdbInput 相同。
    """

    kind = InputBackendKind.AGENT

    def __init__(self, client: AgentClient, input_sim: InputSimConfig | None = None):
        self._inject_input_sim(self, input_sim)
        self._client = client
        self.background_mode = True
        self.target_hwnd = None

    @property
    def client(self) -> AgentClient:
        return self._client

    def _call(self, op: str, what: str, **params: Any) -> None:
        """下发输入；失败必须传播，不能让工作流误以为动作已经执行。"""
        try:
            self._client.call(op, **params)
        except AgentError as e:
            logger.warning(f"[Agent] {what}未成功: {e}")
            raise

    # ─── 点击 ─────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = "",
                     *, pre_delay=None, post_delay=None, button: str = "left"):
        """触屏没有鼠标键概念，非 left 时按普通点击处理并记警告。"""
        if button != "left":
            logger.warning(f"[Agent] 设备端手势不支持 {button} 键，按普通点击处理")
        sx = screen_x + random.randint(-self.click_random_offset, self.click_random_offset)
        sy = screen_y + random.randint(-self.click_random_offset, self.click_random_offset)
        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[Agent] 点击 {label}: ({sx},{sy})")
        self._call("tap", "点击", x=int(sx), y=int(sy))
        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    def place_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        logger.warning("[Agent] place 指令无效：设备端手势不支持鼠标放置")

    def move_screen(
        self, screen_x: int, screen_y: int, poi_name: str = "",
        duration: float | None = None,
    ):
        logger.warning("[Agent] move 指令无效：设备端手势不支持鼠标移动")

    def move_relative(
        self, delta_x: int, delta_y: int, poi_name: str = "",
        duration: float | None = None,
    ):
        logger.warning("[Agent] move by 指令无效：设备端手势不支持鼠标移动")

    def scroll_screen(self, screen_x: int, screen_y: int, direction: str = "down",
                      amount: int = 1, poi_name: str = "", *, interval: float | None = None):
        """与 AdbInput 同一套换算：每格 100px 的短距离滑动模拟滚轮。
        单次连续 swipe，interval 参数在此后端被忽略。"""
        dist = 100 * amount
        end_y = screen_y - dist if direction == "up" else screen_y + dist
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[Agent] 滚轮 {label}: {direction} x{amount} @ ({screen_x}, {screen_y})")
        self._call("swipe", "滚动", x1=int(screen_x), y1=int(screen_y),
                   x2=int(screen_x), y2=int(end_y), duration_ms=100)

    # ─── 拖拽 ─────────────────────────────────────────────

    def drag_screen(self, from_x: int, from_y: int, to_x: int, to_y: int, poi_name: str = "",
                    duration: float | tuple[float, float] | None = None, hold: float | None = None,
                    *, pre_delay=None, post_delay=None):
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)
        move_ms = int(move_dur * 1000)
        hold_ms = int(float(hold) * 1000) if hold and hold > 0 else 0

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))
        hold_info = f" + hold {hold_ms}ms" if hold_ms else ""
        logger.debug(f"[Agent] 拖拽 {poi_name}: ({from_x},{from_y})->({to_x},{to_y}) {move_ms}ms{hold_info}")
        coords = dict(x1=int(from_x), y1=int(from_y), x2=int(to_x), y2=int(to_y))
        if hold_ms > 0:
            self._call("hold_move", "推住", move_ms=move_ms, hold_ms=hold_ms, **coords)
        else:
            self._call("swipe", "拖拽", duration_ms=move_ms, **coords)
        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    # ─── 键盘 ─────────────────────────────────────────────

    _GLOBAL_KEYS = {"ESC": "BACK", "HOME": "HOME"}

    def key_down(self, key: str) -> None:
        """ESC/HOME 走系统全局动作；其余键按 keycode 发（需要 Shizuku）"""
        upper = key.strip().upper()
        if upper in self._GLOBAL_KEYS:
            logger.debug(f"[Agent] key: {upper} → {self._GLOBAL_KEYS[upper]}")
            self._call("key", f"按键 {upper}", name=self._GLOBAL_KEYS[upper])
            return
        code = _KEY_TO_ANDROID_KEYCODE.get(upper)
        if code is None:
            raise ValueError(f"未知按键名: {key!r}，无对应 Android keycode")
        logger.debug(f"[Agent] key: {upper} → keycode {code}")
        self._call("key", f"按键 {upper}", keycode=code)

    def key_up(self, key: str) -> None:
        """keyevent / 全局动作都是一次性的，抬起无事可做"""
