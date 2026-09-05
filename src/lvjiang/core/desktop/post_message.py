"""PostMessage 输入后端 - 不移动光标，直接向目标窗口投递鼠标消息

通过 Win32 PostMessageW 发送 WM_LBUTTON* 消息到指定窗口客户区，
实现后台操作（不抢占焦点、不移动光标）。
"""

import random
import time

from loguru import logger

from ...core.config import InputSimConfig
from ...core.key_names import normalize_key
from ..input_base import InputBackend, InputBackendKind
from .win32_keyboard import (
    KEYEVENTF_EXTENDEDKEY,
    KEYEVENTF_KEYUP,
    is_extended_key,
    key_to_vk_scan,
    post_keyboard_input,
)
from .win32_util import (
    _WHEEL_DELTA,
    postmessage_click,
    postmessage_drag,
    postmessage_move,
    postmessage_scroll,
    screen_to_client_logical,
)


class PostMessageInput(InputBackend):
    """基于 PostMessage 的输入后端（后台模式，不移动光标）"""

    kind = InputBackendKind.POST

    def __init__(self, input_sim: InputSimConfig | None = None, hwnd: int | None = None):
        self._inject_input_sim(self, input_sim)
        # 兼容属性：PostMessage 恒为后台模式
        self.background_mode = True
        self.target_hwnd = hwnd
        # 默认**不**激活目标窗口。后台模式的全部意义就是不抢焦点——
        # 用户只承诺窗口可见（副屏常驻即可），焦点归他自己用。
        #
        # 这里曾默认 True：7838759 排查「后台投递偶发不落地」时，在根因
        # 未明（当时 TODO 原文：「可能是 Win32 API 序列、窗口焦点、消息
        # 队列竞态」）的情况下，与 screen_to_client_logical 这个 DPI 坐标
        # 修正一起打包塞了进来，随后问题消失便再没回头区分谁才是真修复。
        # 代价是每次点击/移动/滚动/拖拽都 SetForegroundWindow 一来一回，
        # 后台模式退化成「只是不移动光标」，副屏挂机边用电脑的场景直接废掉。
        #
        # 真正需要它的只有 SDL 类窗口（scrcpy 投屏窗等）：那类窗口不在前台
        # 就不把 PostMessage 转成事件。这种目标可以显式把本属性设回 True。
        self.activate_before_send = False

    # ─── 点击 ─────────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = "",
                     *, pre_delay=None, post_delay=None, button: str = "left"):
        """后台点击：PostMessage 向目标窗口发送鼠标事件，不移动光标

        当前只实现了左键投递（postmessage_click 底层硬编码
        WM_LBUTTONDOWN/UP）；非左键按左键降级处理并记警告。
        """
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return
        if button != "left":
            logger.warning(f"PostMessage 模式暂不支持 {button} 键，按左键处理")

        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        sx, sy = screen_x + offset_x, screen_y + offset_y

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))

        cx, cy = screen_to_client_logical(self.target_hwnd, sx, sy)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[后台] 点击 {label}: 屏幕({sx},{sy}) -> 客户区({cx},{cy}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        postmessage_click(self.target_hwnd, cx, cy, activate=self.activate_before_send)

        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    def place_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """后台放置与移动到目标均表现为一次绝对 WM_MOUSEMOVE。"""
        self.move_screen(screen_x, screen_y, poi_name, duration=0)

    def move_screen(
        self,
        screen_x: int,
        screen_y: int,
        poi_name: str = "",
        duration: float | None = None,
    ):
        """后台移动：PostMessage 向目标窗口发送 WM_MOUSEMOVE，不移动光标"""
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return

        cx, cy = screen_to_client_logical(self.target_hwnd, screen_x, screen_y)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[后台] 移动 {label}: 屏幕({screen_x},{screen_y}) -> 客户区({cx},{cy})")
        postmessage_move(self.target_hwnd, cx, cy, activate=self.activate_before_send)

    def move_relative(
        self,
        delta_x: int,
        delta_y: int,
        poi_name: str = "",
        duration: float | None = None,
    ):
        logger.warning("PostMessage 后端不支持相对鼠标移动，已忽略 move by")

    def scroll_screen(
        self,
        screen_x: int,
        screen_y: int,
        direction: str = "down",
        amount: int = 1,
        poi_name: str = "",
        *,
        interval: float | None = None,
    ):
        """后台滚动：PostMessage 向目标窗口发送 WM_MOUSEWHEEL

        逐格发送 amount 次独立消息，理由同 SendInputInput.scroll_screen——
        目标窗口收到消息通常只按"是否发生过"响应一次，不会按 wParam 里的
        delta 数值等比例滚动。

        interval 不为 None 时，逐格之间用该固定间隔；否则用默认 20~50ms 随机间隔。
        """
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return

        cx, cy = screen_to_client_logical(self.target_hwnd, screen_x, screen_y)
        sign = 1 if direction == "up" else -1
        delta = sign * _WHEEL_DELTA
        label = f"({poi_name})" if poi_name else ""
        logger.debug(
            f"[后台] 滚轮 {label}: {direction} x{amount} "
            f"屏幕({screen_x},{screen_y}) -> 客户区({cx},{cy})"
        )
        for i in range(amount):
            postmessage_scroll(self.target_hwnd, cx, cy, delta, activate=self.activate_before_send)
            if i < amount - 1:
                time.sleep(
                    interval if interval is not None
                    else random.uniform(0.02, 0.05))

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

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))

        fx, fy = screen_to_client_logical(self.target_hwnd, from_x, from_y)
        tx, ty = screen_to_client_logical(self.target_hwnd, to_x, to_y)
        steps = max(int(move_dur / 0.02), 5)

        logger.debug(f"[后台] 拖拽 {poi_name}: ({from_x},{from_y})->({to_x},{to_y}) "
                     f"客户区: ({fx},{fy})->({tx},{ty}) [{move_dur:.2f}s]")
        postmessage_drag(self.target_hwnd, fx, fy, tx, ty, steps=steps, activate=self.activate_before_send)

        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    # ─── 键盘 ─────────────────────────────────────────────────

    def key_down(self, key: str) -> None:
        """按下按键（仅 keydown，不释放）"""
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return
        k = normalize_key(key)
        vk, scan = key_to_vk_scan(k)
        flags = KEYEVENTF_EXTENDEDKEY if is_extended_key(k) else 0
        logger.debug(f"[PostMessage] key_down: {k} (vk=0x{vk:02X}, scan={scan})")
        post_keyboard_input(self.target_hwnd, vk, scan, flags)

    def key_up(self, key: str) -> None:
        """释放按键"""
        if not self.target_hwnd:
            logger.error("PostMessage 模式未设置目标窗口句柄")
            return
        k = normalize_key(key)
        vk, scan = key_to_vk_scan(k)
        flags = KEYEVENTF_KEYUP | (KEYEVENTF_EXTENDEDKEY if is_extended_key(k) else 0)
        logger.debug(f"[PostMessage] key_up: {k} (vk=0x{vk:02X}, scan={scan})")
        post_keyboard_input(self.target_hwnd, vk, scan, flags)

    def paste_text(self, text: str) -> None:
        """通过系统剪贴板和后台 Ctrl+V 消息粘贴文本。"""
        from .clipboard import paste_via_clipboard
        paste_via_clipboard(text, self.key_down, self.key_up)
