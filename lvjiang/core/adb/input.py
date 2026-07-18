"""ADB 输入后端 - 通过 adb shell input 注入触摸事件

直接调用设备原生 input 命令，绕过 minitouch 对高版本 Android 的兼容性问题。
每次操作走一次 adb 子进程，延迟约 50–150ms/次，适合工作流场景（非低延迟流）。

上层（工作流引擎、run_control）无需改动：
- click_screen(x, y, poi) → adb shell input tap x y
- drag_screen(...) → adb shell input swipe x1 y1 x2 y2 duration_ms
- background_mode 恒 True、target_hwnd 恒 None（供 run_control 访问）
"""

import random
import time

from loguru import logger

from ...config import DelayConfig
from ..input_base import InputBackend
from .device import AdbDevice


class AdbInput(InputBackend):
    """基于 adb shell input 的输入后端（接口继承 InputBackend）"""

    def __init__(self, device: AdbDevice, delay_config: DelayConfig | None = None):
        self._inject_delay_config(self, delay_config)
        self._device = device

        # 兼容 InputBackend 公开面：ADB 无窗口/后台概念，恒定值供上层访问
        self.background_mode = True
        self.target_hwnd = None

    # ─── 点击 ─────────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """点击设备坐标（带随机偏移 + before/after 延迟）"""
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        actual_x = screen_x + offset_x
        actual_y = screen_y + offset_y

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[ADB] 点击 {label}: ({actual_x}, {actual_y}) [偏移: {offset_x:+d}, {offset_y:+d}]")
        self._device.shell("input", "tap", str(actual_x), str(actual_y))

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
        """从起点拖拽到终点（adb shell input swipe，随机化移动时长）

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达终点后按住不放的时长（秒）。adb input swipe 不支持 hold，仅日志提示。
        """
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)

        pre_delay = random.uniform(*self.before_click_wait)
        time.sleep(pre_delay)

        duration_ms = int(move_dur * 1000)
        hold_info = f" + hold {hold}s（adb 不支持）" if hold and hold > 0 else ""
        logger.debug(f"[ADB] 拖拽 {poi_name}: ({from_x},{from_y}) -> ({to_x},{to_y}) "
                     f"[{move_dur:.2f}s]{hold_info}")
        self._device.shell(
            "input", "swipe",
            str(from_x), str(from_y), str(to_x), str(to_y), str(duration_ms),
        )

        # adb input swipe 不支持 hold，若需要则额外 sleep 模拟
        if hold is not None and hold > 0:
            time.sleep(float(hold))

        post_delay = random.uniform(*self.after_click_wait)
        time.sleep(post_delay)
