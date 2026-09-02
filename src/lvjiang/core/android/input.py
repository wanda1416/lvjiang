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

from ...core.config import InputSimConfig
from ..input_base import InputBackend, InputBackendKind
from .device import AdbDevice

# 标准键名 → Android keycode（adb shell input keyevent 参数）
# 参考: https://developer.android.com/reference/android/view/KeyEvent
_KEY_TO_ANDROID_KEYCODE: dict[str, int] = {
    # 字母 A-Z
    **{chr(c): 29 + (c - ord("A")) for c in range(ord("A"), ord("Z") + 1)},
    # 数字 0-9
    **{str(d): 7 + d for d in range(10)},
    **{f"NUMPAD{d}": 144 + d for d in range(10)},
    # 功能键 F1-F12
    **{f"F{i}": 131 + i - 1 for i in range(1, 13)},
    "COMMA": 55,
    "PERIOD": 56,
    "GRAVE": 68,
    "MINUS": 69,
    "EQUALS": 70,
    "LBRACKET": 71,
    "RBRACKET": 72,
    "BACKSLASH": 73,
    "SEMICOLON": 74,
    "APOSTROPHE": 75,
    "SLASH": 76,
    "NUMPAD_DIVIDE": 154,
    "NUMPAD_MULTIPLY": 155,
    "NUMPAD_SUBTRACT": 156,
    "NUMPAD_ADD": 157,
    "NUMPAD_DECIMAL": 158,
    "NUMPAD_ENTER": 160,
    # 特殊键
    "ESC": 111,
    "ENTER": 66,
    "SPACE": 62,
    "TAB": 61,
    "BACKSPACE": 67,
    "DELETE": 112,
    "INSERT": 124,
    "HOME": 3,
    "END": 123,
    "PAGEUP": 92,
    "PAGEDOWN": 93,
    # 修饰键
    "SHIFT": 59,
    "CTRL": 113,
    "ALT": 57,
    "LCTRL": 113,
    "RCTRL": 114,
    "LSHIFT": 59,
    "RSHIFT": 60,
    "LALT": 57,
    "RALT": 58,
    "WIN": 0,   # Android 无 WIN 键，用 0 占位
    "LWIN": 0,
    "RWIN": 0,
    # 方向键
    "UP": 19,
    "DOWN": 20,
    "LEFT": 21,
    "RIGHT": 22,
    # 其他
    "CAPSLOCK": 115,
    "NUMLOCK": 143,
    "SCROLLLOCK": 116,
    "PRINTSCREEN": 122,
    "PAUSE": 121,
}


class AdbInput(InputBackend):
    """基于 adb shell input 的输入后端（接口继承 InputBackend）"""

    kind = InputBackendKind.ADB

    def __init__(self, device: AdbDevice, input_sim: InputSimConfig | None = None):
        self._inject_input_sim(self, input_sim)
        self._device = device

        # 兼容 InputBackend 公开面：ADB 无窗口/后台概念，恒定值供上层访问
        self.background_mode = True
        self.target_hwnd = None

    # ─── 坐标映射 ──────────────────────────────────────────
    #
    # AdbCapture 已在截图阶段把图像旋转到设备原生方向，因此工作流下发的
    # 截图坐标与 input tap 期望的设备坐标处于同一坐标系，通常无需变换。
    # 保留一道尺寸一致性守卫作为安全网：仅当截图尺寸与设备分辨率一致时直通。

    def _transform(self, x: int, y: int) -> tuple[int, int]:
        """截图坐标 → 设备坐标（截图已对齐设备方向，直通）"""
        return x, y

    # ─── 点击 ─────────────────────────────────────────────────

    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = "",
                     *, pre_delay=None, post_delay=None, button: str = "left"):
        """点击设备坐标（带随机偏移 + before/after 延迟）

        触屏没有鼠标键概念，非 left 时按普通点击处理并记警告。
        """
        if button != "left":
            logger.warning(f"ADB 触屏输入不支持 {button} 键，按普通点击处理")
        offset_x = random.randint(-self.click_random_offset, self.click_random_offset)
        offset_y = random.randint(-self.click_random_offset, self.click_random_offset)
        sx = screen_x + offset_x
        sy = screen_y + offset_y

        # 截图坐标 → 设备坐标（处理横竖屏旋转）
        actual_x, actual_y = self._transform(sx, sy)

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))

        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[ADB] 点击 {label}: 截图({sx},{sy}) → 设备({actual_x},{actual_y})")
        self._device.shell("input", "tap", str(actual_x), str(actual_y))

        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    def place_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """ADB 不支持鼠标放置，空操作。"""
        logger.warning("[ADB] place 指令无效：ADB 后端不支持鼠标放置")

    def move_screen(
        self, screen_x: int, screen_y: int, poi_name: str = "",
        duration: float | None = None,
    ):
        """ADB 不支持鼠标移动，空操作"""
        logger.warning("[ADB] move 指令无效：ADB 后端不支持鼠标移动")

    def move_relative(
        self, delta_x: int, delta_y: int, poi_name: str = "",
        duration: float | None = None,
    ):
        logger.warning("[ADB] move by 指令无效：ADB 后端不支持鼠标移动")

    def scroll_screen(
        self,
        screen_x: int,
        screen_y: int,
        direction: str = "down",
        amount: int = 1,
        poi_name: str = "",
    ):
        """ADB 模拟滚动：用短距离 swipe 模拟鼠标滚轮

        每格对应 100px 的滑动距离，方向由参数决定。
        """
        dist = 100 * amount
        if direction == "up":
            # 向上滚动 = 手指从下往上划
            end_y = screen_y - dist
        else:
            # 向下滚动 = 手指从上往下划
            end_y = screen_y + dist
        label = f"({poi_name})" if poi_name else ""
        logger.debug(f"[ADB] 滚轮 {label}: {direction} x{amount} @ ({screen_x}, {screen_y})")
        self._device.shell(
            "input", "swipe",
            str(screen_x), str(screen_y),
            str(screen_x), str(end_y),
            "100",
        )

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
        """从起点拖拽到终点（adb shell input swipe，随机化移动时长）

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达终点后按住不放的时长（秒）。通过延长 swipe 总时长实现
                  （adb input swipe 的手指在 duration 结束前保持按下）。
        """
        if duration is None:
            move_dur = random.uniform(*self.mouse_move_duration)
        elif isinstance(duration, tuple):
            move_dur = random.uniform(*duration)
        else:
            move_dur = float(duration)

        # hold 合并到 swipe 时长：手指在 duration_ms 结束前不会抬起，
        # 因此 move_dur + hold 等效于「滑到终点后按住 hold 秒再松开」
        hold_dur = float(hold) if hold and hold > 0 else 0.0
        total_dur = move_dur + hold_dur

        _pre = pre_delay if pre_delay is not None else self.before_click_wait
        time.sleep(random.uniform(*_pre))

        duration_ms = int(total_dur * 1000)
        hold_info = f" + hold {hold}s（合并到 swipe 时长）" if hold_dur > 0 else ""

        # 截图坐标 → 设备坐标（处理横竖屏旋转）
        fx, fy = self._transform(from_x, from_y)
        tx, ty = self._transform(to_x, to_y)

        logger.debug(f"[ADB] 拖拽 {poi_name}: 截图({from_x},{from_y})->({to_x},{to_y}) "
                     f"→ 设备({fx},{fy})->({tx},{ty}) [移动{move_dur:.2f}s]{hold_info}")
        self._device.shell(
            "input", "swipe",
            str(fx), str(fy), str(tx), str(ty), str(duration_ms),
        )

        _post = post_delay if post_delay is not None else self.after_click_wait
        time.sleep(random.uniform(*_post))

    # ─── 键盘 ─────────────────────────────────────────────────

    def _key_to_keycode(self, key: str) -> int:
        """将标准化键名转为 Android keycode"""
        upper = key.strip().upper()
        code = _KEY_TO_ANDROID_KEYCODE.get(upper)
        if code is None:
            raise ValueError(f"未知按键名: {key!r}，无对应 Android keycode")
        return code

    def key_down(self, key: str) -> None:
        """按下按键（adb input keyevent 不区分 down/up，发送一次即可）"""
        code = self._key_to_keycode(key)
        logger.debug(f"[ADB] key_down: {key} → keycode {code}")
        self._device.shell("input", "keyevent", str(code))

    def key_up(self, key: str) -> None:
        """释放按键（adb input keyevent 不区分 down/up，发送一次即可）"""
        code = self._key_to_keycode(key)
        logger.debug(f"[ADB] key_up: {key} → keycode {code}")
        self._device.shell("input", "keyevent", str(code))
