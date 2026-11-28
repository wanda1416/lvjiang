"""应用入口：创建 QApplication 并显示主窗口。

主应用创建唯一的通用 MainWindow；插件通过 AppHooks 的 builder
注入 Tab / 菜单，不再替换主窗口类。
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any

from loguru import logger
from PyQt6 import sip
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from .core.platforms import clean_subprocess_env
from .ui.main import MainWindow
from .ui.widgets.misc import install_wheel_guard
from .ui.widgets.theme import get_theme_manager, reset_theme_manager

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


_restart_requested = False


def request_application_restart() -> None:
    """标记当前事件循环退出后自动原地重启应用。"""
    global _restart_requested
    _restart_requested = True


def cancel_application_restart() -> None:
    """取消已登记的原地重启（例如主窗口因任务运行中拒绝关闭）。"""
    global _restart_requested
    _restart_requested = False


def build_restart_command() -> list[str]:
    """构造原地重启命令，兼容 PyInstaller 发行版与源码运行。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, "-m", "lvjiang", *sys.argv[1:]]


def build_restart_env() -> dict[str, str]:
    """清理 PyInstaller 注入的环境变量，防止子进程继承旧 bootloader 状态。"""
    return clean_subprocess_env()


def spawn_restarted_process():
    """在单实例锁释放后以分离进程模式拉起新实例。"""
    import subprocess

    cmd = build_restart_command()
    env = build_restart_env()
    kwargs: dict = {
        "env": env,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, **kwargs)


def run_app(hooks_list: list[Any] | None = None) -> int:
    """启动 GUI 应用。

    Args:
        hooks_list: 已加载的插件 hooks 列表（按 -reg 顺序）。

    Returns:
        QApplication.exec() 的返回码。
    """
    global _app, _window, _hooks

    hooks_list = hooks_list or []
    from .core.plugins import register_config_policies
    for hooks in hooks_list:
        register_config_policies(hooks)

    logger.info("[app] 启动主窗口，已加载插件: {}",
                [getattr(h, "name", "?") for h in hooks_list] or "无")

    from .core.config.interface import initialize_state_stores
    initialize_state_stores()
    # 重建 content.json（实体修订索引）并清空 YAML 读取缓存；随后显式初始化
    # 场景注册表。scene_registry 已改为懒加载（模块导入不构造），因此这是
    # 启动时唯一一次场景加载，且发生在所有插件注册完成之后、创建 QApplication
    # 之前——插件内置函数/布局模块导入 scene_registry 都不会再提前触发加载。
    from .core.config.content import rebuild_content

    # 旧聚合配置（scenes.yaml / layouts.yaml）一次性拆分为目录化结构；
    # 幂等，聚合文件不存在时是空操作。必须在 rebuild_content 与场景
    # 注册表初始化之前完成，保证索引用新结构建立。
    from .core.config.content_split import prepare_content_split
    from .core.config.resolver import get_resolver
    prepare_content_split(get_resolver())
    rebuild_content(get_resolver())
    get_resolver().reset_edit_baselines()
    from .core.scene.registry import initialize_scene_registry
    initialize_scene_registry()
    # 任务栏归类必须在第一个窗口出现之前设置，否则 Windows 会沿用 python.exe 的图标。
    from .ui.widgets.app_icon import (
        apply_app_icon,
        set_linux_desktop_file_name,
        set_windows_app_user_model_id,
    )
    set_windows_app_user_model_id()
    _app = QApplication(sys.argv)
    apply_app_icon(_app)
    set_linux_desktop_file_name()
    from .core.config.conflicts import set_conflict_handler
    from .ui.config_conflicts import confirm_config_overwrite
    set_conflict_handler(confirm_config_overwrite)

    # 窗口创建前应用主题，避免启动时先闪出系统浅色再切换。
    from .core.config import load_user_config
    get_theme_manager(_app).apply(load_user_config().theme)

    # ── 初始化 i18n（在 QApplication 之后、MainWindow 之前）──
    from .i18n import init_i18n, load_app_i18n
    init_i18n()

    # 主体翻译加载完成后，加载已注册插件的专属翻译
    for h in hooks_list:
        key = h.id or h.name
        if key:
            try:
                load_app_i18n(key, directory=h.i18n_directory)
            except Exception:  # noqa: BLE001
                pass

    # 全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）
    install_wheel_guard(_app)

    from .core.access import is_readonly

    _window = MainWindow()
    if is_readonly():
        from PyQt6.QtWidgets import QLabel
        notice = "只读实例：全局热键由另一实例持有"
        _window.setWindowTitle(_window.windowTitle() + " [只读实例]")
        status = _window.statusBar()
        if status is not None:
            status.addPermanentWidget(QLabel(notice, _window))
    _window.setUpdatesEnabled(True)   # 构造期重绘已关闭，show 前恢复
    _window.show()

    # 启动后延迟检查更新（不阻塞主窗口显示）
    QTimer.singleShot(1000, _window.check_update_on_startup)

    # 在线配置同样放到后台：下载完只登记为待安装，下次启动读配置之前才安装。
    # 只读实例不下载；任务运行中也不会替换 system。
    from .core.config import remote as remote_config
    from .core.control import start_control_server, stop_control_server

    if not is_readonly():
        remote_config.start_background_sync()
        run_manager = _window._run_manager if _window is not None else None
        try:
            start_control_server(
                mode="gui",
                external_busy_check=(
                    run_manager.is_any_running if run_manager is not None else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).warning(f"[control] 启动 GUI 本地控制面失败: {exc}")
    # 先让后台下载与本地控制面看到停止信号，再由 _wait_for_threads 兜底等待；
    # aboutToQuit 的槽按连接顺序执行，所以这两条必须排在它前面。
    _app.aboutToQuit.connect(stop_control_server)
    _app.aboutToQuit.connect(remote_config.stop_background_sync)

    # 退出前等待后台线程，避免 PyQt6/SIP 清理时的 native crash
    _app.aboutToQuit.connect(_wait_for_threads)

    # 保存模块级引用，确保 QApplication 在整个进程生命周期内保持存活。
    _hooks = hooks_list

    exit_code = _app.exec()
    _dispose_qt_objects()
    global _restart_requested
    if _restart_requested:
        _restart_requested = False
        from .core.access import close_instance

        close_instance()
        try:
            spawn_restarted_process()
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error(f"原地重启拉起新进程失败: {exc}")
    return exit_code
