"""应用入口：创建 QApplication 并显示主窗口。

主应用创建唯一的通用 MainWindow；插件通过 AppHooks 的 builder
注入 Tab / 菜单，不再替换主窗口类。
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Any

from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.widgets import install_wheel_guard

logger = logging.getLogger(__name__)


def _wait_for_threads(timeout: float = 5.0) -> None:
    """等待所有非主线程退出，避免退出时 native crash。

    PyQt6/SIP 在 QApplication 析构时会清理 Qt 包装器，如果此时还有
    后台线程（如 PyAV 解码线程）在访问 C 扩展对象，会导致段错误。
    此函数在 aboutToQuit 时调用，确保线程先于 Qt 清理退出。
    """
    # 先停掉 loguru 异步写入线程（enqueue=True 创建的后台线程），
    # 否则该线程会一直阻塞等待队列刷完，导致退出挂起。
    try:
        from loguru import logger as _loguru
        _loguru.remove()
    except Exception:
        pass

    main_thread = threading.current_thread()
    pending = [
        t for t in threading.enumerate()
        if t is not main_thread and t.is_alive() and t.daemon
    ]
    if not pending:
        return

    logger.debug(f"[app] 等待 {len(pending)} 个后台线程退出 (timeout={timeout}s)...")
    deadline = time.monotonic() + timeout
    for t in pending:
        remaining = max(0.1, deadline - time.monotonic())
        t.join(timeout=remaining)
        if t.is_alive():
            logger.warning(f"[app] 线程 {t.name} 未按时退出，强制继续")


def run_app(hooks_list: list[Any] | None = None) -> int:
    """启动 GUI 应用。

    Args:
        hooks_list: 已加载的插件 hooks 列表（按 -reg 顺序）。

    Returns:
        QApplication.exec() 的返回码。
    """
    hooks_list = hooks_list or []

    logger.info("[app] 启动主窗口，已加载插件: %s",
                [getattr(h, "name", "?") for h in hooks_list] or "无")

    app = QApplication(sys.argv)
    # 全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）
    install_wheel_guard(app)

    window = MainWindow(hooks_list=hooks_list)
    window.show()

    # 退出前等待后台线程，避免 PyQt6/SIP 清理时的 native crash
    app.aboutToQuit.connect(_wait_for_threads)

    return app.exec()
