"""律匠 GUI 启动入口"""

import sys
from PyQt6.QtWidgets import QApplication
from loguru import logger

from .main_window import MainWindow


def run_app():
    """启动应用"""
    # Qt6 默认已设置 Per Monitor DPI Aware v2，无需额外调用

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
