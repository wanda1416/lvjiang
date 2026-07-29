"""应用入口：创建 QApplication 并显示主窗口。

阶段 1 实现最小可用版本：
- 读取全局注册表中的 main_window_class（若有插件覆盖）
- 实例化主窗口并展示
"""
from __future__ import annotations

import inspect
import logging
import sys
from typing import Any

from PyQt6.QtWidgets import QApplication

from src.apps import get_registry
from .ui.main_window import MainWindow as DefaultMainWindow
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
    registry = get_registry()

    # 选择主窗口类：插件可覆盖
    window_class = registry.get("main_window_class") or DefaultMainWindow

    logger.info("[app] 启动主窗口: %s", window_class.__name__)

    app = QApplication(sys.argv)
    # 全局屏蔽下拉框/数字输入框的滚轮改值（防滑动页面时误改）
    install_wheel_guard(app)

    # 根据主窗口类的签名决定是否传入 hooks_list
    sig = inspect.signature(window_class.__init__)
    if "hooks_list" in sig.parameters:
        window = window_class(hooks_list=hooks_list)
    else:
        window = window_class()
    window.show()

    return app.exec()
