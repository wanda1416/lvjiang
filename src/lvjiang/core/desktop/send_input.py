"""SendInput 输入后端 - 移动真实光标，需窗口在前台

通过 Win32 SendInput API 注入鼠标事件，替代 pyautogui，
避免其封装层在 QThread 中可能引发的死锁问题。
"""

import random
import time

from loguru import logger

from ...core.config import InputSimConfig
from ..input_base import InputBackend
from .win32_util import (
    _MOUSEEVENTF_LEFTDOWN,
    _MOUSEEVENTF_LEFTUP,
    _user32,
    send_mouse_event,
    smooth_move_to,
)


class SendInputInput(InputBackend):
    """基于 SendInput 的输入后端（移动真实光标）"""

    def __init__(self, input_sim: InputSimConfig | None = None):
        self._inject_input_sim(self, input_sim)
        # 兼容属性：SendInput 模式无后台概念
        self.background_mode = False
        self.target_hwnd = None

    # ─── 点击 ─────────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """点击屏幕坐标（带鼠标移动时长 + 点击后延迟）"""
        self._move_to(screen_x, screen_y)
        self._click(screen_x, screen_y, poi_name)

    def _move_to(self, x: int, y: int):
        """移动鼠标到指定位置（时长随机化）"""
        duration = random.uniform(*self.mouse_move_duration)
        smooth_move_to(x, y, duration)

    def _click(self, x: int, y: int, poi_name: str = ""):
        """点击指定坐标（加入随机偏移和延迟模拟人类）"""
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        _user32.SetCursorPos(actual_x, actual_y)
        send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        send_mouse_event(_MOUSEEVENTF_LEFTUP)

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)

    # ─── 拖拽 ─────────────────────────────────────────────────

    def drag_screen(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        poi_name: str = "",
        duration: float | tuple[float, float] | None = None,
        hold: float | None = None,
    ):
        """从起点拖拽到终点（模拟人类操作）

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达目标后按住不放的时长（秒）。None 表示不按。
        """
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
        smooth_move_to(from_x, from_y, move_dur)
        send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        smooth_move_to(to_x, to_y, move_dur)
        if hold is not None and hold > 0:
            logger.debug(f"按住 {hold}s")
            time.sleep(float(hold))
        send_mouse_event(_MOUSEEVENTF_LEFTUP)
        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
