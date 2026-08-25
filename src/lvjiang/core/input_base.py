"""输入后端抽象基类

定义所有输入后端（SendInput / PostMessage / ADB shell input）的统一公开面。
工作流与上层代码仅依赖此抽象，不感知具体实现。

子类职责：
- SendInputInput：移动真实光标，需窗口在前台
- PostMessageInput：向目标窗口投递鼠标消息，不移动光标
- AdbInput：通过 adb shell input 命令注入触摸事件
"""

from abc import ABC, abstractmethod

from ..core.config import InputSimConfig


class InputBackend(ABC):
    """输入后端抽象基类

    公开接口：
    - click_screen(x, y, poi_name)：点击屏幕/设备坐标
    - place_screen(x, y, poi_name)：直接设置鼠标位置（不产生移动过程）
    - move_screen(x, y, poi_name, duration)：产生鼠标移动并到达屏幕坐标
    - move_relative(dx, dy, poi_name, duration)：产生有符号的相对鼠标位移
    - drag_screen(from_x, from_y, to_x, to_y, ...)：从起点拖拽到终点
    - key_down(key)：按下按键（仅 keydown，不释放）
    - key_up(key)：释放按键

    兼容属性（供 run_control 访问）：
    - background_mode: bool（ADB 恒 True）
    - target_hwnd: int | None（ADB 恒 None）

    延迟/抖动参数（由 InputSimConfig 注入，所有子类共享）：
    - before_click_wait / after_click_wait：点击前后延迟范围
    - mouse_move_duration：鼠标/触摸移动时长范围
    - click_random_offset：坐标随机偏移像素
    - region_jitter_ratio：区域中心抖动比例
    """

    # ─── 延迟/抖动参数（子类 __init__ 由 InputSimConfig 注入）──────────
    before_click_wait: tuple[float, float]
    after_click_wait: tuple[float, float]
    mouse_move_duration: tuple[float, float]
    click_random_offset: int
    region_jitter_ratio: float

    # ─── 兼容属性 ────────────────────────────────────────────────
    background_mode: bool
    target_hwnd: int | None

    @abstractmethod
    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = "",
                     *, pre_delay: tuple[float, float] | None = None,
                        post_delay: tuple[float, float] | None = None,
                        button: str = "left"):
        """点击指定坐标（带随机偏移 + before/after 延迟）

        Args:
            pre_delay: 点击前延迟范围。None=使用默认 before_click_wait，(0,0)=不延迟。
            post_delay: 点击后延迟范围。None=使用默认 after_click_wait，(0,0)=不延迟。
            button: 鼠标键，left/right/middle/x1/x2。触屏类后端（ADB/设备端）
                没有非左键的概念，非 left 时忽略该参数、按普通点击处理并记警告。
        """

    def mouse_button(self, button: str, pressed: bool) -> None:
        """在当前指针位置发送一个原始鼠标键 down/up 事件。

        这是桌面宏录制回放能力；不具备鼠标概念的后端应明确报错，不能把
        down/up 偷换成一次完整 tap。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 不支持原始鼠标键事件")

    @abstractmethod
    def place_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """直接设置鼠标位置，不产生移动过程。

        SendInput：SetCursorPos 绝对放置系统光标。
        PostMessage：投递一次目标位置 WM_MOUSEMOVE。
        ADB/设备端：不支持。
        """

    @abstractmethod
    def move_screen(
        self,
        screen_x: int,
        screen_y: int,
        poi_name: str = "",
        duration: float | None = None,
    ):
        """通过鼠标移动输入到达指定坐标（不点击）。

        SendInput：根据当前光标位置计算相对位移并分步注入。
        PostMessage：WM_MOUSEMOVE 向目标窗口投递鼠标移动消息。
        ADB：不支持，空操作并记录警告。
        """

    @abstractmethod
    def move_relative(
        self,
        delta_x: int,
        delta_y: int,
        poi_name: str = "",
        duration: float | None = None,
    ):
        """产生相对鼠标位移，不依赖系统光标绝对位置。

        SendInput：逐步注入 MOUSEEVENTF_MOVE 相对位移。
        其他后端：不支持，空操作并记录警告。
        """

    @abstractmethod
    def scroll_screen(
        self,
        screen_x: int,
        screen_y: int,
        direction: str = "down",
        amount: int = 1,
        poi_name: str = "",
    ):
        """在指定坐标位置滚动鼠标滚轮

        通常与 move_screen 配套使用：先移动光标到目标区域，再执行滚动。

        Args:
            screen_x: 屏幕 x 坐标
            screen_y: 屏幕 y 坐标
            direction: 滚动方向，"up" 或 "down"
            amount: 滚动格数（默认 1）
            poi_name: 日志标签

        SendInput：MOUSEEVENTF_WHEEL 发送滚轮事件。
        PostMessage：WM_MOUSEWHEEL 向目标窗口投递滚轮消息。
        ADB：用短距离 input swipe 模拟滚动。
        """

    @abstractmethod
    def drag_screen(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        poi_name: str = "",
        duration: float | tuple[float, float] | None = None,
        hold: float | None = None,
        *, pre_delay: tuple[float, float] | None = None,
           post_delay: tuple[float, float] | None = None,
    ):
        """从起点拖拽到终点

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达终点后按住不放的时长（秒）。None 表示不按。
            pre_delay: 拖拽前延迟范围。None=使用默认 before_click_wait，(0,0)=不延迟。
            post_delay: 拖拽后延迟范围。None=使用默认 after_click_wait，(0,0)=不延迟。
        """

    @abstractmethod
    def key_down(self, key: str) -> None:
        """按下按键（仅 keydown，不释放）

        Backend 不负责状态管理，只负责发送。
        key 参数已经是标准化后的键名（由调用方 normalize_key 处理）。
        """

    @abstractmethod
    def key_up(self, key: str) -> None:
        """释放按键

        Backend 不负责状态管理，只负责发送。
        key 参数已经是标准化后的键名。
        """

    @staticmethod
    def _inject_input_sim(instance, input_sim: InputSimConfig | None):
        """工具方法：将 InputSimConfig 的输入模拟参数注入到子类实例

        子类 __init__ 可调用此方法统一注入，避免重复代码。
        """
        cfg = input_sim or InputSimConfig()
        instance.before_click_wait = cfg.before_click_wait
        instance.after_click_wait = cfg.after_click_wait
        instance.mouse_move_duration = cfg.mouse_move_duration
        instance.click_random_offset = cfg.click_random_offset
        instance.region_jitter_ratio = cfg.region_jitter_ratio
