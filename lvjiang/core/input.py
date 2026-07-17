"""输入控制模块 - ctypes + Win32 SendInput / PostMessage，模拟人类操作

直接调用 user32.dll SendInput API，替代 pyautogui，
避免其封装层在 QThread 中可能引发的死锁问题。

支持两种模式：
- SendInput 模式（默认）：移动真实光标，需要窗口在前台
- PostMessage 后台模式：不移动光标，直接向目标窗口投递鼠标消息
"""

import ctypes
import random
import time
from ctypes import wintypes

from loguru import logger

from ..config import DelayConfig

# ─── Win32 基础设施 ────────────────────────────────────────
_user32 = ctypes.windll.user32

PUL = ctypes.POINTER(ctypes.c_ulong)


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _InputUnion)]


# 鼠标事件常量
_INPUT_MOUSE = 0
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004

# PostMessage 鼠标消息常量
_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001


def _send_mouse_event(flags: int, dx: int = 0, dy: int = 0):
    """通过 SendInput 发送鼠标事件"""
    mi = _MouseInput(
        dx=dx, dy=dy, mouseData=0, dwFlags=flags, time=0,
        dwExtraInfo=PUL(ctypes.c_ulong(0)),
    )
    ii = _InputUnion(mi=mi)
    inp = _Input(type=_INPUT_MOUSE, ii=ii)
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _smooth_move_to(x: int, y: int, duration: float):
    """平滑移动鼠标到指定位置（分步 SetCursorPos）"""
    steps = max(int(duration / 0.01), 1)
    point = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    sx, sy = point.x, point.y
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(sx + (x - sx) * ratio)
        cy = int(sy + (y - sy) * ratio)
        _user32.SetCursorPos(cx, cy)
        time.sleep(duration / steps)


def _make_lparam(x: int, y: int) -> int:
    """将 (x, y) 打包为 PostMessage LPARAM（低位 x，高位 y）"""
    return (y << 16) | (x & 0xFFFF)


def _screen_to_client(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int]:
    """屏幕坐标 → 窗口客户区坐标"""
    pt = wintypes.POINT(screen_x, screen_y)
    _user32.ScreenToClient(hwnd, ctypes.byref(pt))
    return pt.x, pt.y


def _postmessage_click(hwnd: int, client_x: int, client_y: int):
    """通过 PostMessage 向窗口发送一次点击（不移动光标）"""
    lparam = _make_lparam(client_x, client_y)
    _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, lparam)
    time.sleep(0.03)
    _user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, lparam)
    time.sleep(0.05)
    _user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0, lparam)


def _postmessage_drag(hwnd: int, x1: int, y1: int, x2: int, y2: int, steps: int = 20):
    """通过 PostMessage 向窗口发送拖拽（不移动光标）"""
    # 移动到起点
    _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, 0, _make_lparam(x1, y1))
    time.sleep(0.03)
    # 按下
    _user32.PostMessageW(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, _make_lparam(x1, y1))
    time.sleep(0.05)
    # 逐步移动
    for i in range(1, steps + 1):
        ratio = i / steps
        cx = int(x1 + (x2 - x1) * ratio)
        cy = int(y1 + (y2 - y1) * ratio)
        _user32.PostMessageW(hwnd, _WM_MOUSEMOVE, _MK_LBUTTON, _make_lparam(cx, cy))
        time.sleep(0.02)
    # 终点松开
    _user32.PostMessageW(hwnd, _WM_LBUTTONUP, 0, _make_lparam(x2, y2))


# ─── 输入控制器 ────────────────────────────────────────────

class InputController:
    """输入控制器（所有点击延迟参数统一从 DelayConfig 读取）

    支持两种模式：
    - SendInput 模式（默认）：移动真实光标，需要窗口在前台
    - PostMessage 后台模式：不移动光标，直接向目标窗口投递鼠标消息

    公开接口：click_screen / drag_screen / set_background_mode
    """

    def __init__(self, delay_config: DelayConfig | None = None):
        cfg = delay_config or DelayConfig()
        self.before_click_wait = cfg.before_click_wait
        self.after_click_wait = cfg.after_click_wait
        self.mouse_move_duration = cfg.mouse_move_duration
        self.click_random_offset = cfg.click_random_offset
        self.region_jitter_ratio = cfg.region_jitter_ratio

        # 后台模式相关
        self.background_mode = True    # 默认启用 PostMessage 后台模式（不移动光标）
        self.target_hwnd = None        # 目标窗口句柄

    def set_background_mode(self, enabled: bool, hwnd: int | None = None):
        """设置后台模式

        Args:
            enabled: 是否启用 PostMessage 后台模式
            hwnd: 目标窗口句柄（启用时必须提供）
        """
        self.background_mode = enabled
        if hwnd is not None:
            self.target_hwnd = hwnd
        if enabled:
            logger.info(f"切换到后台模式 (hwnd=0x{self.target_hwnd:08X})")
        else:
            logger.info("切换到前台模式 (SendInput)")

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """
        点击屏幕坐标（带鼠标移动时长 + 点击后延迟）
        工作流唯一调用的公开方法
        """
        if self.background_mode and self.target_hwnd:
            self._bg_click(screen_x, screen_y, poi_name)
        else:
            self._move_to(screen_x, screen_y)
            self._click(screen_x, screen_y, poi_name)

    def _move_to(self, x: int, y: int):
        """移动鼠标到指定位置（时长随机化）"""
        duration = random.uniform(*self.mouse_move_duration)
        _smooth_move_to(x, y, duration)

    def _click(self, x: int, y: int, poi_name: str = ""):
        """
        点击指定坐标（加入随机偏移和延迟模拟人类）
        poi_name: 可选的 POI 名称，用于日志
        """
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        _user32.SetCursorPos(actual_x, actual_y)
        _send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        _send_mouse_event(_MOUSEEVENTF_LEFTUP)

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    def _fg_drag(self, from_x: int, from_y: int, to_x: int, to_y: int,
                 poi_name: str = "", duration: float | tuple[float, float] | None = None,
                 hold: float | None = None):
        """前台拖拽：SendInput 移动真实光标"""
        self._move_to(from_x, from_y)
        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)
        hold_info = f" + hold {hold}s" if hold else ""
        logger.debug(f"拖拽 {poi_name}: ({from_x},{from_y}) -> ({to_x},{to_y}) [{move_dur:.2f}s]{hold_info}")
        _smooth_move_to(from_x, from_y, move_dur)
        _send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        _smooth_move_to(to_x, to_y, move_dur)
        if hold is not None and hold > 0:
            logger.debug(f"按住 {hold}s")
            time.sleep(float(hold))
        _send_mouse_event(_MOUSEEVENTF_LEFTUP)
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    def drag_screen(self, from_x: int, from_y: int, to_x: int, to_y: int, poi_name: str = "",
                    duration: float | tuple[float, float] | None = None,
                    hold: float | None = None):
        """从起点拖拽到终点（模拟人类操作）

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达目标后按住不放的时长（秒）。None 表示不按。
        """
        if self.background_mode and self.target_hwnd:
            self._bg_drag(from_x, from_y, to_x, to_y, poi_name, duration)
        else:
            self._fg_drag(from_x, from_y, to_x, to_y, poi_name, duration, hold)

    # ─── 后台模式（PostMessage）───────────────────────────────

    def _bg_click(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """后台点击：PostMessage 向目标窗口发送鼠标事件，不移动光标"""
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        sx, sy = screen_x + offset_x, screen_y + offset_y

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        cx, cy = _screen_to_client(self.target_hwnd, sx, sy)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[后台] 点击 {label}: 屏幕({sx},{sy}) -> 客户区({cx},{cy}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        _postmessage_click(self.target_hwnd, cx, cy)

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    def _bg_drag(self, from_x: int, from_y: int, to_x: int, to_y: int,
                 poi_name: str = "", duration: float | tuple[float, float] | None = None):
        """后台拖拽：PostMessage 向目标窗口发送拖拽事件，不移动光标"""
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        fx, fy = _screen_to_client(self.target_hwnd, from_x, from_y)
        tx, ty = _screen_to_client(self.target_hwnd, to_x, to_y)
        steps = max(int(move_dur / 0.02), 5)

        logger.debug(f"[后台] 拖拽 {poi_name}: ({from_x},{from_y})->({to_x},{to_y}) "
                     f"客户区: ({fx},{fy})->({tx},{ty}) [{move_dur:.2f}s]")
        _postmessage_drag(self.target_hwnd, fx, fy, tx, ty, steps=steps)

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    # ─── 前台模式（SendInput）───────────────────────────────

    def _move_to(self, x: int, y: int):
        """移动鼠标到指定位置（时长随机化）"""
        duration = random.uniform(*self.mouse_move_duration)
        _smooth_move_to(x, y, duration)

    def _click(self, x: int, y: int, poi_name: str = ""):
        """
        点击指定坐标（加入随机偏移和延迟模拟人类）
        poi_name: 可选的 POI 名称，用于日志
        """
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        _user32.SetCursorPos(actual_x, actual_y)
        _send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        _send_mouse_event(_MOUSEEVENTF_LEFTUP)

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    def _fg_drag(self, from_x: int, from_y: int, to_x: int, to_y: int,
                 poi_name: str = "", duration: float | tuple[float, float] | None = None,
                 hold: float | None = None):
        """前台拖拽：SendInput 移动真实光标"""
        self._move_to(from_x, from_y)
        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)
        hold_info = f" + hold {hold}s" if hold else ""
        logger.debug(f"拖拽 {poi_name}: ({from_x},{from_y}) -> ({to_x},{to_y}) [{move_dur:.2f}s]{hold_info}")
        _smooth_move_to(from_x, from_y, move_dur)
        _send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        _smooth_move_to(to_x, to_y, move_dur)
        if hold is not None and hold > 0:
            logger.debug(f"按住 {hold}s")
            time.sleep(float(hold))
        _send_mouse_event(_MOUSEEVENTF_LEFTUP)
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
