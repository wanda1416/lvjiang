"""律匠 GUI 启动入口"""

import sys
from PyQt6.QtWidgets import QApplication
from loguru import logger

from .main_window import MainWindow
from .widgets import install_wheel_guard


def run_app():
    """启动应用"""
    # 进程 DPI 感知已在入口 __main__._lock_dpi_awareness() 中锁定为 Per-Monitor v2，
    # 必须早于 mss import，此处不再重复设置。

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）
    install_wheel_guard(app)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
