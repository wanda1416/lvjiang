"""bootstrap - 已废弃（保留为兼容桩）

历史背景：
    早期 ADB 后端使用 DeviceFarmer/minicap + minitouch 实现低延迟截图与触摸注入，
    需要按设备 abi/sdk 推送预编译二进制到 /data/local/tmp。
    但 minicap/minitouch 已停止维护多年，对 Android 14+ 不兼容。

现状：
    ADB 后端已切换为 `adb exec-out screencap -p` + `adb shell input tap/swipe`，
    无需任何二进制推送，bootstrap 流程废弃。

保留内容（供历史脚本/手动测试脚本兼容）：
    - BootstrapError 异常类（空壳）
    - bootstrap_minicap / bootstrap_minitouch / bootstrap_all：no-op 函数，仅打 warning

新代码请勿引用此模块。
"""

from loguru import logger

from .device import AdbDevice


class BootstrapError(RuntimeError):
    """已废弃：保留仅为兼容历史手动测试脚本"""


def bootstrap_minicap(device: AdbDevice) -> None:
    logger.warning("[bootstrap] bootstrap_minicap 已废弃，ADB 后端现使用 adb screencap，无需推送二进制")


def bootstrap_minitouch(device: AdbDevice) -> None:
    logger.warning("[bootstrap] bootstrap_minitouch 已废弃，ADB 后端现使用 adb shell input，无需推送二进制")


def bootstrap_all(device: AdbDevice) -> None:
    logger.warning("[bootstrap] bootstrap_all 已废弃，ADB 后端现使用 adb screencap + adb shell input，无需推送二进制")
