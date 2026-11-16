"""燕云菜单 —— 通过 AppHooks menu_builders 注入通用 MainWindow 的菜单栏。

「燕云」菜单：游戏配置（F5）、调律配置（F6，内含调律验证入口）、属性配置、
智能调律。
"""
from __future__ import annotations

from PyQt6.QtGui import QAction

from ....core.config.menu_shortcuts import ZONE_PLUGIN_MENU, MenuItem
from ....i18n import tr
from ....ui.plugin_menus import add_plugin_menu


def build_menu(host, menubar) -> None:
    """在通用菜单栏上追加「燕云」菜单（host 作为对话框 parent）"""
    menu = add_plugin_menu(menubar, "yysls", tr("燕云"))
    from .tuning.agent_page import AgentTuningDialog
    agent_dialog = AgentTuningDialog(host)
    host._agent_tuning_dialog = agent_dialog

    def _open_agent_tuning():
        agent_dialog.show()
        agent_dialog.raise_()
        agent_dialog.activateWindow()

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
        ("game_config", tr("游戏配置"), _open_game_config),
        ("tuning_rules", tr("调律配置"), _open_tuning_rules),
        ("attr_config", tr("属性配置"), _open_attr_config),
        ("agent_tuning", tr("智能调律"), _open_agent_tuning),
    ]

    actions: dict[str, QAction] = {}
    for item_id, label, handler in entries:
        action = QAction(label, host)
        action.triggered.connect(handler)
        menu.addAction(action)
        actions[item_id] = action
    ACTIONS.clear()
    ACTIONS.update(actions)
    apply_shortcuts(shortcuts_from_config())


#: 插件菜单的键位清单：区间 ``F5–F7``（插件段）与 ``Alt+字母``。
SHORTCUT_ITEMS: tuple[MenuItem, ...] = (
    MenuItem("game_config", ZONE_PLUGIN_MENU, "F5", group="燕云", label="游戏配置"),
    MenuItem("tuning_rules", ZONE_PLUGIN_MENU, "F6", group="燕云", label="调律配置"),
    MenuItem("attr_config", ZONE_PLUGIN_MENU, "", group="燕云", label="属性配置"),
    MenuItem("agent_tuning", ZONE_PLUGIN_MENU, "", group="燕云", label="智能调律"),
)

_ITEMS = {entry.item_id: entry for entry in SHORTCUT_ITEMS}
#: 当前窗口的插件菜单动作；由 build_menu 填充，供基础配置页保存后即时应用。
ACTIONS: dict[str, QAction] = {}


def resolved(item_id: str, overrides: dict[str, str]) -> str:
    spec = _ITEMS[item_id]
    return str((overrides or {}).get(item_id, spec.default) or "").strip()


def shortcuts_from_config() -> dict[str, str]:
    """读插件配置里的键位覆盖（缺省时用默认值，不写盘）。"""
    from ..config import get_game_config

    data = get_game_config().get_raw()
    basic = data.get("basic_config") if isinstance(data, dict) else None
    hotkeys = basic.get("hotkeys") if isinstance(basic, dict) else None
    return dict(hotkeys) if isinstance(hotkeys, dict) else {}


def current_shortcuts(overrides: dict[str, str] | None = None) -> dict[str, str]:
    values = shortcuts_from_config() if overrides is None else overrides
    return {entry.item_id: resolved(entry.item_id, values) for entry in SHORTCUT_ITEMS}


def apply_shortcuts(overrides: dict[str, str]) -> None:
    """把键位写到插件菜单动作上（窗口重建后由 build_menu 再次调用）。"""
    from PyQt6.QtGui import QKeySequence

    for item_id, action in ACTIONS.items():
        text = resolved(item_id, overrides)
        action.setShortcut(QKeySequence(text) if text else QKeySequence())
