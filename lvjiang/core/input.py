"""输入控制模块 - ctypes + Win32 SendInput，模拟人类操作

直接调用 user32.dll SendInput API，替代 pyautogui，
避免其封装层在 QThread 中可能引发的死锁问题。
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


# ─── 输入控制器 ────────────────────────────────────────────

class InputController:
    """输入控制器（所有点击延迟参数统一从 DelayConfig 读取）

    公开接口：click_screen / drag_screen
    """

    def __init__(self, delay_config: DelayConfig | None = None):
        cfg = delay_config or DelayConfig()
        self.before_click_wait = cfg.before_click_wait
        self.after_click_wait = cfg.after_click_wait
        self.mouse_move_duration = cfg.mouse_move_duration
        self.click_random_offset = cfg.click_random_offset
        self.region_jitter_ratio = cfg.region_jitter_ratio

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """
        点击屏幕坐标（带鼠标移动时长 + 点击后延迟）
        工作流唯一调用的公开方法
        """
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
        # 随机偏移
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        # 点击前延迟（模拟人类反应时间）
        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        # 执行点击（SendInput）
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        _user32.SetCursorPos(actual_x, actual_y)
        _send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        _send_mouse_event(_MOUSEEVENTF_LEFTUP)

        # 点击后等待
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
        self._move_to(from_x, from_y)
        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)
        # 解析时长
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
        # 到达目标后按住不放
        if hold is not None and hold > 0:
            logger.debug(f"按住 {hold}s")
            time.sleep(float(hold))
        _send_mouse_event(_MOUSEEVENTF_LEFTUP)
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
