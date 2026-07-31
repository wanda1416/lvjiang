"""桌面端（Windows）输入/截图后端

提供基于 Win32 API 的输入与截图能力：
- SendInputInput：移动真实光标，需窗口在前台
- PostMessageInput：向目标窗口投递鼠标消息，不移动光标
- DesktopCapture：基于 mss 的桌面窗口截图
- list_visible_windows：枚举所有可见窗口

工厂函数：
- create_input_backend(mode, input_sim, hwnd)：按 mode 创建输入后端
- create_capture_backend()：创建桌面截图后端
"""

from ...config import InputSimConfig
from ..input_base import InputBackend
from .capture import DesktopCapture
from .post_message import PostMessageInput
from .send_input import SendInputInput
from .win32_util import list_visible_windows


def create_input_backend(
    mode: str = "post",
    input_sim: InputSimConfig | None = None,
    hwnd: int | None = None,
) -> InputBackend:
    """创建桌面端输入后端

    Args:
        mode: "send"（SendInput，前台）或 "post"（PostMessage，后台，默认）
        input_sim: 输入模拟参数
        hwnd: 目标窗口句柄（PostMessage 模式使用）

    Returns:
        SendInputInput 或 PostMessageInput 实例
    """
    if mode == "send":
        return SendInputInput(input_sim=input_sim)
    elif mode == "post":
        return PostMessageInput(input_sim=input_sim, hwnd=hwnd)
    else:
        raise ValueError(f"未知的桌面输入模式: {mode!r}，可选: 'send', 'post'")


def create_capture_backend() -> DesktopCapture:
    """创建桌面端截图后端

    Returns:
        DesktopCapture 实例
    """
    return DesktopCapture()


__all__ = [
    "SendInputInput",
    "PostMessageInput",
    "DesktopCapture",
    "list_visible_windows",
    "create_input_backend",
    "create_capture_backend",
]
