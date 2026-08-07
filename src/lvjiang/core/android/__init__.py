"""Android 设备后端 — 统一封装 ADB 截图/输入 + scrcpy 视频流

模块结构：
- device.py        : AdbDevice，adb 可执行/serial 解析、shell/forward/push、设备属性
- input.py         : AdbInput，继承 InputBackend，基于 adb shell input tap/swipe
- adb_capture.py   : AdbCapture，继承 CaptureBackend，基于 adb exec-out screencap -p
- scrcpy_capture.py: AndroidStreamCapture，继承 CaptureBackend，基于 scrcpy 4.1 server H.264 视频流
- bootstrap.py     : 已废弃（保留为兼容桩）

工厂函数：
- create_input_backend(device, input_sim)：创建 ADB 输入后端
- create_capture_backend(device, method)：创建截图后端（screencap 或 scrcpy）
"""

from ...core.config import InputSimConfig
from ..capture_base import CaptureBackend
from ..input_base import InputBackend
from .adb_capture import AdbCapture
from .device import AdbDevice, list_adb_devices
from .input import AdbInput
from .scrcpy_capture import AndroidStreamCapture
from .wireless import scan_and_connect_wireless


def create_input_backend(
    device: AdbDevice,
    input_sim: InputSimConfig | None = None,
) -> InputBackend:
    """创建 ADB 输入后端

    Args:
        device: AdbDevice 实例
        input_sim: 输入模拟参数

    Returns:
        AdbInput 实例
    """
    return AdbInput(device=device, input_sim=input_sim)


def create_capture_backend(device: AdbDevice, method: str = "screencap") -> CaptureBackend:
    """创建截图后端

    Args:
        device: AdbDevice 实例
        method: 截图方式，"screencap"（默认，adb exec-out screencap -p）
                或 "scrcpy"（scrcpy 4.1 server H.264 视频流，推送式取帧）

    Returns:
        AdbCapture 或 AndroidStreamCapture 实例
    """
    if method == "scrcpy":
        return AndroidStreamCapture(device=device)
    return AdbCapture(device=device)


__all__ = [
    "AdbDevice",
    "list_adb_devices",
    "AdbInput",
    "AdbCapture",
    "AndroidStreamCapture",
    "create_input_backend",
    "create_capture_backend",
    "scan_and_connect_wireless",
]
