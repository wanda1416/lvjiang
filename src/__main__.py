"""通用引擎启动入口。

用法：
    python -m src                 # 纯通用（场景编辑 + 识别测试）
    python -m src -reg yysls      # 通用 + 燕云插件
    python -m src -reg a -reg b   # 通用 + 多个插件

本模块职责：
1. 锁定 DPI 感知（Windows）
2. 配置 loguru / logging
3. 解析命令行参数（-reg）
4. 按顺序加载插件并注册 hooks
5. 调用 run_app() 启动 GUI
"""
from __future__ import annotations

import argparse
import logging
import sys


def _configure_dpi() -> None:
    """Windows 下锁定 DPI 感知为 Per-Monitor v2，避免截图坐标偏移。

    必须在任何 import 之前调用，否则 pyautogui/mss 等模块可能在
    QApplication 之前调用 SetProcessDPIAware() 把进程钉死为 System 级别，
    导致 GetWindowRect（窗口定位）与 mss.grab（截图）坐标系错位。
    """
    import ctypes
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


def _configure_logging() -> None:
    """配置基础 logging（后续可接入 loguru / 崩溃处理）。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="src",
        description="通用视觉 RPA 引擎（支持插件扩展）",
    )
    parser.add_argument(
        "-reg",
        "--register",
        action="append",
        dest="apps",
        metavar="APP",
        help="注册并加载指定插件（可多次使用，例如 -reg yysls -reg other）",
    )
    return parser.parse_args()


def main() -> int:
    _configure_dpi()
    _configure_logging()

    args = _parse_args()
    logger = logging.getLogger("src.__main__")

    # 延迟导入 PyQt6 / 内部模块，确保 logging 先配置好
    from .app import run_app
    from src.apps import load_app, register_hooks

    hooks_list = []
    for name in args.apps or []:
        logger.info("加载插件: %s", name)
        hooks = load_app(name)
        register_hooks(hooks)
        hooks_list.append(hooks)

    return run_app(hooks_list)


if __name__ == "__main__":
    sys.exit(main())
