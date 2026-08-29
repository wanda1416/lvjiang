"""系统内容删除守卫：用户模式下不给删除系统配置的入口

后端已拦下删除（见 tests/core/test_config_resolver.py::TestDeletionAllowlist），
但用户点了按钮却静默失败是糟糕的体验——面板要把按钮置灰并说明替代方案。
开发模式不受限：编排系统配置是开发者的职责。
"""

import shutil
from pathlib import Path

import pytest

from lvjiang.apps.yysls.ui.game_settings.factory_guard import (
    GAME_CONFIG_REL,
    READONLY_HINT,
    deletable,
    factory_dict_keys,
    factory_list_values,
)

ATTRS_FILE = Path("config/system/yysls/game_config.yaml")


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """把 resolver 根指向 tmp 副本

    本仓库带 .git 即开发模式，面板保存会**直接写 config/system**。
    任何会触发 _apply()/_save_data() 的用例都必须先用本 fixture 隔离，
    否则测试会把脏数据写进真实系统配置。
    """
    import lvjiang.core.config.resolver as cr
    dst = tmp_path / "system" / "yysls" / "game_config.yaml"
    dst.parent.mkdir(parents=True)
    shutil.copy(ATTRS_FILE, dst)
    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")
    monkeypatch.setattr(cr, "_resolver", None)
    return dst


class TestFactoryLookup:
    def test_dict_keys_read_from_system_only(self):
        keys = factory_dict_keys(GAME_CONFIG_REL, "schools")
        assert "鸣金·虹" in keys

    def test_list_values_by_field(self):
        names = factory_list_values(GAME_CONFIG_REL, "weapon_types", field="name")
        assert "剑" in names

    def test_missing_node_returns_empty(self):
        assert factory_dict_keys(GAME_CONFIG_REL, "查无此节点") == set()
        assert factory_list_values(GAME_CONFIG_REL, "查无此节点", field="x") == set()

    def test_wrong_type_returns_empty(self):
        # weapon_types 是列表，按 dict 取应返回空集而不是抛异常
        assert factory_dict_keys(GAME_CONFIG_REL, "weapon_types") == set()


class TestDeletable:
    FACTORY = {"系统甲", "系统乙"}

    def test_factory_entry_blocked_in_user_mode(self, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.apps.yysls.ui.game_settings.factory_guard.is_user_mode",
            lambda: True)
        ok, hint = deletable("系统甲", self.FACTORY)
        assert not ok
        assert hint

    def test_user_added_entry_allowed_in_user_mode(self, monkeypatch):
        """用户自建条目照常可删——守卫只挡系统内容。"""
        monkeypatch.setattr(
            "lvjiang.apps.yysls.ui.game_settings.factory_guard.is_user_mode",
            lambda: True)
        ok, hint = deletable("我自己加的", self.FACTORY)
        assert ok
        assert hint == ""

    def test_dev_mode_allows_everything(self, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.apps.yysls.ui.game_settings.factory_guard.is_user_mode",
            lambda: False)
        assert deletable("系统甲", self.FACTORY)[0]

    def test_none_selection_not_treated_as_factory(self, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.apps.yysls.ui.game_settings.factory_guard.is_user_mode",
            lambda: True)
        assert deletable(None, self.FACTORY)[0]

    def test_custom_hint_passed_through(self, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.apps.yysls.ui.game_settings.factory_guard.is_user_mode",
            lambda: True)
        assert deletable("系统甲", self.FACTORY, hint=READONLY_HINT)[1] == READONLY_HINT


class TestPanelsDisableDeleteForFactoryEntries:
    """五个面板的删除按钮在选中系统条目时置灰。"""

    @pytest.fixture(autouse=True)
    def _user_mode(self, monkeypatch):
        monkeypatch.setattr(
            "lvjiang.apps.yysls.ui.game_settings.factory_guard.is_user_mode",
            lambda: True)

    def test_school_panel(self, qtbot):
        from lvjiang.apps.yysls.ui.game_settings.school_panel import SchoolPanel
        panel = SchoolPanel()
        qtbot.addWidget(panel)
        names = panel._names
        assert names, "系统应有流派"
        panel._school_list.setCurrentRow(names.index("鸣金·虹"))
        assert not panel._btn_del.isEnabled()
        assert panel._btn_del.toolTip()

    def test_base_attr_panel_weapon(self, qtbot):
        from lvjiang.apps.yysls.ui.game_settings.base_attr_panel import (
            BaseAttrPanel,
        )
        panel = BaseAttrPanel()
        qtbot.addWidget(panel)
        texts = [panel._weapon_list.item(i).text()
                 for i in range(panel._weapon_list.count())]
        idx = next(i for i, t in enumerate(texts) if t.startswith("剑"))
        panel._weapon_list.setCurrentRow(idx)
        assert not panel._btn_del_weapon.isEnabled()

    def test_affix_caps_panel(self, qtbot):
        from lvjiang.apps.yysls.ui.game_settings.affix_caps_panel import (
            AffixCapsPanel,
        )
        panel = AffixCapsPanel()
        qtbot.addWidget(panel)
        rows = [panel._affix_list.item(i).text()
                for i in range(panel._affix_list.count())]
        assert "会心率" in rows
        panel._affix_list.setCurrentRow(rows.index("会心率"))
        assert not panel._btn_del_affix.isEnabled()


class TestTableRowPanels:
    """等级/赛季配置表：身份要从 cellWidget 的 QSpinBox 取

    这两张表所有列都是 setCellWidget，``item()`` 恒为 None——早先误用
    ``item(row, 0)`` 读文本，身份永远是 None，守卫整个空转（删除按钮
    对系统行始终可用）。本用例锁住修复。

    这里用**真实用户模式**（LVJIANG_DEV_MODE=0）而不是打桩 is_user_mode：
    打桩会造出「判定按用户模式、写盘却按开发模式」的矛盾状态——新增的行
    被直接写进 system，立刻变成"系统内容"，现实中不会发生。
    """

    @pytest.fixture(autouse=True)
    def _user_mode(self, isolated_config, monkeypatch):
        import lvjiang.core.config.resolver as cr
        monkeypatch.setenv("LVJIANG_DEV_MODE", "0")
        monkeypatch.setattr(cr, "_resolver", None)

    def _panel(self, qtbot, cls):
        panel = cls()
        qtbot.addWidget(panel)
        return panel

    def test_level_panel_blocks_factory_row(self, qtbot):
        from lvjiang.apps.yysls.ui.game_settings.level_config_panel import (
            LevelConfigPanel,
        )
        panel = self._panel(qtbot, LevelConfigPanel)
        factory = factory_list_values(GAME_CONFIG_REL, "level_configs", field="level")
        assert factory, "系统应有等级配置"
        target = next(
            r for r in range(panel._table.rowCount())
            if panel._table.cellWidget(r, 1).value() in factory)
        panel._table.setCurrentCell(target, 1)
        assert not panel._del_btn.isEnabled()
        assert panel._del_btn.toolTip()

    def test_level_panel_allows_user_added_row(self, qtbot):
        """用户模式下新增行写进 local，不是系统内容，应可删除。"""
        from lvjiang.apps.yysls.ui.game_settings.level_config_panel import (
            LevelConfigPanel,
        )
        panel = self._panel(qtbot, LevelConfigPanel)
        factory = factory_list_values(GAME_CONFIG_REL, "level_configs", field="level")
        panel._on_add_row()
        row = panel._table.rowCount() - 1
        panel._table.cellWidget(row, 1).setValue(
            next(v for v in range(1, 1000) if v not in factory))
        panel._table.setCurrentCell(row, 1)
        panel._refresh_del_enabled(row)
        assert panel._del_btn.isEnabled(), "用户自建行应可删除"

    def test_season_panel_blocks_factory_row(self, qtbot):
        from lvjiang.apps.yysls.ui.game_settings.season_config_panel import (
            SeasonConfigPanel,
        )
        panel = self._panel(qtbot, SeasonConfigPanel)
        factory = factory_list_values(
            GAME_CONFIG_REL, "season_configs", field="season_number")
        assert factory, "系统应有赛季配置"
        target = next(
            r for r in range(panel._table.rowCount())
            if panel._table.cellWidget(r, 1).value() in factory)
        panel._table.setCurrentCell(target, 1)
        assert not panel._del_btn.isEnabled()

    def test_dev_mode_allows_deleting_factory_row(self, qtbot, monkeypatch):
        """开发模式即 system 身份，删除不受限。"""
        import lvjiang.core.config.resolver as cr
        from lvjiang.apps.yysls.ui.game_settings.level_config_panel import (
            LevelConfigPanel,
        )
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        monkeypatch.setattr(cr, "_resolver", None)
        panel = self._panel(qtbot, LevelConfigPanel)
        panel._table.setCurrentCell(0, 1)
        assert panel._del_btn.isEnabled()
