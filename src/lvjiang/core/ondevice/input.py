"""设备端输入后端

两条通道：
  A11yInput   主通道，无障碍 dispatchGesture
  ShellInput  可选通道，input tap/swipe 经 Shizuku（命令与 PC 端 AdbInput 同源）

主通道选无障碍而不是 Shizuku：后者必须由 adb 引导启动，手机重启一次就失效，
要用户重新配对无线调试；无障碍开关开一次即长期有效。

继承 InputBackend：config.py 已去 pydantic（dataclass 实现），基类与 InputSimConfig
在设备端可直接导入，延迟/拟人化参数由 InputSimConfig 统一注入，与 PC 端同源。
"""

import random
import time

from ...core.config import InputSimConfig
from ..input_base import InputBackend
from . import a11y, shell


class _GestureInput(InputBackend):
    """点击/拖拽的共同逻辑

    随机偏移、前后延迟、拖拽时长换算这些是与通道无关的策略，两条通道必须表现
    一致，否则换通道会连带改变操作节奏。子类只实现 _tap / _swipe 两个原语。
    """

    #: 日志前缀，用于在报告里区分实际走的哪条通道
    name = "_GestureInput"

    def __init__(self, input_sim: InputSimConfig | None = None):
        # 供 run_control 访问：设备端无窗口概念，与 AdbInput 取同样的恒定值
        self.background_mode = True
        self.target_hwnd = None

        self._inject_input_sim(self, input_sim)

    def _tap(self, x: int, y: int) -> None:
        raise NotImplementedError

    def _swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        raise NotImplementedError

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = "",
                     *, pre_delay=None, post_delay=None):
        """点击设备坐标（带随机偏移 + before/after 延迟）"""
        sx = screen_x + random.randint(-self.click_random_offset, self.click_random_offset)
        sy = screen_y + random.randint(-self.click_random_offset, self.click_random_offset)

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))
        label = f"({poi_name})" if poi_name else ""
        print(f"[{self.name}] 点击 {label}: ({sx},{sy})")
        self._tap(sx, sy)
        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    def move_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """设备端不支持鼠标移动，空操作"""
        print(f"[{self.name}] move 指令无效：设备端输入后端不支持鼠标移动")

    def scroll_screen(
        self,
        screen_x: int,
        screen_y: int,
        direction: str = "down",
        amount: int = 1,
        poi_name: str = "",
    ):
        """设备端不支持鼠标滚轮，空操作"""
        print(f"[{self.name}] scroll 指令无效：设备端输入后端不支持鼠标滚轮")

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
        """从起点拖拽到终点（随机化移动时长）

        hold 合并进总时长：手指在 duration_ms 结束前不会抬起，因此等效于
        「滑到终点后按住 hold 秒」——与 PC 端 AdbInput 同一套处理。
        """
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)

        total_ms = int((move_dur + (float(hold) if hold and hold > 0 else 0.0)) * 1000)

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))
        print(f"[{self.name}] 拖拽 {poi_name}: ({from_x},{from_y})->({to_x},{to_y}) {total_ms}ms")
        self._swipe(from_x, from_y, to_x, to_y, total_ms)
        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    def key_down(self, key: str) -> None:
        """设备端不支持独立键盘按下"""
        raise NotImplementedError("设备端输入后端不支持 key_down")

    def key_up(self, key: str) -> None:
        """设备端不支持独立键盘释放"""
        raise NotImplementedError("设备端输入后端不支持 key_up")


class A11yInput(_GestureInput):
    """基于无障碍 dispatchGesture 的输入后端（主通道）

    与 shell 的 input 命令有一处本质差别：dispatchGesture 是同步等回调的，
    手势真正落地后才返回，所以紧接着截图不会截到「还没生效」的画面。
    """

    name = "A11yInput"

    def _tap(self, x: int, y: int) -> None:
        if not a11y.tap(x, y):
            print(f"[{self.name}] 点击未成功（无障碍开关未开？）")

    def _swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        if not a11y.swipe(x1, y1, x2, y2, duration_ms):
            print(f"[{self.name}] 拖拽未成功（无障碍开关未开？）")


class ShellInput(_GestureInput):
    """基于 ShellBridge input 的输入后端（可选通道）"""

    name = "ShellInput"

    def _tap(self, x: int, y: int) -> None:
        shell.tap(x, y)

    def _swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        shell.swipe(x1, y1, x2, y2, duration_ms)
