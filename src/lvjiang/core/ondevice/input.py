"""设备端输入后端

两条通道：
  A11yInput   主通道，无障碍 dispatchGesture
  ShellInput  可选通道，input tap/swipe 经 Shizuku（命令与 PC 端 AdbInput 同源）

主通道选无障碍而不是 Shizuku：后者必须由 adb 引导启动，手机重启一次就失效，
要用户重新配对无线调试；无障碍开关开一次即长期有效。

刻意不继承 InputBackend：那个基类会 `from ..config import DelayConfig`，而
config.py 依赖 pydantic —— 设备端依赖矩阵里还没有它。等 Phase 2 把配置层搬上设备
（届时必须解决 pydantic 的 Android wheel 问题）再改为继承，接口签名此处已按基类
对齐，改动只是加一行 class 声明。

延迟与随机偏移的语义与 PC 端一致（拟人化，避免固定节奏），但参数暂用写死的默认值，
同样等 DelayConfig 上设备后再注入。
"""

import random
import time

from . import a11y, shell

# 与 config.DelayConfig 的默认值保持一致；Phase 2 接上配置后删除
_BEFORE_CLICK = (0.05, 0.15)
_AFTER_CLICK = (0.1, 0.25)
_MOVE_DURATION = (0.3, 0.6)
_RANDOM_OFFSET = 3


class _GestureInput:
    """点击/拖拽的共同逻辑（签名对齐 InputBackend）

    随机偏移、前后延迟、拖拽时长换算这些是与通道无关的策略，两条通道必须表现
    一致，否则换通道会连带改变操作节奏。子类只实现 _tap / _swipe 两个原语。
    """

    #: 日志前缀，用于在报告里区分实际走的哪条通道
    name = "_GestureInput"

    def __init__(self):
        # 供 run_control 访问：设备端无窗口概念，与 AdbInput 取同样的恒定值
        self.background_mode = True
        self.target_hwnd = None

        self.before_click_wait = _BEFORE_CLICK
        self.after_click_wait = _AFTER_CLICK
        self.mouse_move_duration = _MOVE_DURATION
        self.click_random_offset = _RANDOM_OFFSET

    def _tap(self, x: int, y: int) -> None:
        raise NotImplementedError

    def _swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        raise NotImplementedError

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """点击设备坐标（带随机偏移 + before/after 延迟）"""
        sx = screen_x + random.randint(-self.click_random_offset, self.click_random_offset)
        sy = screen_y + random.randint(-self.click_random_offset, self.click_random_offset)

        time.sleep(random.uniform(*self.before_click_wait))
        label = f"({poi_name})" if poi_name else ""
        print(f"[{self.name}] 点击 {label}: ({sx},{sy})")
        self._tap(sx, sy)
        time.sleep(random.uniform(*self.after_click_wait))

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

        time.sleep(random.uniform(*self.before_click_wait))
        print(f"[{self.name}] 拖拽 {poi_name}: ({from_x},{from_y})->({to_x},{to_y}) {total_ms}ms")
        self._swipe(from_x, from_y, to_x, to_y, total_ms)
        time.sleep(random.uniform(*self.after_click_wait))


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
