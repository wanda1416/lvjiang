"""应用入口：创建 QApplication 并显示主窗口。

主应用创建唯一的通用 MainWindow；插件通过 AppHooks 的 builder
注入 Tab / 菜单，不再替换主窗口类。
"""
from __future__ import annotations

import logging
import sys
from typing import Any

from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui.widgets import install_wheel_guard

logger = logging.getLogger(__name__)


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

    return app.exec()
