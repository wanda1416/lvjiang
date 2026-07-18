"""ADB 设备后端 - 通过 adb 直连手机截图/注入，替代投屏窗口方案

模块结构：
- device.py     : AdbDevice，adb 可执行/serial 解析、shell/forward/push、设备属性
- capture.py    : AdbCapture，继承 CaptureBackend，基于 adb exec-out screencap -p
- input.py      : AdbInput，继承 InputBackend，基于 adb shell input tap/swipe

工厂函数：
- create_input_backend(device, delay_config)：创建 ADB 输入后端
- create_capture_backend(device)：创建 ADB 截图后端
"""

from ...config import DelayConfig
from ..input_base import InputBackend
from ..capture_base import CaptureBackend
from .device import AdbDevice, list_adb_devices
from .input import AdbInput
from .capture import AdbCapture


def create_input_backend(
    device: AdbDevice,
    delay_config: DelayConfig | None = None,
) -> InputBackend:
    """创建 ADB 输入后端

    Args:
        device: AdbDevice 实例
        delay_config: 延迟参数

    Returns:
        AdbInput 实例
    """
    return AdbInput(device=device, delay_config=delay_config)


def create_capture_backend(device: AdbDevice) -> CaptureBackend:
    """创建 ADB 截图后端

    Args:
        device: AdbDevice 实例

    Returns:
        AdbCapture 实例
    """
    return AdbCapture(device=device)


__all__ = [
    "AdbDevice",
    "list_adb_devices",
    "AdbInput",
    "AdbCapture",
    "create_input_backend",
    "create_capture_backend",
]
