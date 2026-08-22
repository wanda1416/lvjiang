"""Android 设备后端 — 统一封装 ADB 截图/输入 + scrcpy 视频流 + 设备端代理

模块结构：
- device.py        : AdbDevice，adb 可执行/serial 解析、shell/forward/push、设备属性
- input.py         : AdbInput，继承 InputBackend，基于 adb shell input tap/swipe
- adb_capture.py   : AdbCapture，继承 CaptureBackend，基于 adb exec-out screencap -p
- scrcpy_capture.py: AndroidStreamCapture，继承 CaptureBackend，基于 scrcpy 4.1 server H.264 视频流
- agent.py         : AgentClient / AgentCapture / AgentInput，经 adb forward 连接手机上的律匠 app，
                     截图与手势由 app 内的无障碍服务（或 Shizuku）落地
- bootstrap.py     : 已废弃（保留为兼容桩）

工厂函数：
- connect_agent(device)：尝试连接设备端代理，不可达返回 None
- create_input_backend(device, input_sim, agent)：有代理走 AgentInput，否则 AdbInput
- create_capture_backend(device, method, agent)：screencap / scrcpy / agent
"""

from ...core.config import InputSimConfig
from ..capture_base import CaptureBackend
from ..input_base import InputBackend
from .adb_capture import AdbCapture
from .agent import AgentCapture, AgentClient, AgentError, AgentInput, connect_agent
from .device import AdbDevice, list_adb_devices
from .input import AdbInput
from .scrcpy_capture import AndroidStreamCapture
from .wireless import scan_and_connect_wireless


def create_input_backend(
    device: AdbDevice,
    input_sim: InputSimConfig | None = None,
    agent: AgentClient | None = None,
) -> InputBackend:
    """创建输入后端

    Args:
        device: AdbDevice 实例
        input_sim: 输入模拟参数
        agent: 已连接的设备端代理；给了就走设备端手势（无障碍 dispatchGesture），
               否则退回 adb shell input

    Returns:
        AgentInput 或 AdbInput 实例
    """
    if agent is not None and agent.connected:
        return AgentInput(client=agent, input_sim=input_sim)
    return AdbInput(device=device, input_sim=input_sim)


def create_capture_backend(
    device: AdbDevice,
    method: str = "screencap",
    agent: AgentClient | None = None,
) -> CaptureBackend:
    """创建截图后端

    Args:
        device: AdbDevice 实例
        method: 截图方式，"screencap"（默认，adb exec-out screencap -p）、
                "scrcpy"（scrcpy 4.1 server H.264 视频流，推送式取帧）
                或 "agent"（设备端代理：无障碍 takeScreenshot / Shizuku screencap）
        agent: method="agent" 时必须给已连接的代理

    Returns:
        AdbCapture / AndroidStreamCapture / AgentCapture 实例
    """
    if method == "scrcpy":
        return AndroidStreamCapture(device=device)
    if method == "agent":
        if agent is None or not agent.connected:
            raise ValueError("method='agent' 需要已连接的设备端代理")
        return AgentCapture(client=agent)
    return AdbCapture(device=device)


__all__ = [
    "AdbDevice",
    "list_adb_devices",
    "AdbInput",
    "AdbCapture",
    "AndroidStreamCapture",
    "AgentClient",
    "AgentCapture",
    "AgentInput",
    "AgentError",
    "connect_agent",
    "create_input_backend",
    "create_capture_backend",
    "scan_and_connect_wireless",
]
