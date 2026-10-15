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


class _InterceptHandler(logging.Handler):
    """把标准库 logging 记录转发给 loguru。

    插件加载器（``lvjiang.apps``）与 ``__main__`` 用的是标准库 logging：不转发
    的话，「加载插件 / 注册插件 / 忽略非法插件目录名」这类关键状态只进控制台、
    不落 ``logs/``，插件名册与加载顺序事后无法复盘。
    """

    def emit(self, record: logging.LogRecord) -> None:
        from loguru import logger

        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:  # 自定义 level 不在 loguru 级别表里
            level = record.levelno

        # 直接用 logging record 自带的调用点覆盖 loguru 的 {name}/{function}/{line}。
        # 不用 opt(depth=...)：回溯层数随 Python 版本变化，写死后会把调用方显示成
        # logging 内部（实测为 ``logging:callHandlers``）。
        logger.patch(
            lambda entry: entry.update(
                name=record.name,
                function=record.funcName,
                line=record.lineno,
            )
        ).opt(exception=record.exc_info).log(level, record.getMessage())


def _configure_logging() -> None:
    """配置 loguru（控制台 + logs/ 落盘），标准库 logging 转发到 loguru。

    落盘文件按天命名 ``logs/lvjiang_YYYY-MM-DD.log``；午夜（00:00）换新文件。
    启动时把仍在 7 天保留期内的更早日志归入 ``logs/logs/YYYY-MM/``，再早的删除。
    归档目录不在 FileSink 的文件名模式里，保留期由归档自己执行。
    """
    from loguru import logger

    logger.remove()  # 移除默认 handler
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}",
        level="DEBUG",
    )
    logger.add(
        str(PROJECT_ROOT / "logs" / "lvjiang_{time:YYYY-MM-DD}.log"),
        # 用午夜对齐而不是 "1 day"：后者按「创建满 24 小时」轮转，会让文件名与
        # 内容所属日期错位（例如 08 号文件里混进 09 号上午），归档时按名搬错。
        rotation="00:00",
        encoding="utf-8",
        level="DEBUG",
        enqueue=True,  # 异步写入，防止进程崩溃时缓冲丢失
    )
    # 插件加载器与 __main__ 等模块仍用标准库 logging；统一转发给 loguru，
    # 使「加载插件 / 注册插件」等关键状态同样落到 logs/ 文件里。
    logging.basicConfig(
        handlers=[_InterceptHandler()],
        level=logging.INFO,
        force=True,
    )


def _archive_old_logs() -> None:
    """归入 7 天内的历史日志，并删除更早的主日志（只在启动时做一次）。"""
    from loguru import logger

    from .core.log_files import archive_old_logs

    logs_dir = PROJECT_ROOT / "logs"
    try:
        archived = archive_old_logs(logs_dir)
    except OSError as e:  # 归档失败不能挡住启动，当天日志照常写入
        logger.warning(f"[logs] 历史日志归档失败: {e}")
        return
    if archived:
        logger.info(f"[logs] 已归档 {len(archived)} 份历史日志到 {logs_dir / 'logs'}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lvjiang",
        description=tr("通用视觉 RPA 引擎（支持插件扩展）"),
    )
    parser.add_argument(
        "--read-only", action="store_true", help=tr("共享配置只读，用户业务数据正常保存"),
    )
    parser.add_argument(
        "-reg",
        "--register",
        action="append",
        dest="apps",
        metavar="APP",
        help=tr("注册并加载插件：NAME（内置或 plugins/）或 NAME@PATH（任意目录）；可多次使用"),
    )
    parser.add_argument(
        "--all-plugins",
        action="store_true",
        dest="all_plugins",
        help=tr("额外加载内置插件与 plugins/ 默认目录中的全部插件，无需逐个 -reg"),
    )
    return parser.parse_args()


def main() -> int:
    import atexit

    from .core.access import close_instance, initialize_instance
    args = _parse_args()
    initialize_instance(PROJECT_ROOT, readonly=args.read_only)
    atexit.register(close_instance)
    _configure_dpi()
    _configure_logging()
    _archive_old_logs()
    from .core.config.interface import initialize_state_stores
    initialize_state_stores()

    # 插件会在 QApplication 创建前加载；先初始化文本翻译，避免插件 hooks
    # 和模块级显示标签永远按默认中文固化。run_app 会在 QApplication 创建后
    # 再初始化一次，以安装 Qt 自带控件的翻译器。
    from .i18n import init_i18n, load_app_i18n
    init_i18n()

    logger = logging.getLogger("lvjiang.__main__")

    # 崩溃防护：必须在所有 C 扩展（mss 等）加载之前安装
    from lvjiang.core.crash_handler import install as install_crash_handler
    install_crash_handler()

    # 延迟导入 PyQt6 / 内部模块，确保 logging 先配置好
    from lvjiang.apps import (
        app_name,
        load_app,
        load_config_policies,
        register_hooks,
        resolve_app_specs,
    )

    # -reg 保持出现顺序；--all-plugins 追加内置与 plugins/ 目录扫描结果。
    app_specs = resolve_app_specs(args.apps or (), all_plugins=args.all_plugins)

    for spec in app_specs:
        load_app_i18n(app_name(spec))
    load_config_policies(app_specs)

    # 恢复未完成的安装事务、撤下明确失效的会员内容，再加载场景与布局缓存。
    # 这里不联网，也不提升旧协议暂存目录。
    try:
        from lvjiang.core.config.remote import recover_installation
        recover_installation()
    except Exception:  # noqa: BLE001
        logger.exception("应用在线配置失败，本次使用现有配置")

    from .app import run_app

    hooks_list = []
    for spec in app_specs:
        logger.info("加载插件: %s", spec)
        load_app_i18n(app_name(spec))
        hooks = load_app(spec)
        register_hooks(hooks)
        hooks_list.append(hooks)

    return run_app(hooks_list)


if __name__ == "__main__":
    sys.exit(main())
