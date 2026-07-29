"""装备调律规则 UI 冒烟测试

对话框实例化 + 各页面 收集→校验→写盘 往返（在 tmp 目录副本上
执行，不触碰真实规则文件），保证 UI 管线不破坏规则语义。
"""

import shutil
from pathlib import Path

import pytest

from src.apps.yysls.game_config import get_game_config
from src.apps.yysls.evaluator.tuning_rules import TuningRuleManager
from src.apps.yysls.ui.rules_editor import TuningRulesDialog
from src.apps.yysls.ui.rules_editor.rule_panel import RulePanel

PROJECT_ROOT = Path(__file__).parents[2]
RULES_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "tuning_rules"

ALL_KEYS = ["huiyi_general", "huixin_small", "huixin_big",
            "heal_pure", "heal_fire"]


@pytest.fixture
def tmp_manager(tmp_path):
    """真实规则的 tmp 副本管理器（写盘不影响真实配置）"""
    for f in RULES_DIR.glob("*.yaml"):
        shutil.copy(f, tmp_path)
    return TuningRuleManager(rules_dir=tmp_path)


class TestDialog:
    def test_dialog_nav(self, qtbot):
        dialog = TuningRulesDialog()
        qtbot.addWidget(dialog)
        # 左侧一级导航：基础配置 + 分割线 + 各规则；
        # StackedWidget 不含分割线（导航行 = 栈页 + 1 偏移）
        assert dialog._nav.count() == len(ALL_KEYS) + 2
        assert dialog._stack.count() == len(ALL_KEYS) + 1
        assert dialog._nav.item(0).text() == "基础配置"
        # 分割线项不可选中
        assert not dialog._nav.item(1).flags()
        # 规则项名称随真实规则文件 name 字段（可被用户改名）
        first_rule = next(iter(
            TuningRuleManager(rules_dir=RULES_DIR).get_rules().values()))
        assert dialog._nav.item(2).text() == first_rule.name
        # 导航切换驱动右侧内容区（跳过分割线偏移）
        dialog._nav.setCurrentRow(2)
        assert dialog._stack.currentIndex() == 1


class TestPanelRoundtrip:
    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_all_pages_apply_saves_ok(self, qtbot, tmp_manager, key):
        # 未改动任何控件时逐页触发收集：校验须通过且保存成功
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel(key, tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        original = tmp_manager.get_rule(key)

        appliers = [
            panel._settings_page._apply_playstyles,
            panel._pool_page._apply,
            panel._common_page._apply,
        ] + [page._apply for page in panel._part_pages]
        for apply in appliers:
            apply()
            assert statuses and not statuses[-1][1], \
                f"{key}: {statuses[-1][0]}"

        # 往返后规则语义不变（玩法设定/池集合/转律词条库/四档条件）
        saved = tmp_manager.get_rule(key)
        assert saved.name == original.name
        assert saved.playstyle_options == original.playstyle_options
        assert saved.pool_set == original.pool_set
        assert saved.transmute_priority == original.transmute_priority
        for tier in ("junk_conditions", "normal_conditions",
                     "excellent_conditions", "top_conditions"):
            # 通用判定往返不变
            assert len(getattr(saved.common, tier)) == \
                len(getattr(original.common, tier))
        assert set(saved.patterns) == set(original.patterns)
        for part, pattern in original.patterns.items():
            saved_p = saved.patterns[part]
            assert set(saved_p.first) == set(pattern.first)
            assert saved_p.default_rating == pattern.default_rating
            for tier in ("junk_conditions", "normal_conditions",
                         "excellent_conditions", "top_conditions"):
                saved_groups = getattr(saved_p, tier)
                orig_groups = getattr(pattern, tier)
                assert len(saved_groups) == len(orig_groups)
                assert [len(g.conditions) for g in saved_groups] == \
                    [len(g.conditions) for g in orig_groups]
                assert [g.when for g in saved_groups] == \
                    [g.when for g in orig_groups]

    def test_clear_first_removes_pattern(self, qtbot, tmp_manager):
        # 首词条清空 = 该部位不定义模式：保存成功且模式移除
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel("huiyi_general", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._part_pages[0]  # 主武器
        page._first = []
        page._apply()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        rule = tmp_manager.get_rule("huiyi_general")
        assert "主武器" not in rule.patterns

    def test_common_page_nav_and_layout(self, qtbot, tmp_manager):
        # 通用判定页：导航在分割线下、主武器之上，无首词条/默认判定，
        # 直接展示判定条件 Tab（全部 + 四档）
        panel = RulePanel("huiyi_general", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        assert panel._nav.item(3).text() == "通用判定"
        assert panel._nav.item(4).text() == "主武器"
        panel._nav.setCurrentRow(3)
        assert panel._stack.currentIndex() == 2
        page = panel._common_page
        assert not hasattr(page, "_first_btn")
        assert not hasattr(page, "_rating_combo")
        bar = page._tier_tabs._bar
        assert bar.count() == 5
        assert bar.tabText(0) == "全部"
        assert len(page._tier_editors) == 4

    def test_tier_tabs_all_view_visibility(self, qtbot, tmp_manager):
        # 「全部」页四档均可见；单档 Tab 只显示对应档
        panel = RulePanel("huiyi_general", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        tabs = panel._common_page._tier_tabs
        assert tabs._bar.currentIndex() == 0
        assert all(not e.isHidden() for e in tabs.editors.values())
        tabs._bar.setCurrentIndex(1)  # 垃圾
        hidden = [k for k, e in tabs.editors.items() if e.isHidden()]
        assert not tabs.editors["junk_conditions"].isHidden()
        assert set(hidden) == {"normal_conditions", "excellent_conditions",
                               "top_conditions"}

    def test_common_page_apply_roundtrip(self, qtbot, tmp_manager):
        # 通用判定四档全空 = 不写 common_conditions；有组时写回并可解析
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel("huiyi_general", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._common_page
        for editor in page._tier_editors.values():
            editor.set_groups([])
        page._apply()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        assert "common_conditions" not in tmp_manager.get_raw("huiyi_general")

        editor = page._tier_editors["junk_conditions"]
        editor.set_groups([{"contains_all": ["劲"]}])
        page._apply()
        assert statuses and not statuses[-1][1], statuses[-1][0]
        rule = tmp_manager.get_rule("huiyi_general")
        assert len(rule.common.junk_conditions) == 1


class TestPanelCreateDelete:
    def test_delete_callback_forwards_key(self, qtbot, tmp_manager):
        deleted: list[str] = []
        panel = RulePanel("heal_pure", tmp_manager,
                                lambda t, e: None, on_delete=deleted.append)
        qtbot.addWidget(panel)
        panel._request_delete()
        assert deleted == ["heal_pure"]


class TestSettingsPageBasics:
    """key/名称只读展示；key 重命名走按钮弹窗，名称重命名走导航双击"""

    def test_key_and_name_readonly_labels(self, qtbot, tmp_manager):
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        rule = tmp_manager.get_rule("heal_pure")
        # QLabel 只读展示（非输入框）
        assert page._key_label.text() == "heal_pure"
        assert page._name_label.text() == rule.name
        assert not hasattr(page, "_key_edit")
        assert not hasattr(page, "_name_edit")

    def test_playstyle_tips_button(self, qtbot, tmp_manager):
        # 玩法设定说明收入「?」按钮（点击展示，不用悬停 tooltip）
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        btn = panel._settings_page._playstyle_tips_btn
        assert btn.text() == "?"
        assert btn.toolTip() == ""

    def test_set_rule_name_saves(self, qtbot, tmp_manager):
        # 导航双击重命名入口：写入工作副本、同步展示、保存生效
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        panel.set_rule_name("新名字")
        assert panel.rule_name == "新名字"
        assert panel._settings_page._name_label.text() == "新名字"
        assert tmp_manager.get_rule("heal_pure").name == "新名字"

    def test_rename_key_via_button(self, qtbot, tmp_manager, monkeypatch):
        # 「重命名」按钮弹窗确认后：文件改名、新 key 生效、展示同步
        from src.apps.yysls.ui.rules_editor import rule_settings_page
        monkeypatch.setattr(
            rule_settings_page.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("heal_pure2", True)))
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        page._rename_key()
        assert panel.rule_key == "heal_pure2"
        assert page._key_label.text() == "heal_pure2"
        assert tmp_manager.get_rule("heal_pure2") is not None
        assert tmp_manager.get_rule("heal_pure") is None

    def test_rename_key_cancel_noop(self, qtbot, tmp_manager, monkeypatch):
        from src.apps.yysls.ui.rules_editor import rule_settings_page
        monkeypatch.setattr(
            rule_settings_page.QInputDialog, "getText",
            staticmethod(lambda *a, **k: ("whatever", False)))
        panel = RulePanel("heal_pure", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        panel._settings_page._rename_key()
        assert panel.rule_key == "heal_pure"
        assert tmp_manager.get_rule("heal_pure") is not None


class TestPlaystyleTableCombos:
    """玩法设定表：武器/增伤词条为下拉格，候选来自游戏配置数据源"""

    def test_combo_candidates_from_data_source(self, qtbot, tmp_manager):
        panel = RulePanel("heal_fire", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        table = page._playstyle_table
        assert table.rowCount() >= 1

        mgr = get_game_config()
        weapons = mgr.get_weapon_types()
        for col in (1, 3):  # 武器列：全量注册表候选
            combo = table.cellWidget(0, col)
            items = [combo.itemText(i) for i in range(combo.count())]
            assert items[0] == ""  # 留空候选
            # 数据源候选全量在列（失效旧值可能额外追加在末尾）
            assert items[1:1 + len(weapons)] == weapons

    def test_damage_candidates_narrowed_by_weapon(self, qtbot, tmp_manager):
        # 火拳 main=扇 / sub=伞，均已绑定武学增效词条 →
        # 增伤列候选收窄为：留空 + 该绑定词条
        panel = RulePanel("heal_fire", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        table = panel._settings_page._playstyle_table
        mgr = get_game_config()
        for weapon_col, dmg_col in ((1, 2), (3, 4)):
            weapon = table.cellWidget(0, weapon_col).currentText()
            bound = mgr.get_weapon_wuxue_affix(weapon)
            assert bound  # 前提：配置中该武器已绑定
            combo = table.cellWidget(0, dmg_col)
            items = [combo.itemText(i) for i in range(combo.count())]
            # 主/副增伤留空项均以占位文案展示
            assert items == ["- 无需增伤 -", bound]

    def test_weapon_change_resets_invalid_damage(self, qtbot, tmp_manager):
        # 先选中绑定增伤，再换武器 → 旧增伤不在新候选内，
        # 重置为留空并写盘 damage=None
        panel = RulePanel("heal_fire", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        table = page._playstyle_table
        mgr = get_game_config()
        old_weapon = table.cellWidget(0, 1).currentText()
        table.cellWidget(0, 2).setCurrentText(
            mgr.get_weapon_wuxue_affix(old_weapon))
        new_weapon = next(w for w in mgr.get_weapon_types()
                          if w != old_weapon)
        table.cellWidget(0, 1).setCurrentText(new_weapon)
        combo = table.cellWidget(0, 2)
        assert combo.currentText() == "- 无需增伤 -"
        items = [combo.itemText(i) for i in range(combo.count())]
        assert items == ["- 无需增伤 -",
                         mgr.get_weapon_wuxue_affix(new_weapon)]
        name = page._cell(table, 0, 0)
        saved = tmp_manager.get_rule("heal_fire")
        assert saved.playstyles[name].main.weapon == new_weapon
        assert saved.playstyles[name].main.damage is None

    def test_sub_damage_no_damage_label_saved_as_none(
            self, qtbot, tmp_manager):
        # 副增伤选「- 无需增伤 -」→ 收集为空，写盘 damage=None
        panel = RulePanel("heal_fire", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        page._playstyle_table.cellWidget(0, 4).setCurrentText("- 无需增伤 -")
        name = page._cell(page._playstyle_table, 0, 0)
        saved = tmp_manager.get_rule("heal_fire")
        assert saved.playstyles[name].sub.damage is None

    def test_attr_column_candidates(self, qtbot, tmp_manager):
        # 属性列（col 5）候选 = 属性攻击组名，无留空项
        from src.apps.yysls.evaluator.tuning_rules import standard_playstyle_attrs
        panel = RulePanel("heal_fire", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        table = panel._settings_page._playstyle_table
        combo = table.cellWidget(0, 5)
        items = [combo.itemText(i) for i in range(combo.count())]
        assert items == standard_playstyle_attrs()
        # 火拳 attr = 牵丝
        assert combo.currentText() == "牵丝"

    def test_combo_selection_saved(self, qtbot, tmp_manager):
        statuses: list[tuple[str, bool]] = []
        panel = RulePanel("heal_fire", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._settings_page
        mgr = get_game_config()
        # 候选已随武器收窄：选同侧武器的绑定增伤词条
        weapon = page._playstyle_table.cellWidget(0, 1).currentText()
        affix = mgr.get_weapon_wuxue_affix(weapon)

        # 下拉选中主增伤词条 → 触发收集→校验→写盘
        page._playstyle_table.cellWidget(0, 2).setCurrentText(affix)
        assert statuses and not statuses[-1][1], statuses[-1][0]
        name = page._cell(page._playstyle_table, 0, 0)
        saved = tmp_manager.get_rule("heal_fire")
        assert saved.playstyles[name].main.damage == affix
