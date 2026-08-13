"""燕云菜单 —— 通过 AppHooks menu_builders 注入通用 MainWindow 的菜单栏。

「燕云」菜单：游戏配置（F5）、调律配置（F6，内含调律验证入口）。
"""
from __future__ import annotations

from PyQt6.QtGui import QAction


def build_menu(host, menubar) -> None:
    """在通用菜单栏上追加「燕云」菜单（host 作为对话框 parent）"""
    menu = menubar.addMenu("燕云")

    def _open_game_config():
        from .game_config import GameConfigDialog
        dialog = GameConfigDialog(parent=host)
        dialog.exec()

    def _open_tuning_rules():
        from .tune_config import TuningRulesDialog
        dialog = TuningRulesDialog(parent=host)
        dialog.exec()

    for label, handler, shortcut in [
        ("游戏配置", _open_game_config, "F5"),
        ("调律配置", _open_tuning_rules, "F6"),
    ]:
        action = QAction(label, host)
        action.setShortcut(shortcut)
        action.triggered.connect(handler)
        menu.addAction(action)
