"""扫描处理覆盖提示只对部位和品阶作静态结论。"""

from lvjiang.apps.yysls.core.tuning_rules import QUALITY_PARTS, BehaviorRule
from lvjiang.apps.yysls.ui.tune_settings.scan_coverage import (
    scan_quality_coverage,
)


def _matching(rules, part, quality):
    return next(cell.rule_numbers for cell in scan_quality_coverage(rules)
                if (cell.part, cell.quality) == (part, quality))


def test_parts_and_quality_cover_only_their_own_branches():
    rules = [
        BehaviorRule(parts=["武器"], max_quality="gold_only",
                     ratings=["junk"], action="recycle"),
        BehaviorRule(parts=["胸甲"], max_quality="purple_only",
                     judge_scope="affix", ratings=["最大外功攻击"],
                     action="skip"),
        BehaviorRule(parts=["武器"], max_quality="purple",
                     ratings=["excellent"], action="skip"),
    ]
    assert _matching(rules, "武器", "gold") == (1,)
    assert _matching(rules, "武器", "purple") == (3,)
    assert _matching(rules, "胸甲", "purple") == (2,)
    assert _matching(rules, "胸甲", "gold") == ()
    assert len(scan_quality_coverage(rules)) == len(QUALITY_PARTS) * 2


def test_disabled_and_pure_rating_conditions_do_not_fake_coverage():
    rules = [
        BehaviorRule(enabled=False, action="skip"),
        BehaviorRule(max_quality="purple_only", pct_op="ge", pct=90,
                     judge_scope="incoming", ratings=["junk"],
                     action="recycle"),
    ]
    assert _matching(rules, "腕甲", "gold") == ()
    assert _matching(rules, "腕甲", "purple") == (2,)


def test_unlimited_quality_covers_both_checked_qualities():
    rules = [BehaviorRule(max_quality="gold", ratings=[], action="skip")]
    assert _matching(rules, "佩", "gold") == (1,)
    assert _matching(rules, "佩", "purple") == (1,)
