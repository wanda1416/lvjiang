"""SendInput 输入后端 - 移动真实光标，需窗口在前台

通过 Win32 SendInput API 注入鼠标事件，替代 pyautogui，
避免其封装层在 QThread 中可能引发的死锁问题。
"""

import ctypes
import random
import threading
import time
from ctypes import wintypes
from typing import Callable

from loguru import logger

from ...core.config import InputSimConfig
from ...core.input_trace import InputTrace
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
    _MOUSEEVENTF_MIDDLEDOWN,
    _MOUSEEVENTF_MIDDLEUP,
    _MOUSEEVENTF_MOVE,
    _MOUSEEVENTF_MOVE_NOCOALESCE,
    _MOUSEEVENTF_RIGHTDOWN,
    _MOUSEEVENTF_RIGHTUP,
    _MOUSEEVENTF_XDOWN,
    _MOUSEEVENTF_XUP,
    _WHEEL_DELTA,
    _XBUTTON1,
    _XBUTTON2,
    _user32,
    activate_window,
    send_mouse_event,
    send_mouse_wheel_event,
    smooth_move_to,
)

if _user32 is not None:
    _user32.GetForegroundWindow.restype = wintypes.HWND


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

    def place_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """直接设置系统光标位置，不产生鼠标移动输入。"""
        self._activate_target()
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"放置 {label}: ({screen_x}, {screen_y})")
        _user32.SetCursorPos(int(screen_x), int(screen_y))

    def move_screen(
        self,
        screen_x: int,
        screen_y: int,
        poi_name: str = "",
        duration: float | None = None,
    ):
        """根据当前光标位置计算相对位移，并分步移动到目标坐标。"""
        self._activate_target()
        point = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(point))
        dx = int(screen_x) - int(point.x)
        dy = int(screen_y) - int(point.y)
        move_dur = (
            random.uniform(*self.mouse_move_duration)
            if duration is None else max(float(duration), 0.0)
        )
        self._send_relative_steps(dx, dy, move_dur)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(
            f"移动到 {label}: ({screen_x}, {screen_y}), "
            f"delta=({dx},{dy}), duration={move_dur:.3f}s")

    def move_relative(
        self,
        delta_x: int,
        delta_y: int,
        poi_name: str = "",
        duration: float | None = None,
    ):
        """通过 SendInput 注入相对鼠标位移。"""
        self._activate_target()
        move_dur = (
            random.uniform(*self.mouse_move_duration)
            if duration is None else max(float(duration), 0.0)
        )
        self._send_relative_steps(int(delta_x), int(delta_y), move_dur)
        label = f"({poi_name})" if poi_name else ""
        logger.debug(
            f"相对移动 {label}: ({delta_x:+d}, {delta_y:+d}), "
            f"duration={move_dur:.3f}s")

    @staticmethod
    def _distribute(total: int, steps: int) -> list[int]:
        """把有符号整数位移无损、均匀地分配到多个步骤。"""
        if steps <= 1:
            return [total]
        values: list[int] = []
        previous = 0
        for index in range(1, steps + 1):
            current = round(total * index / steps)
            values.append(current - previous)
            previous = current
        return values

    def _send_relative_steps(self, dx: int, dy: int, duration: float):
        # 供 move_relative（普通 DSL move by）使用，步长与 smooth_move_to
        # 对齐（10ms/步）。注意 replay_input_trace 回放高精度轨迹时不走
        # 这个方法——它有自己独立的、按绝对截止时间调度的发送循环。
        steps = max(int(duration / 0.01), 1)
        dx_steps = self._distribute(dx, steps)
        dy_steps = self._distribute(dy, steps)
        delay = duration / steps if duration > 0 else 0
        for step_dx, step_dy in zip(dx_steps, dy_steps, strict=True):
            send_mouse_event(_MOUSEEVENTF_MOVE, step_dx, step_dy)
            if delay:
                time.sleep(delay)

    def replay_input_trace(
        self,
        trace: InputTrace,
        *,
        canvas_width: int,
        canvas_height: int,
        stop_check: Callable[[], bool],
        pause_event: threading.Event | None = None,
    ) -> None:
        """以绝对截止时间回放完整输入轨迹，避免逐条 DSL 与相对 sleep 漂移。"""
        self._activate_target()
        start_ns = time.perf_counter_ns()
        paused_ns = 0
        source_x = source_y = 0
        sent_x = sent_y = 0
        held_buttons: set[str] = set()
        held_keys: set[str] = set()
        # 值是 (dwFlags, mouseData) 二元组——XBUTTONDOWN/XBUTTONUP（侧键）
        # 靠 mouseData 区分 XBUTTON1/XBUTTON2，其余键该字段固定为 0，
        # 统一走同一套 send_mouse_event(flag, mouse_data=...) 调用。
        button_flags = {
            ("left", True): (_MOUSEEVENTF_LEFTDOWN, 0),
            ("left", False): (_MOUSEEVENTF_LEFTUP, 0),
            ("right", True): (_MOUSEEVENTF_RIGHTDOWN, 0),
            ("right", False): (_MOUSEEVENTF_RIGHTUP, 0),
            ("middle", True): (_MOUSEEVENTF_MIDDLEDOWN, 0),
            ("middle", False): (_MOUSEEVENTF_MIDDLEUP, 0),
            ("x1", True): (_MOUSEEVENTF_XDOWN, _XBUTTON1),
            ("x1", False): (_MOUSEEVENTF_XUP, _XBUTTON1),
            ("x2", True): (_MOUSEEVENTF_XDOWN, _XBUTTON2),
            ("x2", False): (_MOUSEEVENTF_XUP, _XBUTTON2),
        }

        try:
            for event in trace.events:
                if stop_check():
                    break
                # 内层循环：deadline 等待期间也可能被暂停打断（长间隔事件
                # 之间常有数秒空档，不能只在等待开始前查一次 pause_event），
                # 打断后回到暂停阻塞分支、累计暂停时长、用最新 paused_ns
                # 重新算 deadline 再继续等，直到真正到达或收到停止信号。
                while True:
                    if pause_event is not None and not pause_event.is_set():
                        pause_started = time.perf_counter_ns()
                        while not pause_event.wait(0.01):
                            if stop_check():
                                return
                        paused_ns += time.perf_counter_ns() - pause_started
                        continue

                    deadline = start_ns + paused_ns + event.at_us * 1000
                    interrupted = self._wait_trace_deadline(
                        deadline, stop_check, pause_event)
                    if stop_check() or not interrupted:
                        break
                if stop_check():
                    break

                if event.kind == "move":
                    dx, dy = (int(event.values[0]), int(event.values[1]))
                    source_x += dx
                    source_y += dy
                    target_x = round(
                        source_x * canvas_width / trace.source_width)
                    target_y = round(
                        source_y * canvas_height / trace.source_height)
                    out_dx, out_dy = target_x - sent_x, target_y - sent_y
                    sent_x, sent_y = target_x, target_y
                    if out_dx or out_dy:
                        send_mouse_event(
                            _MOUSEEVENTF_MOVE | _MOUSEEVENTF_MOVE_NOCOALESCE,
                            out_dx,
                            out_dy,
                        )
                elif event.kind == "button":
                    button = str(event.values[0])
                    is_down = bool(event.values[1])
                    flag_pair = button_flags.get((button, is_down))
                    if flag_pair is None:
                        logger.warning(f"忽略未知轨迹鼠标键: {button}")
                        continue
                    flag, mouse_data = flag_pair
                    send_mouse_event(flag, mouse_data=mouse_data)
                    if is_down:
                        held_buttons.add(button)
                    else:
                        held_buttons.discard(button)
                elif event.kind == "wheel":
                    send_mouse_wheel_event(int(event.values[0]))
                elif event.kind == "key":
                    key = normalize_key(str(event.values[0]))
                    is_down = bool(event.values[1])
                    vk, scan = key_to_vk_scan(key)
                    flags = KEYEVENTF_SCANCODE
                    if is_extended_key(key):
                        flags |= KEYEVENTF_EXTENDEDKEY
                    if not is_down:
                        flags |= KEYEVENTF_KEYUP
                    send_keyboard_input(vk, scan, flags)
                    if is_down:
                        held_keys.add(key)
                    else:
                        held_keys.discard(key)
        finally:
            # 停止、异常或暂停退出都不能把游戏按键留在按下状态。
            for button in held_buttons:
                flag_pair = button_flags.get((button, False))
                if flag_pair is not None:
                    flag, mouse_data = flag_pair
                    send_mouse_event(flag, mouse_data=mouse_data)
            for key in held_keys:
                vk, scan = key_to_vk_scan(key)
                flags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
                if is_extended_key(key):
                    flags |= KEYEVENTF_EXTENDEDKEY
                send_keyboard_input(vk, scan, flags)

    @staticmethod
    def _wait_trace_deadline(
        deadline_ns: int,
        stop_check: Callable[[], bool],
        pause_event: threading.Event | None = None,
    ) -> bool:
        """粗粒度休眠后短暂自旋，以绝对时钟达到亚 10ms 调度。

        单次 sleep 上限 50ms：长间隔事件之间的等待可达数秒，不能一次
        睡掉整个区间，否则 stop_check / pause_event 在此期间形同虚设。
        pause_event 变为 clear 时提前返回 True（"被暂停打断"），调用方
        据此回到暂停阻塞分支、用最新累计暂停时长重新算 deadline 再继续
        等——不这样处理的话，暂停请求会被这里的长等待吞掉，held 的
        按键/鼠标键会在暂停后继续按原计划触发一段时间。到达 deadline
        正常返回 False。
        """
        while not stop_check():
            if pause_event is not None and not pause_event.is_set():
                return True
            remaining_ns = deadline_ns - time.perf_counter_ns()
            if remaining_ns <= 0:
                return False
            if remaining_ns > 2_000_000:
                sleep_s = min((remaining_ns - 1_000_000) / 1_000_000_000, 0.05)
                time.sleep(sleep_s)
            elif remaining_ns > 200_000:
                time.sleep(0)
        return False

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
            if _user32.GetForegroundWindow() == self.target_hwnd:
                return
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
