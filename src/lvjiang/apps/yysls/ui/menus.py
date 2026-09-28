"""燕云菜单 —— 通过 AppHooks menu_builders 注入通用 MainWindow 的菜单栏。

「燕云」菜单：游戏配置（F5）、调律配置（F6，内含调律验证入口）；
开发模式额外展示属性配置。
"""
from __future__ import annotations

from PyQt6.QtGui import QAction

from ....core.config import get_resolver
from ....i18n import tr


def build_menu(host, menubar) -> None:
    """在通用菜单栏上追加「燕云」菜单（host 作为对话框 parent）"""
    menu = menubar.addMenu(tr("燕云"))

    def _open_game_config():
        from .game_settings import GameConfigDialog
        dialog = GameConfigDialog(parent=host)
        dialog.exec()

    def _open_tuning_rules():
        from .tune_settings import TuningRulesDialog
        dialog = TuningRulesDialog(parent=host)
        dialog.exec()

    def _open_attr_config():
        from .game_settings import AttrConfigDialog
        dialog = AttrConfigDialog(parent=host)
        dialog.exec()

    entries = [
        (tr("游戏配置"), _open_game_config, "F5"),
        (tr("调律配置"), _open_tuning_rules, "F6"),
    ]
    if get_resolver().is_dev_mode():
        entries.append((tr("属性配置"), _open_attr_config, ""))

    for label, handler, shortcut in entries:
        action = QAction(label, host)
        if shortcut:
            action.setShortcut(shortcut)
        action.triggered.connect(handler)
        menu.addAction(action)
