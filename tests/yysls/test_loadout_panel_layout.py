from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QComboBox

from lvjiang.apps.yysls.ui.loadout.loadout_panel import LoadoutPanel


class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return []


class _Host(QObject):
    user_changed = pyqtSignal(str)
    app_event = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.user_manager = _Users()
        self.user_combo = QComboBox()

    @staticmethod
    def active_user_name() -> str:
        return ""

    @staticmethod
    def navigate_user(_delta: int) -> None:
        return None


def test_assumptions_share_public_metric_row(qtbot):
    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)

    controls = [
        panel._assumption_layout.itemAt(index).widget()
        for index in range(3)
    ]
    assert [control.text() for control in controls] == [
        "满等级", "满承音", "满定音",
    ]
    assert all(
        control.parentWidget() is panel._assumption_card
        for control in controls
    )
    assert [panel._metrics_layout.stretch(index) for index in range(3)] == [
        2, 1, 1,
    ]


def test_panel_loads_one_inventory_per_refresh_and_shares_it(qtbot, tmp_path, monkeypatch):
    """一轮刷新只读一次仓储：装备页与战斗属性页共用同一份快照，
    装备页的「当前流派」由方案主副武学派生而不是问兄弟页。"""
    from lvjiang.apps.yysls.core.combat import equipment as equipment_mod
    from lvjiang.apps.yysls.core.loadout import LoadoutRepository
    from lvjiang.apps.yysls.ui.loadout import loadout_panel as panel_mod

    class _NamedHost(_Host):
        @staticmethod
        def active_user_name() -> str:
            return "tester"

    monkeypatch.setattr(
        panel_mod, "LoadoutRepository",
        lambda username: LoadoutRepository(username, tmp_path))
    real_inventory = equipment_mod.EquipmentInventory
    loads: list[str] = []

    class _CountingInventory(real_inventory):
        def __init__(self, user_name: str) -> None:
            loads.append(user_name)
            self._repo = LoadoutRepository(user_name, tmp_path)
            self.reload()

    monkeypatch.setattr(equipment_mod, "EquipmentInventory", _CountingInventory)

    panel = LoadoutPanel(_NamedHost())
    qtbot.addWidget(panel)
    loads.clear()
    panel.refresh()

    assert loads == ["tester"]                      # 面板加载一次
    equipment = panel._equipment
    combat = panel._character._combat_attrs_tab
    assert equipment._inv is not None
    assert combat._equipped_cache == equipment._inv.equipped   # 同一份快照
    assert equipment._combat_tab is combat         # 注入而非 findChildren
    # 没有主副武学的方案：流派为空，与方案派生口径一致
    assert equipment._get_current_school() == ""


def test_panel_refresh_picks_up_changed_equip_display_settings(qtbot, tmp_path, monkeypatch):
    """改了装备卡片展示参数后点刷新即生效，不需要重启。"""
    from lvjiang.apps.yysls.config import equip_display
    from lvjiang.apps.yysls.core.loadout import LoadoutRepository
    from lvjiang.apps.yysls.ui.loadout import loadout_panel as panel_mod

    class _NamedHost(_Host):
        @staticmethod
        def active_user_name() -> str:
            return "tester"

    monkeypatch.setattr(
        panel_mod, "LoadoutRepository",
        lambda username: LoadoutRepository(username, tmp_path))
    from lvjiang.apps.yysls.core.combat import equipment as equipment_mod

    class _Inventory(equipment_mod.EquipmentInventory):
        def __init__(self, user_name: str) -> None:
            self._repo = LoadoutRepository(user_name, tmp_path)
            self.reload()

    monkeypatch.setattr(equipment_mod, "EquipmentInventory", _Inventory)
    settings = {"equip_display": {"name_font_size": 13, "grid_columns": 4}}
    monkeypatch.setattr(equip_display, "load_settings", lambda: settings)

    panel = LoadoutPanel(_NamedHost())
    qtbot.addWidget(panel)
    panel.refresh()
    assert panel._equipment._display_params["name_font_size"] == 13

    settings["equip_display"] = {"name_font_size": 16, "grid_columns": 3}
    panel.refresh()                       # 刷新按钮走的就是这条路径
    assert panel._equipment._display_params["name_font_size"] == 16
    assert panel._equipment._display_params["grid_columns"] == 3


def test_startup_builds_inventory_once_and_first_show_does_not_refresh(
    qtbot, tmp_path, monkeypatch,
):
    """启动路径只读一次仓储、只建一次装备格子：装备页构造期不自己读盘，
    面板首次显示不再补一次全量刷新；真正隐藏过之后再显示才刷新。"""
    from lvjiang.apps.yysls.core.combat import equipment as equipment_mod
    from lvjiang.apps.yysls.core.loadout import LoadoutRepository
    from lvjiang.apps.yysls.ui.loadout import loadout_panel as panel_mod
    from lvjiang.apps.yysls.ui.loadout.equip import status_tab as status_mod

    class _NamedHost(_Host):
        @staticmethod
        def active_user_name() -> str:
            return "tester"

    monkeypatch.setattr(
        panel_mod, "LoadoutRepository",
        lambda username: LoadoutRepository(username, tmp_path))
    real_inventory = equipment_mod.EquipmentInventory
    loads: list[str] = []

    class _CountingInventory(real_inventory):
        def __init__(self, user_name: str) -> None:
            loads.append(user_name)
            self._repo = LoadoutRepository(user_name, tmp_path)
            self.reload()

    monkeypatch.setattr(equipment_mod, "EquipmentInventory", _CountingInventory)
    rebuilds: list[int] = []
    real_rebuild = status_mod.EquipStatusTab._rebuild_grid
    monkeypatch.setattr(
        status_mod.EquipStatusTab, "_rebuild_grid",
        lambda self: rebuilds.append(1) or real_rebuild(self))

    panel = LoadoutPanel(_NamedHost())
    qtbot.addWidget(panel)
    assert loads == ["tester"]
    # 构造期一次空格子 + 面板刷新一次
    assert len(rebuilds) == 2

    panel.show()
    qtbot.waitExposed(panel)
    qtbot.wait(50)
    assert loads == ["tester"]          # 首次显示不重复读盘
    assert len(rebuilds) == 2

    panel.hide()
    panel.show()
    qtbot.wait(50)
    assert loads == ["tester", "tester"]   # 隐藏期间可能错过变更，再显示补一次
