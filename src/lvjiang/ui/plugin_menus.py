"""插件菜单按稳定 ID 登记与查找，显示名称只用于 UI。"""

from PyQt6.QtWidgets import QMenu, QMenuBar


def add_plugin_menu(menubar: QMenuBar, app_id: str, label: str) -> QMenu:
    if find_plugin_menu(menubar, app_id) is not None:
        raise ValueError(f"插件菜单已存在: {app_id}")
    menu = menubar.addMenu(label)
    if menu is None:
        raise RuntimeError(f"无法创建插件菜单: {app_id}")
    action = menu.menuAction()
    if action is None:
        raise RuntimeError(f"插件菜单缺少菜单动作: {app_id}")
    action.setData(app_id)
    return menu


def find_plugin_menu(menubar: QMenuBar, app_id: str) -> QMenu | None:
    for action in menubar.actions():
        if action.data() == app_id:
            return action.menu()
    return None


def require_plugin_menu(menubar: QMenuBar, app_id: str) -> QMenu:
    menu = find_plugin_menu(menubar, app_id)
    if menu is None:
        raise RuntimeError(f"插件菜单尚未创建: {app_id}")
    return menu
