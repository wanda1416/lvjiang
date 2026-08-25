"""Win32 Raw Input 鼠标相对位移监听。

使用独立消息线程和 message-only window 接收 ``WM_INPUT``，
不依赖系统光标坐标，因此可以捕获游戏锁定光标时的真实鼠标位移。
不使用 ``RIDEV_NOLEGACY``，不会屏蔽应用原有的点击/滚轮监听。
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable

from loguru import logger

_WM_INPUT = 0x00FF
_WM_QUIT = 0x0012
_RID_INPUT = 0x10000003
_RIM_TYPEMOUSE = 0
_RIDEV_INPUTSINK = 0x00000100
_RIDEV_REMOVE = 0x00000001
_MOUSE_MOVE_ABSOLUTE = 0x0001
_RI_MOUSE_BUTTON_4_DOWN = 0x0040
_RI_MOUSE_BUTTON_4_UP = 0x0080
_RI_MOUSE_BUTTON_5_DOWN = 0x0100
_RI_MOUSE_BUTTON_5_UP = 0x0200
_HWND_MESSAGE = ctypes.c_void_p(-3 & ((1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1))


class _RawInputDevice(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_uint16),
        ("usUsage", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("hwndTarget", ctypes.c_void_p),
    ]


class _RawInputHeader(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("dwSize", ctypes.c_uint32),
        ("hDevice", ctypes.c_void_p),
        ("wParam", ctypes.c_size_t),
    ]


class _RawMouseButtons(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", ctypes.c_uint16),
        ("usButtonData", ctypes.c_uint16),
    ]


class _RawMouseButtonUnion(ctypes.Union):
    _anonymous_ = ("buttons",)
    _fields_ = [
        ("ulButtons", ctypes.c_uint32),
        ("buttons", _RawMouseButtons),
    ]


class _RawMouse(ctypes.Structure):
    _anonymous_ = ("button_union",)
    _fields_ = [
        ("usFlags", ctypes.c_uint16),
        ("button_union", _RawMouseButtonUnion),
        ("ulRawButtons", ctypes.c_uint32),
        ("lLastX", ctypes.c_int32),
        ("lLastY", ctypes.c_int32),
        ("ulExtraInformation", ctypes.c_uint32),
    ]


class _RawInputData(ctypes.Union):
    _fields_ = [("mouse", _RawMouse)]


class _RawInput(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [
        ("header", _RawInputHeader),
        ("data", _RawInputData),
    ]


def decode_raw_mouse(buffer: bytes) -> tuple[int, int] | None:
    """解析 ``GetRawInputData`` 返回的 RAWINPUT；非相对鼠标包返回 None。"""
    if len(buffer) < ctypes.sizeof(_RawInput):
        return None
    raw = _RawInput.from_buffer_copy(buffer)
    if raw.header.dwType != _RIM_TYPEMOUSE:
        return None
    if raw.mouse.usFlags & _MOUSE_MOVE_ABSOLUTE:
        return None
    return int(raw.mouse.lLastX), int(raw.mouse.lLastY)


def decode_raw_mouse_buttons(buffer: bytes) -> tuple[tuple[str, bool], ...]:
    """解析 RAWINPUT 中的物理侧键事件。

    Raw Input 将鼠标“后退/前进”键称为 Button 4/5；统一映射到 DSL 已有的
    ``x1/x2``。一个包可能同时包含多个标志，因此返回事件序列。
    """
    if len(buffer) < ctypes.sizeof(_RawInput):
        return ()
    raw = _RawInput.from_buffer_copy(buffer)
    if raw.header.dwType != _RIM_TYPEMOUSE:
        return ()
    flags = int(raw.mouse.usButtonFlags)
    mapping = (
        (_RI_MOUSE_BUTTON_4_DOWN, "x1", True),
        (_RI_MOUSE_BUTTON_4_UP, "x1", False),
        (_RI_MOUSE_BUTTON_5_DOWN, "x2", True),
        (_RI_MOUSE_BUTTON_5_UP, "x2", False),
    )
    return tuple((button, pressed) for flag, button, pressed in mapping
                 if flags & flag)


def message_time_to_monotonic_ns(
    message_time_ms: int,
    current_tick_ms: int,
    current_monotonic_ns: int,
) -> int:
    """将 ``MSG.time`` 的 32 位毫秒时间扩展到当前单调时钟。

    不直接使用回调处理时刻：高频鼠标包在消息队列中积压时，
    处理时刻会把原本的包间隔压缩掉。``MSG.time`` 是事件投递时间，
    精度 1ms，并且是 GetTickCount 的低 32 位。
    """
    current_tick_ms = int(current_tick_ms)
    event_tick = (current_tick_ms & ~0xFFFFFFFF) | (int(message_time_ms) & 0xFFFFFFFF)
    if event_tick > current_tick_ms:
        event_tick -= 1 << 32
    queued_ms = max(current_tick_ms - event_tick, 0)
    return int(current_monotonic_ns) - queued_ms * 1_000_000


class RawMouseListener:
    """在独立 Win32 消息线程中监听鼠标 Raw Input。"""

    def __init__(
        self,
        on_move: Callable[[int, int, int], None],
        on_button: Callable[[str, bool, int, int, int], None] | None = None,
    ):
        self._on_move = on_move
        self._on_button = on_button
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None

    def start(self):
        if sys.platform != "win32":
            raise RuntimeError("Raw Input 鼠标录制仅支持 Windows")
        if self._thread is not None:
            return
        self._ready.clear()
        self._startup_error = None
        self._thread = threading.Thread(
            target=self._run,
            name="lvjiang-raw-mouse",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(2.0):
            self.stop()
            raise RuntimeError("Raw Input 鼠标监听启动超时")
        if self._startup_error is not None:
            error = self._startup_error
            self.stop()
            raise RuntimeError(f"Raw Input 鼠标监听启动失败: {error}") from error

    def stop(self):
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        if self._thread_id and sys.platform == "win32":
            posted = ctypes.windll.user32.PostThreadMessageW(
                wintypes.DWORD(self._thread_id), _WM_QUIT, 0, 0)
            if not posted:
                logger.warning(
                    "Raw Input 鼠标监听退出信号投递失败（PostThreadMessageW），"
                    "线程可能无法正常退出")
        if thread is not threading.current_thread():
            thread.join(2.0)
            if thread.is_alive():
                logger.warning("Raw Input 鼠标监听线程 2 秒内未退出")
        self._thread_id = 0

    def _run(self):  # pragma: no cover - 真实 Win32 消息循环由上机验证
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = None
        registered = False
        try:
            # 线程消息队列由 OS 在首次调用消息相关 API 时惰性创建；stop()
            # 靠 PostThreadMessageW 通知退出，必须先确保队列已存在，否则
            # 该调用会因 ERROR_INVALID_THREAD_ID 静默失败，线程永久卡在
            # 下面的 GetMessageW（daemon 线程不阻塞进程退出，但会泄漏
            # 窗口 / Raw Input 注册）。用 PM_NOREMOVE 的 PeekMessageW 强制
            # 创建队列后再暴露 _thread_id，保证 stop() 一旦看到非零
            # _thread_id 就意味着队列已就绪、消息投递一定能成功。
            peek_msg = wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(peek_msg), None, 0, 0, 0)
            self._thread_id = int(kernel32.GetCurrentThreadId())
            lpvoid = getattr(wintypes, "LPVOID", ctypes.c_void_p)
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.CreateWindowExW.argtypes = [
                wintypes.DWORD,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                wintypes.HWND,
                wintypes.HMENU,
                wintypes.HINSTANCE,
                lpvoid,
            ]
            user32.RegisterRawInputDevices.restype = wintypes.BOOL
            user32.RegisterRawInputDevices.argtypes = [
                ctypes.POINTER(_RawInputDevice), wintypes.UINT, wintypes.UINT,
            ]
            user32.GetRawInputData.restype = wintypes.UINT
            user32.GetRawInputData.argtypes = [
                wintypes.HANDLE,
                wintypes.UINT,
                lpvoid,
                ctypes.POINTER(wintypes.UINT),
                wintypes.UINT,
            ]
            kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            hwnd = user32.CreateWindowExW(
                0, "STATIC", "lvjiang-raw-mouse", 0,
                0, 0, 0, 0, _HWND_MESSAGE, None, None, None,
            )
            if not hwnd:
                raise ctypes.WinError()

            device = _RawInputDevice(
                usUsagePage=0x01,
                usUsage=0x02,
                dwFlags=_RIDEV_INPUTSINK,
                hwndTarget=hwnd,
            )
            if not user32.RegisterRawInputDevices(
                ctypes.byref(device), 1, ctypes.sizeof(device)):
                raise ctypes.WinError()
            registered = True
            self._ready.set()

            msg = wintypes.MSG()
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError()
                if msg.message == _WM_INPUT:
                    timestamp_ns = message_time_to_monotonic_ns(
                        msg.time,
                        kernel32.GetTickCount64(),
                        time.monotonic_ns(),
                    )
                    self._handle_wm_input(user32, msg.lParam, timestamp_ns)
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except BaseException as exc:  # noqa: BLE001 - 必须传回启动线程
            if not self._ready.is_set():
                self._startup_error = exc
                self._ready.set()
            else:
                logger.exception("Raw Input 鼠标监听线程异常")
        finally:
            if registered:
                remove = _RawInputDevice(
                    usUsagePage=0x01,
                    usUsage=0x02,
                    dwFlags=_RIDEV_REMOVE,
                    hwndTarget=None,
                )
                if not user32.RegisterRawInputDevices(
                    ctypes.byref(remove), 1, ctypes.sizeof(remove)):
                    logger.warning("Raw Input 鼠标设备注销失败")
            if hwnd:
                user32.DestroyWindow(hwnd)
            self._ready.set()

    def _handle_wm_input(self, user32, raw_handle, timestamp_ns: int):
        size = wintypes.UINT(0)
        header_size = ctypes.sizeof(_RawInputHeader)
        result = user32.GetRawInputData(
            raw_handle, _RID_INPUT, None, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF or not size.value:
            return
        buffer = ctypes.create_string_buffer(size.value)
        result = user32.GetRawInputData(
            raw_handle, _RID_INPUT, buffer, ctypes.byref(size), header_size)
        if result == 0xFFFFFFFF:
            return
        packet = buffer.raw[:size.value]
        movement = decode_raw_mouse(packet)
        if movement is not None and movement != (0, 0):
            try:
                self._on_move(movement[0], movement[1], timestamp_ns)
            except Exception:  # noqa: BLE001 - Win32 消息线程不能被回调异常打断
                logger.exception("Raw Input 鼠标移动回调异常")

        buttons = decode_raw_mouse_buttons(packet)
        if buttons and self._on_button is not None:
            point = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return
            for button, pressed in buttons:
                try:
                    self._on_button(
                        button, pressed, point.x, point.y, timestamp_ns)
                except Exception:  # noqa: BLE001
                    logger.exception("Raw Input 鼠标侧键回调异常")


__all__ = [
    "RawMouseListener",
    "decode_raw_mouse",
    "decode_raw_mouse_buttons",
    "message_time_to_monotonic_ns",
]
