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

from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .ui.main import MainWindow
from .ui.theme import get_theme_manager, reset_theme_manager
from .ui.widgets import install_wheel_guard

logger = logging.getLogger(__name__)

# ── 模块级 Qt 对象引用 ──────────────────────────────────────────────
# 必须保持在模块级（而非函数局部），确保 QApplication 比窗口及其他
# Qt 包装器活得更久。业务资源由 MainWindow.closeEvent/aboutToQuit 清理，
# Qt 对象则在事件循环返回后、解释器退出前按依赖顺序显式释放。
_app: QApplication | None = None
_window: MainWindow | None = None
_hooks: list[Any] | None = None


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
        if t is not main_thread
        and t.is_alive()
        and t.daemon
        # 排除 native 线程的 _DummyThread 包装（不支持 join）
        and not isinstance(t, threading._DummyThread)  # noqa: SLF001
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


def _dispose_qt_objects() -> None:
    """在 Qt/Python 运行时仍完整时按依赖顺序释放顶层对象。

    不能把这段清理注册到 ``atexit``：解释器关闭阶段模块和 SIP 状态的
    析构顺序不确定，此时调用 ``close()``、``processEvents()`` 或清空
    QApplication 都可能触发原生访问违规。也不能完全交给解释器回收，
    否则同样会在模块清理阶段才析构 Qt 包装器。
    """
    global _app, _window, _hooks

    _hooks = None
    reset_theme_manager()

    if _window is not None:
        if not sip.isdeleted(_window):
            sip.delete(_window)
        _window = None

    if _app is not None:
        # 处理窗口析构排入的延迟事件，再销毁最后一个 Qt 根对象。
        _app.processEvents()
        if not sip.isdeleted(_app):
            sip.delete(_app)
        _app = None


def run_app(hooks_list: list[Any] | None = None) -> int:
    """启动 GUI 应用。

    Args:
        hooks_list: 已加载的插件 hooks 列表（按 -reg 顺序）。

    Returns:
        QApplication.exec() 的返回码。
    """
    global _app, _window, _hooks

    hooks_list = hooks_list or []

    logger.info("[app] 启动主窗口，已加载插件: %s",
                [getattr(h, "name", "?") for h in hooks_list] or "无")

    _app = QApplication(sys.argv)

    # 窗口创建前应用主题，避免启动时先闪出系统浅色再切换。
    from .core.config import load_user_config
    get_theme_manager(_app).apply(load_user_config().theme)

    # ── 初始化 i18n（在 QApplication 之后、MainWindow 之前）──
    from .i18n import init_i18n, load_app_i18n
    init_i18n()

    # 主体翻译加载完成后，加载已注册插件的专属翻译
    for h in hooks_list:
        if h.name:
            try:
                load_app_i18n(h.name)
            except Exception:  # noqa: BLE001
                pass

    # 全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）
    install_wheel_guard(_app)

    _window = MainWindow()
    _window.setUpdatesEnabled(True)   # 构造期重绘已关闭，show 前恢复
    _window.show()

    # 启动后延迟检查更新（不阻塞主窗口显示）
    QTimer.singleShot(1000, _window.check_update_on_startup)

    # 退出前等待后台线程，避免 PyQt6/SIP 清理时的 native crash
    _app.aboutToQuit.connect(_wait_for_threads)

    # 保存模块级引用，确保 QApplication 在整个进程生命周期内保持存活。
    _hooks = hooks_list

    exit_code = _app.exec()
    _dispose_qt_objects()
    return exit_code
