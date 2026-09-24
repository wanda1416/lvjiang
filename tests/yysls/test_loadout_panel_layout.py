from __future__ import annotations

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QLineEdit, QPushButton

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
    assert panel._hypothesis_view_toggle.text() == "假设视图"
    assert panel._assumption_layout.itemAt(
        panel._assumption_layout.count() - 1).widget() is (
            panel._hypothesis_view_toggle)


def test_hypothesis_view_projects_slot_cards_without_mutating_equipment(
    qtbot, monkeypatch,
):
    from lvjiang.apps.yysls.config import get_game_config
    from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions

    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)
    original = {
        "type": "环", "name": "测试环", "level": 110,
        "quality": "gold", "is_chengyin": False,
        "affix_1": {"name": "最大外功攻击", "value": 100},
    }
    panel._equipment._equipped = {"ring": original}
    assumptions = Assumptions(full_level=get_game_config().current_equip_level())
    monkeypatch.setattr(
        panel._character._combat_attrs_tab, "assumptions", lambda: assumptions)
    panel._equipment._refresh_slots()

    panel._hypothesis_view_toggle.setChecked(True)

    shown = panel._equipment._slot_cards["ring"]._equip_data
    assert shown is not original
    assert shown["level"] == get_game_config().current_equip_level()
    assert shown["is_chengyin"] is True
    assert "承音" in panel._equipment._slot_cards["ring"].lbl_info.text()
    assert original["level"] == 110
    assert original["is_chengyin"] is False

    panel._hypothesis_view_toggle.setChecked(False)
    assert panel._equipment._slot_cards["ring"]._equip_data is original


def test_main_toolbar_exposes_first_three_analysis_tabs(qtbot):
    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)
    root = panel.layout()
    assert root is not None
    toolbar = root.itemAt(0).layout()
    assert toolbar is not None
    labels = [
        widget.text()
        for index in range(toolbar.count())
        if (widget := toolbar.itemAt(index).widget()) is not None
        and isinstance(widget, QPushButton)
    ]
    positions = [labels.index(name) for name in (
        "最优组合", "转律建议", "培养建议")]
    assert positions == list(range(positions[0], positions[0] + 3))
    assert "词条收益率" not in labels


def test_plan_row_only_exposes_create_manage_and_read_only_details(qtbot):
    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)
    row = panel.layout().itemAt(1).layout()
    assert row is not None
    buttons = [row.itemAt(index).widget().text()
               for index in range(row.count())
               if isinstance(row.itemAt(index).widget(), QPushButton)]
    assert buttons == ["新建", "管理"]
    assert isinstance(panel._plans, QComboBox)
    fields = (panel._school, panel._main_art,
              panel._sub_art, panel._playstyle)
    assert all(isinstance(widget, QLineEdit) for widget in fields)
    assert all(widget.isReadOnly() and not widget.isEnabled()
               for widget in fields)
    combat = panel._character._combat_attrs_tab
    assert row.indexOf(combat._plan_scheme_field) > row.indexOf(panel._playstyle)
    assert row.indexOf(combat._plan_gongjue_field) > row.indexOf(
        combat._plan_scheme_field)
    assert combat._select_group.title() == "当前属性"
    assert combat._select_layout.count() == 2
    assert [combat._select_layout.itemAt(i).widget() for i in range(2)][1] is (
        combat._btn_edit_play_style)


@pytest.mark.parametrize("school", ["测试流派", ""])
def test_edit_attrs_opens_school_config_without_selected_attr(
    qtbot, monkeypatch, school,
):
    from lvjiang.apps.yysls.ui import game_settings

    panel = LoadoutPanel(_Host())
    qtbot.addWidget(panel)
    combat = panel._character._combat_attrs_tab
    combat._current_school_name = school
    combat._combo_play_style.clear()
    opened = []

    class _Dialog:
        def __init__(self, parent):
            assert parent is panel._host

        def select_school_base_attr(self, school, base_attr):
            opened.append((school, base_attr))

        def exec(self):
            return 0

    monkeypatch.setattr(game_settings, "GameConfigDialog", _Dialog)
    monkeypatch.setattr(combat, "_refresh_play_styles", lambda: None)
    monkeypatch.setattr(combat, "_refresh_display", lambda: None)
    combat._on_edit_play_style()
    assert opened == [(school or None, "")]


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


def test_assumption_checkboxes_round_trip_through_user_metadata(qtbot, tmp_path, monkeypatch):
    """假设开关与黄字显示偏好写进当前用户的 user.json，下次刷新恢复。"""
    from lvjiang.apps.yysls.core.combat import equipment as equipment_mod
    from lvjiang.apps.yysls.core.loadout import LoadoutRepository
    from lvjiang.apps.yysls.ui.loadout import loadout_panel as panel_mod
    from lvjiang.core.user_config import User, save_user_metadata

    save_user_metadata(User("tester"), tmp_path)

    class _NamedHost(_Host):
        @staticmethod
        def active_user_name() -> str:
            return "tester"

    monkeypatch.setattr(
        panel_mod, "LoadoutRepository",
        lambda username: LoadoutRepository(username, tmp_path))
    monkeypatch.setattr(
        "lvjiang.apps.yysls.ui.loadout.combat.attrs_tab.LoadoutRepository",
        lambda username: LoadoutRepository(username, tmp_path), raising=False)

    class _Inventory(equipment_mod.EquipmentInventory):
        def __init__(self, user_name: str) -> None:
            self._repo = LoadoutRepository(user_name, tmp_path)
            self.reload()

    monkeypatch.setattr(equipment_mod, "EquipmentInventory", _Inventory)

    import lvjiang.apps.yysls.core.loadout as loadout_pkg
    real_repo = loadout_pkg.LoadoutRepository
    monkeypatch.setattr(loadout_pkg, "LoadoutRepository",
                        lambda username, *a: real_repo(username, tmp_path))

    panel = LoadoutPanel(_NamedHost())
    qtbot.addWidget(panel)
    panel.refresh()
    combat = panel._character._combat_attrs_tab
    assert not combat._chk_full_chengyin.isChecked()

    combat._chk_full_chengyin.setChecked(True)      # 触发 _save_selection
    stored = real_repo("tester", tmp_path).get_combat_prefs()
    assert stored["full_chengyin"] is True

    # 新开一个面板：从该用户的 user.json 恢复
    fresh = LoadoutPanel(_NamedHost())
    qtbot.addWidget(fresh)
    fresh.refresh()
    assert fresh._character._combat_attrs_tab._chk_full_chengyin.isChecked()
