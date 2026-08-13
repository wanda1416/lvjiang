"""应用入口：创建 QApplication 并显示主窗口。

主应用创建唯一的通用 MainWindow；插件通过 AppHooks 的 builder
注入 Tab / 菜单，不再替换主窗口类。
"""
from __future__ import annotations

import atexit
import logging
import sys
import threading
import time
from typing import Any

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.widgets import install_wheel_guard

logger = logging.getLogger(__name__)

# ── 模块级 Qt 对象引用 ──────────────────────────────────────────────
# 必须保持在模块级（而非函数局部），原因：
# PyQt 官方文档 "Crashes On Exit" 明确指出：函数返回后局部 Qt 对象
# 的析构顺序不确定，QApplication 可能先于子 widget 被销毁，导致 SIP
# 的 cleanup_qobject 访问已释放的 C++ 指针而崩溃（EXC_BAD_ACCESS）。
# 模块级对象的析构在进程终止时不被调用（由 PyQt 保证），改由
# _cleanup_on_exit() 通过 atexit 按正确顺序显式清理。
_app: QApplication | None = None
_window: MainWindow | None = None
_hooks: list[Any] | None = None


@atexit.register
def _cleanup_on_exit() -> None:
    """解释器退出时按安全顺序清理 Qt 对象。

    清理顺序（每步确保前一步 C++ 对象仍有效）：
    1. 释放插件 hooks → 插件持有的 widget 引用被释放，
       子 widget Python 包装器随 GC 回收，其 C++ 指针在
       sipWrapper_dealloc 中被安全置 NULL，从 SIP 跟踪列表移除。
    2. 关闭并删除 window → 剩余 C++ 子对象被级联释放，
       但此时 SIP 列表中已无对应包装器（步骤 1 已移除）。
    3. 删除 app → QApplication 析构时 cleanup_qobject 遍历
       SIP 列表，所有先前子对象的包装器已不在列表中，安全退出。

    此顺序避免了 cleanup_qobject 访问 NULL cppPtr 导致的
    EXC_BAD_ACCESS（macOS 上尤为常见）。
    """
    global _hooks, _window, _app

    # 1. 先释放插件 hooks（可能持有对 Qt widget 的引用）
    if _hooks is not None:
        _hooks.clear()
        _hooks = None

    # 2. 关闭并删除主窗口（及残留的顶级 widget）
    if _window is not None:
        _window.close()
        # 清理可能脱离主窗口的孤儿顶级 widget（如独立对话框）
        if _app is not None:
            for w in list(_app.topLevelWidgets()):
                if w is not _window:
                    w.close()
                    w.deleteLater()
            _app.processEvents()
        _window = None

    # 3. 最后删除 QApplication
    _app = None


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

    # ── 初始化 i18n（在 QApplication 之后、MainWindow 之前）──
    from .i18n import init_i18n
    init_i18n()

    # 全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）
    install_wheel_guard(_app)

    _window = MainWindow()
    _window.setUpdatesEnabled(True)   # 构造期重绘已关闭，show 前恢复
    _window.show()

    # 启动后延迟检查更新（不阻塞主窗口显示）
    QTimer.singleShot(1000, _window.check_update_on_startup)

    # 退出前等待后台线程，避免 PyQt6/SIP 清理时的 native crash
    _app.aboutToQuit.connect(_wait_for_threads)

    # 保存 hooks 引用（模块级），确保 atexit 清理时能先于 window 释放
    _hooks = hooks_list

    return _app.exec()
    # 注意：不在这里 del window / del app。
    # Qt 对象由模块级变量持有，_cleanup_on_exit() 通过 atexit
    # 按正确顺序清理（hooks → window → app），避免 SIP 析构崩溃。
