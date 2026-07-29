"""退出 AV 复现脚本：自动启动主窗口并在数秒后关闭。

用法（与 dev.bat 相同的解释器）：
    python scripts/manual-tests/repro_exit_crash.py            # 基线（enqueue=True）
    set REPRO_NO_ENQUEUE=1 && python scripts/...               # 对照：关闭 loguru 异步写入
    set REPRO_NO_HOTKEY=1 && python scripts/...                # 对照：跳过全局热键监听

观察点：进程退出码（AV → 0xC0000005 / 3221225477）与 logs/crashes/
是否新增崩溃日志。
"""
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

from src.__main__ import _configure_dpi  # noqa: E402

_configure_dpi()


def _configure_logging(enqueue: bool) -> None:
    """复刻 src.__main__._configure_logging，仅 enqueue 可控"""
    import logging
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level="DEBUG",
               format="{time:HH:mm:ss} | {level:<7} | {message}")
    logger.add("logs/lvjiang_{time:YYYY-MM-DD}.log", rotation="1 day",
               retention="7 days", encoding="utf-8", level="DEBUG",
               enqueue=enqueue)
    logging.basicConfig(level=logging.INFO)


def main() -> int:
    enqueue = os.environ.get("REPRO_NO_ENQUEUE") != "1"
    no_hotkey = os.environ.get("REPRO_NO_HOTKEY") == "1"
    print(f"[repro] enqueue={enqueue} no_hotkey={no_hotkey}", flush=True)

    _configure_logging(enqueue)
    from src.core.crash_handler import install as install_crash_handler
    install_crash_handler()

    if no_hotkey:
        # 全局热键置换为空启动，隔离 pynput 因素
        from src.ui import main_window as mw

        class _DummyHotKeys:
            def __init__(self, *_a, **_k):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def join(self, *_a):
                pass

            def is_alive(self):
                return False

        mw.pynput_keyboard.GlobalHotKeys = _DummyHotKeys

    from src.apps import load_app, register_hooks
    hooks = load_app("yysls")
    register_hooks(hooks)

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from src.ui.main_window import MainWindow
    from src.ui.widgets import install_wheel_guard

    app = QApplication(sys.argv)
    install_wheel_guard(app)
    window = MainWindow(hooks_list=[hooks])
    window.show()
    QTimer.singleShot(4000, window.close)
    rc = app.exec()
    print(f"[repro] app.exec() returned {rc}", flush=True)
    return rc


if __name__ == "__main__":
    rc = main()
    print("[repro] main() frame exited (QApplication 已析构)", flush=True)
    sys.exit(rc)
