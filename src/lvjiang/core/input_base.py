"""输入后端抽象基类

定义所有输入后端（SendInput / PostMessage / ADB shell input）的统一公开面。
工作流与上层代码仅依赖此抽象，不感知具体实现。

子类职责：
- SendInputInput：移动真实光标，需窗口在前台
- PostMessageInput：向目标窗口投递鼠标消息，不移动光标
- AdbInput：通过 adb shell input 命令注入触摸事件
"""

from abc import ABC, abstractmethod

from ..config import InputSimConfig


class InputBackend(ABC):
    """输入后端抽象基类

    公开接口：
    - click_screen(x, y, poi_name)：点击屏幕/设备坐标
    - drag_screen(from_x, from_y, to_x, to_y, ...)：从起点拖拽到终点

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
    def click_screen(self, screen_x: int, screen_y: int, poi_name: str = ""):
        """点击指定坐标（带随机偏移 + before/after 延迟）"""

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
    ):
        """从起点拖拽到终点

        Args:
            duration: 移动时长（秒）。单值固定，二元组则范围内随机。None 使用默认 mouse_move_duration。
            hold: 到达终点后按住不放的时长（秒）。None 表示不按。
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
