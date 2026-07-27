"""装备调律规则 UI 冒烟测试

对话框实例化 + 各页面 收集→校验→写盘 往返（在 tmp 目录副本上
执行，不触碰真实规则文件），保证 UI 管线不破坏规则语义。
"""

import shutil
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QPushButton

from src.apps.yysls.evaluator.attr_rules import get_attr_rule_manager
from src.apps.yysls.evaluator.rules import TuningRuleManager
from src.apps.yysls.ui.tuning_rules import TuningRulesDialog
from src.apps.yysls.ui.tuning_rules.school_rule_panel import SchoolRulePanel

PROJECT_ROOT = Path(__file__).parents[2]
RULES_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "tuning_rules"

ALL_KEYS = ["huiyi_general", "lieshi_small", "lieshi_big",
            "heal_pure", "heal_fire"]


@pytest.fixture
def tmp_manager(tmp_path):
    """真实规则的 tmp 副本管理器（写盘不影响真实配置）"""
    for f in RULES_DIR.glob("*.yaml"):
        shutil.copy(f, tmp_path)
    return TuningRuleManager(rules_dir=tmp_path)


class TestDialog:
    def test_dialog_tabs(self, qtbot):
        dialog = TuningRulesDialog()
        qtbot.addWidget(dialog)
        assert dialog._tabs.count() == len(ALL_KEYS)
        assert dialog._tabs.tabText(0) == "会意通用"
        # Tab 栏右上角：新增规则 + 调律验证入口
        corner = dialog._tabs.cornerWidget()
        texts = [b.text() for b in corner.findChildren(QPushButton)]
        assert texts == ["＋ 新增规则", "装备调律验证"]


class TestPanelRoundtrip:
    @pytest.mark.parametrize("key", ALL_KEYS)
    def test_all_pages_apply_saves_ok(self, qtbot, tmp_manager, key):
        # 未改动任何控件时逐页触发收集：校验须通过且保存成功
        statuses: list[tuple[str, bool]] = []
        panel = SchoolRulePanel(key, tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        original = tmp_manager.get_rule(key)

        appliers = [
            panel._settings_page._apply_basic,
            panel._settings_page._apply_weapon_rules,
            panel._pool_page._apply,
        ] + [page._apply for page in panel._part_pages]
        for apply in appliers:
            apply()
            assert statuses and not statuses[-1][1], \
                f"{key}: {statuses[-1][0]}"

        # 往返后规则语义不变（武器规则/池集合/转律优先级/三档条件）
        saved = tmp_manager.get_rule(key)
        assert saved.name == original.name
        assert saved.weapon_rule_options == original.weapon_rule_options
        assert saved.pool_set == original.pool_set
        assert saved.transmute_priority == original.transmute_priority
        assert set(saved.patterns) == set(original.patterns)
        for part, pattern in original.patterns.items():
            saved_p = saved.patterns[part]
            assert set(saved_p.first) == set(pattern.first)
            for tier in ("junk_conditions", "usable_conditions",
                         "top_conditions"):
                saved_groups = getattr(saved_p, tier)
                orig_groups = getattr(pattern, tier)
                assert len(saved_groups) == len(orig_groups)
                assert [len(g) for g in saved_groups] == \
                    [len(g) for g in orig_groups]

    def test_invalid_change_not_saved(self, qtbot, tmp_manager):
        # 制造非法改动（清空首词条）：校验失败、状态标错、不写盘
        statuses: list[tuple[str, bool]] = []
        panel = SchoolRulePanel("huiyi_general", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._part_pages[0]  # 主武器
        page._first = []
        page._apply()
        assert statuses and statuses[-1][1]
        rule = tmp_manager.get_rule("huiyi_general")
        assert rule.patterns["主武器"].first == ["最大外功攻击"]


class TestPanelCreateDelete:
    def test_delete_callback_forwards_key(self, qtbot, tmp_manager):
        deleted: list[str] = []
        panel = SchoolRulePanel("heal_pure", tmp_manager,
                                lambda t, e: None, on_delete=deleted.append)
        qtbot.addWidget(panel)
        panel._request_delete()
        assert deleted == ["heal_pure"]


class TestWeaponTableCombos:
    """武器规则表：武器/增伤词条为下拉格，候选来自游戏配置数据源"""

    def test_combo_candidates_from_data_source(self, qtbot, tmp_manager):
        panel = SchoolRulePanel("heal_fire", tmp_manager, lambda t, e: None)
        qtbot.addWidget(panel)
        page = panel._settings_page
        table = page._weapon_table
        assert table.rowCount() >= 1

        mgr = get_attr_rule_manager()
        weapons = mgr.get_weapon_types()
        wuxue = mgr.get_wuxue_affix_names()
        for col, source in ((1, weapons), (2, wuxue),
                            (3, weapons), (4, wuxue)):
            combo = table.cellWidget(0, col)
            items = [combo.itemText(i) for i in range(combo.count())]
            assert items[0] == ""  # 留空候选
            # 数据源候选全量在列（失效旧值可能额外追加在末尾）
            assert items[1:1 + len(source)] == source

    def test_combo_selection_saved(self, qtbot, tmp_manager):
        statuses: list[tuple[str, bool]] = []
        panel = SchoolRulePanel("heal_fire", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._settings_page
        mgr = get_attr_rule_manager()
        affix = mgr.get_wuxue_affix_names()[0]

        # 下拉选中主增伤词条 → 触发收集→校验→写盘
        page._weapon_table.cellWidget(0, 2).setCurrentText(affix)
        assert statuses and not statuses[-1][1], statuses[-1][0]
        name = page._cell(page._weapon_table, 0, 0)
        saved = tmp_manager.get_rule("heal_fire")
        assert saved.weapon_rules[name].main.damage == affix
