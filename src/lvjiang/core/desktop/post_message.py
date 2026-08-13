"""PostMessage 输入后端 - 不移动光标，直接向目标窗口投递鼠标消息

通过 Win32 PostMessageW 发送 WM_LBUTTON* 消息到指定窗口客户区，
实现后台操作（不抢占焦点、不移动光标）。
"""

import random
import time

from loguru import logger

from ...core.config import InputSimConfig
from ..input_base import InputBackend
from .win32_util import (
    postmessage_click,
    postmessage_drag,
    screen_to_client,
)


class PostMessageInput(InputBackend):
    """基于 PostMessage 的输入后端（后台模式，不移动光标）"""

    def __init__(self, input_sim: InputSimConfig | None = None, hwnd: int | None = None):
        self._inject_input_sim(self, input_sim)
        # 兼容属性：PostMessage 恒为后台模式
        self.background_mode = True
        self.target_hwnd = hwnd

    # ─── 点击 ─────────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """后台点击：PostMessage 向目标窗口发送鼠标事件，不移动光标"""
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return

        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        sx, sy = screen_x + offset_x, screen_y + offset_y

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        cx, cy = screen_to_client(self.target_hwnd, sx, sy)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[后台] 点击 {label}: 屏幕({sx},{sy}) -> 客户区({cx},{cy}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        postmessage_click(self.target_hwnd, cx, cy)

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
        """后台拖拽：PostMessage 向目标窗口发送拖拽事件，不移动光标"""
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return

        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        fx, fy = screen_to_client(self.target_hwnd, from_x, from_y)
        tx, ty = screen_to_client(self.target_hwnd, to_x, to_y)
        steps = max(int(move_dur / 0.02), 5)

        logger.debug(f"[后台] 拖拽 {poi_name}: ({from_x},{from_y})->({to_x},{to_y}) "
                     f"客户区: ({fx},{fy})->({tx},{ty}) [{move_dur:.2f}s]")
        postmessage_drag(self.target_hwnd, fx, fy, tx, ty, steps=steps)

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
