"""通用引擎启动入口。

用法：
    python -m lvjiang                 # 纯通用（场景编辑 + 识别测试）
    python -m lvjiang -reg yysls      # 通用 + 燕云插件
    python -m lvjiang -reg a -reg b   # 通用 + 多个插件

本模块职责：
1. 锁定 DPI 感知（Windows）
2. 配置 loguru / logging（含 logs/ 文件落盘）
3. 安装崩溃防护（logs/crashes/）
4. 解析命令行参数（-reg）
5. 按顺序加载插件并注册 hooks
6. 调用 run_app() 启动 GUI
"""
from __future__ import annotations

import argparse
import logging
import sys

from lvjiang.constants import PROJECT_ROOT

from .i18n import tr


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
    """配置 loguru（控制台 + logs/ 落盘），标准库 logging 保底控制台。"""
    from loguru import logger

    logger.remove()  # 移除默认 handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
        level="DEBUG",
    )
    logger.add(
        str(PROJECT_ROOT / "logs" / "lvjiang_{time:YYYY-MM-DD}.log"),
        rotation="1 day",
        retention="7 days",
        encoding="utf-8",
        level="DEBUG",
        enqueue=True,  # 异步写入，防止进程崩溃时缓冲丢失
    )
    # 少量模块仍用标准库 logging，保底输出到控制台
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lvjiang",
        description=tr("通用视觉 RPA 引擎（支持插件扩展）"),
    )
    parser.add_argument(
        "-reg",
        "--register",
        action="append",
        dest="apps",
        metavar="APP",
        help=tr("注册并加载指定插件（可多次使用，例如 -reg yysls -reg other）"),
    )
    return parser.parse_args()


def main() -> int:
    _configure_dpi()
    _configure_logging()

    # 插件会在 QApplication 创建前加载；先初始化文本翻译，避免插件 hooks
    # 和模块级显示标签永远按默认中文固化。run_app 会在 QApplication 创建后
    # 再初始化一次，以安装 Qt 自带控件的翻译器。
    from .i18n import init_i18n, load_app_i18n
    init_i18n()

    args = _parse_args()
    logger = logging.getLogger("lvjiang.__main__")

    # 崩溃防护：必须在所有 C 扩展（mss 等）加载之前安装
    from lvjiang.core.crash_handler import install as install_crash_handler
    install_crash_handler()

    # 延迟导入 PyQt6 / 内部模块，确保 logging 先配置好
    from lvjiang.apps import load_app, register_hooks

    from .app import run_app

    # 应用上次下载的在线配置。必须在**任何配置读取之前**：场景注册表与布局
    # 都在启动时加载，晚一步这次启动就还是旧配置。失败不阻断启动——用系统
    # 配置本来就是可用状态。
    try:
        from lvjiang.core.config.remote import promote_pending
        promote_pending()
    except Exception:  # noqa: BLE001
        logger.exception("应用在线配置失败，本次使用现有配置")

    hooks_list = []
    for name in args.apps or []:
        logger.info("加载插件: %s", name)
        load_app_i18n(name)
        hooks = load_app(name)
        register_hooks(hooks)
        hooks_list.append(hooks)

    return run_app(hooks_list)


if __name__ == "__main__":
    sys.exit(main())
