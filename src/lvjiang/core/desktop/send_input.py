"""SendInput 输入后端 - 移动真实光标，需窗口在前台

通过 Win32 SendInput API 注入鼠标事件，替代 pyautogui，
避免其封装层在 QThread 中可能引发的死锁问题。
"""

import random
import time

from loguru import logger

from ...core.config import InputSimConfig
from ..input_base import InputBackend
from .win32_keyboard import (
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    KEYEVENTF_SCANCODE,
    is_extended_key,
    key_to_vk_scan,
    normalize_key,
    send_keyboard_input,
)
from .win32_util import (
    _MOUSEEVENTF_LEFTDOWN,
    _MOUSEEVENTF_LEFTUP,
    _WHEEL_DELTA,
    _user32,
    activate_window,
    send_mouse_event,
    send_mouse_wheel_event,
    smooth_move_to,
)


class SendInputInput(InputBackend):
    """基于 SendInput 的输入后端（移动真实光标）"""

    def __init__(self, input_sim: InputSimConfig | None = None):
        self._inject_input_sim(self, input_sim)
        # 兼容属性：SendInput 模式无后台概念
        self.background_mode = False
        self.target_hwnd = None
        # SDL/游戏窗口（scrcpy、燕云十六声等）只有处于前台/焦点才处理
        # SendInput 事件。点击前先瞬时激活目标窗口（随后还原焦点）。
        self.activate_before_send = True

    # ─── 点击 ─────────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = "",
                     *, pre_delay=None, post_delay=None):
        """点击屏幕坐标（带鼠标移动时长 + 点击后延迟）"""
        self._activate_target()
        self._move_to(screen_x, screen_y)
        self._click(screen_x, screen_y, poi_name, pre_delay=pre_delay, post_delay=post_delay)

    def move_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """移动鼠标到屏幕坐标（不点击）"""
        self._activate_target()
        self._move_to(screen_x, screen_y)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"移动 {label}: ({screen_x}, {screen_y})")

    def scroll_screen(
        self,
        screen_x: int,
        screen_y: int,
        direction: str = "down",
        amount: int = 1,
        poi_name: str = "",
    ):
        """在指定坐标位置发送鼠标滚轮事件

        逐格发送 amount 次独立的单格（WHEEL_DELTA=120）滚轮事件，而不是把
        amount 折算进单次事件的 mouseData 一次性发出——很多游戏 UI（含 SDL
        窗口）收到滚轮消息只按"是否发生过"响应一次，不按消息里的 delta 数值
        等比例滚动，一次性发大 delta 会被当成只滚了 1 格（实测：down 2 效果
        与 down 1 一致）。真实鼠标硬件每格也是独立发一条消息，逐格发送能同
        时兼容"按消息计数"和"按 delta 累加"两种处理方式。
        """
        self._activate_target()
        self._move_to(screen_x, screen_y)
        sign = 1 if direction == "up" else -1
        delta = sign * _WHEEL_DELTA
        for i in range(amount):
            send_mouse_wheel_event(delta)
            if i < amount - 1:
                time.sleep(random.uniform(0.02, 0.05))
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"滚轮 {label}: {direction} x{amount} @ ({screen_x}, {screen_y})")

    def _activate_target(self):
        """点击/拖拽前瞬时激活目标窗口（若设置了 hwnd）。

        SDL/游戏窗口只有处于前台/焦点才处理 SendInput 事件（历史验证：
        燕云十六声、scrcpy 均需激活窗口到前台才能收到点击）。激活后
        activate_window 会自动还原原前台窗口焦点。
        """
        if self.target_hwnd and self.activate_before_send:
            activate_window(self.target_hwnd)

    def _move_to(self, x: int, y: int):
        """移动鼠标到指定位置（时长随机化）"""
        duration = random.uniform(*self.mouse_move_duration)
        smooth_move_to(x, y, duration)

    def _click(self, x: int, y: int, poi_name: str = "",
               *, pre_delay=None, post_delay=None):
        """点击指定坐标（加入随机偏移和延迟模拟人类）"""
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = x + offset_x
        actual_y = y + offset_y

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))

        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        _user32.SetCursorPos(actual_x, actual_y)
        send_mouse_event(_MOUSEEVENTF_LEFTDOWN)
        send_mouse_event(_MOUSEEVENTF_LEFTUP)

        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

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
        *, pre_delay=None, post_delay=None,
    ):
        """从起点拖拽到终点（模拟人类操作）

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达目标后按住不放的时长（秒）。None 表示不按。
        """
        self._activate_target()
        self._move_to(from_x, from_y)
        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))
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
        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    # ─── 键盘 ─────────────────────────────────────────────────

    def key_down(self, key: str) -> None:
        """按下按键（仅 keydown，不释放）

        以扫描码为主键码（KEYEVENTF_SCANCODE），同时消息型应用会由系统把
        扫描码翻译成 VK 码、DirectInput 游戏直接读扫描码，两类都能命中。
        """
        self._activate_target()
        k = normalize_key(key)
        vk, scan = key_to_vk_scan(k)
        flags = KEYEVENTF_SCANCODE
        if is_extended_key(k):
            flags |= KEYEVENTF_EXTENDEDKEY
        logger.debug(f"[SendInput] key_down: {k} (vk=0x{vk:02X}, scan={scan})")
        send_keyboard_input(vk, scan, flags)

    def key_up(self, key: str) -> None:
        """释放按键"""
        self._activate_target()
        k = normalize_key(key)
        vk, scan = key_to_vk_scan(k)
        flags = KEYEVENTF_KEYUP | KEYEVENTF_SCANCODE
        if is_extended_key(k):
            flags |= KEYEVENTF_EXTENDEDKEY
        logger.debug(f"[SendInput] key_up: {k} (vk=0x{vk:02X}, scan={scan})")
        send_keyboard_input(vk, scan, flags)
