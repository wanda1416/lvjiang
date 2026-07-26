"""装备调律规则 UI 冒烟测试

对话框实例化 + 各页面 收集→校验→写盘 往返（在 tmp 目录副本上
执行，不触碰真实规则文件），保证 UI 管线不破坏规则语义。
"""

import shutil
from pathlib import Path

import pytest

from src.apps.yysls.evaluator.rules import TuningRuleManager
from src.apps.yysls.ui.tuning_rules import TuningRulesDialog
from src.apps.yysls.ui.tuning_rules.school_rule_panel import SchoolRulePanel

PROJECT_ROOT = Path(__file__).parents[2]
RULES_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "tuning_rules"


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
        assert dialog._tabs.count() == 4
        assert dialog._tabs.tabText(0) == "会意流派-通用"
        # Tab 栏右上角的调律验证入口
        assert dialog._tabs.cornerWidget() is dialog._btn_judge_test
        assert dialog._btn_judge_test.text() == "装备调律验证"


class TestPanelRoundtrip:
    @pytest.mark.parametrize("key", [
        "huiyi_general", "huixin_big", "huixin_small", "heal",
    ])
    def test_all_pages_apply_saves_ok(self, qtbot, tmp_manager, key):
        # 未改动任何控件时逐页触发收集：校验须通过且保存成功
        statuses: list[tuple[str, bool]] = []
        panel = SchoolRulePanel(key, tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        original = tmp_manager.get_rule(key)

        appliers = [
            panel._settings_page._apply_basic,
            panel._settings_page._apply_subs,
            panel._settings_page._apply_weapons,
            panel._pool_page._apply,
        ] + [page._apply for page in panel._part_pages]
        for apply in appliers:
            apply()
            assert statuses and not statuses[-1][1], \
                f"{key}: {statuses[-1][0]}"

        # 往返后规则语义不变（池集合/模式部位/必选槽/变体集合）
        saved = tmp_manager.get_rule(key)
        assert saved.name == original.name
        assert set(saved.variants) == set(original.variants)
        for vk, variant in original.variants.items():
            saved_v = saved.variants[vk]
            assert saved_v.pool_set == variant.pool_set
            assert saved_v.optional_pool_set == variant.optional_pool_set
            assert saved_v.transmute_priority == variant.transmute_priority
            assert set(saved_v.patterns) == set(variant.patterns)
            for part, pattern in variant.patterns.items():
                saved_p = saved_v.patterns[part]
                assert set(saved_p.first) == set(pattern.first)
                assert sorted(map(sorted, saved_p.required)) == \
                    sorted(map(sorted, pattern.required))
                assert saved_p.required_damage == pattern.required_damage
                assert saved_p.optional_n == pattern.optional_n
                assert len(saved_p.top) == len(pattern.top)

    def test_invalid_change_not_saved(self, qtbot, tmp_manager):
        # 制造非法改动（清空首词条）：校验失败、状态标错、不写盘
        statuses: list[tuple[str, bool]] = []
        panel = SchoolRulePanel("huiyi_general", tmp_manager,
                                lambda t, e: statuses.append((t, e)))
        qtbot.addWidget(panel)
        page = panel._part_pages[0]  # 主武器
        for cb in page._first_checks.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        page._apply()
        assert statuses and statuses[-1][1]
        rule = tmp_manager.get_rule("huiyi_general")
        assert rule.variants["default"].patterns["主武器"].first == ["大外"]
