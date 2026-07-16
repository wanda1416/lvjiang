"""律匠启动入口 - python -m lvjiang"""

import ctypes
import sys


def _lock_dpi_awareness() -> None:
    """在导入任何触及 mss 的模块之前，锁定进程 DPI 感知为 Per-Monitor v2。

    mss 在初始化时会调用 SetProcessDPIAware()（System DPI Aware），若它抢在
    QApplication 之前生效，会把进程钉死为 System 级别（进程 DPI 感知只认第一个
    设置者），导致 GetWindowRect（窗口定位）与 mss.grab（截图）坐标系错位，出现
    截图偏移、OCR 裁剪错位。最早锁定 v2 后，mss 后续的 DPI 调用都会无害失败。
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 回退：Windows 8.1 Per-Monitor v1
    except (AttributeError, OSError):
        pass


_lock_dpi_awareness()

from loguru import logger

from .ui.app import run_app


def main():
    # 配置 loguru
    logger.remove()  # 移除默认 handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
        level="DEBUG",
    )
    logger.add(
        "logs/lvjiang_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        level="DEBUG",
        enqueue=True,  # 异步写入，防止进程崩溃时缓冲丢失
    )

    logger.info("律匠启动中...")
    run_app()


if __name__ == "__main__":
    main()
