"""燕云剧情插件菜单：把「自动剧情」注入燕云插件创建的「燕云」菜单。

宿主按插件注册顺序调用 menu_builders；本插件 ``requires_app_ids=("yysls",)``
保证执行到这里时「燕云」菜单已存在（``yysls_premium`` 的「采集录制」同款）。
"""

from __future__ import annotations

from PyQt6.QtGui import QAction

from lvjiang.i18n import tr
from lvjiang.ui.plugin_menus import require_plugin_menu


def build_menu(host, menubar) -> None:
    menu = require_plugin_menu(menubar, "yysls")

    def _open_story_choices() -> None:
        from .dialog import StoryChoicesDialog

        dialog = StoryChoicesDialog(parent=host)
        dialog.exec()

    action = QAction(tr("自动剧情"), host)
    action.triggered.connect(_open_story_choices)
    menu.addAction(action)
