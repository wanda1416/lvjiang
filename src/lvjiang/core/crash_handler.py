"""崩溃防护层

三层防护确保进程崩溃时留下日志：
1. faulthandler — 捕获 segfault / SIGABRT 等硬崩溃，dump 到文件
2. sys.excepthook — 兜底未捕获的 Python 异常
3. Windows SetUnhandledExceptionFilter — 兜底 C 扩展 native crash

注意：faulthandler 预建的崩溃文件名时间戳是启动时间；正常退出且
无故障记录时由 atexit 自动删除空文件，避免空日志堆积。
"""

import atexit
import faulthandler
import sys
import time
import traceback
from pathlib import Path

from loguru import logger

from ..constants import PROJECT_ROOT

# faulthandler 预建的文件句柄与路径（atexit 清理空文件用）
_fault_file = None
_fault_path: Path | None = None
# SetUnhandledExceptionFilter 回调的模块级强引用：必须在模块层持有，
# 否则回调被 GC 后 filter 悬垂，真崩溃时跳入已释放内存产生
# 二次 AV / 0xc000001d 非法指令
_windows_handler_keepalive = None


def _crash_log_dir() -> Path:
    """崩溃日志目录"""
    d = PROJECT_ROOT / "logs" / "crashes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _crash_log_path() -> Path:
    """带时间戳的崩溃日志路径"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    return _crash_log_dir() / f"crash_{ts}.log"


# ─── Layer 1: faulthandler ───────────────────────────────

def _install_faulthandler():
    """启用 faulthandler，segfault 时写 trace 到文件"""
    global _fault_file, _fault_path
    crash_path = _crash_log_path()
    try:
        fp = open(crash_path, "w", encoding="utf-8")
        faulthandler.enable(file=fp, all_threads=True)
        _fault_file, _fault_path = fp, crash_path
        atexit.register(_cleanup_empty_crash_log)
        logger.debug(f"faulthandler 已启用，崩溃日志: {crash_path}")
    except Exception as e:
        logger.warning(f"faulthandler 启用失败: {e}")


def _cleanup_empty_crash_log():
    """正常退出且无故障记录时，删除启动时预建的空崩溃文件"""
    global _fault_file
    if _fault_file is None:
        return
    try:
        faulthandler.disable()
        _fault_file.close()
        _fault_file = None
        if _fault_path is not None and _fault_path.stat().st_size == 0:
            _fault_path.unlink()
    except Exception:
        pass


# ─── Layer 2: sys.excepthook ─────────────────────────────

def _install_excepthook():
    """兜底未捕获 Python 异常"""
    original = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        crash_path = _crash_log_path()
        try:
            with open(crash_path, "w", encoding="utf-8") as f:
                f.write(f"=== 未捕获异常 {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
        except Exception:
            pass
        logger.critical(f"未捕获异常，崩溃日志: {crash_path}")
        original(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    logger.debug("sys.excepthook 已安装")


# ─── Layer 3: Windows native crash handler ───────────────

def _install_windows_handler():
    """Windows SetUnhandledExceptionFilter 兜底 native crash"""
    global _windows_handler_keepalive
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        EXCEPTION_EXECUTE_HANDLER = 1

        # 用 opaque 指针，避免结构体大小/platform 差异
        PEXCEPTION_POINTERS = ctypes.c_void_p

        def _handler(exception_ptrs_raw):
            crash_path = _crash_log_path()
            try:
                with open(crash_path, "w", encoding="utf-8") as f:
                    f.write(f"=== Native Crash {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                    f.write(f"Raw pointer: 0x{exception_ptrs_raw:X}\n")
                    # dump 当前 Python 线程栈
                    f.write("\n--- Python traceback ---\n")
                    faulthandler.dump_traceback(file=f, all_threads=True)
            except Exception:
                pass
            return EXCEPTION_EXECUTE_HANDLER

        HANDLER_FUNC = ctypes.CFUNCTYPE(ctypes.c_long, PEXCEPTION_POINTERS)
        _handler_func = HANDLER_FUNC(_handler)
        kernel32.SetUnhandledExceptionFilter(_handler_func)
        # 模块级强引用防 GC：之前的 _handler_func._keepalive 自引用
        # 是循环引用，函数返回后仍会被垃圾回收器整环回收
        _windows_handler_keepalive = _handler_func
        logger.debug("Windows UnhandledExceptionFilter 已安装")
    except Exception as e:
        logger.warning(f"Windows native crash handler 安装失败: {e}")


# ─── 公共接口 ────────────────────────────────────────────

def install():
    """安装全部崩溃防护"""
    _install_faulthandler()
    _install_excepthook()
    if sys.platform == "win32":
        _install_windows_handler()
    logger.info("崩溃防护已启用")
