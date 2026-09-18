"""评分内核：同一套装备、同一份假设，所有入口给出同一个毕业率。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.core.graduation import (
    get_graduation_calculator,
    invalidate_graduation_cache,
)
from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
from lvjiang.apps.yysls.core.graduation.context import (
    PlanContextError,
    PlanScoringContext,
    gongjue_attrs,
)
from lvjiang.apps.yysls.core.graduation.optimal_combo import search_optimal_combo
from lvjiang.apps.yysls.core.graduation.scoring import BudgetExceeded, LoadoutScorer
from lvjiang.apps.yysls.core.graduation.smart_tuning import _PlanContext, _rate
from lvjiang.apps.yysls.core.graduation.transmute_optimizer import (
    TransmuteSearchRequest,
    optimize_transmutes,
)
from lvjiang.apps.yysls.core.loadout.models import LoadoutPlan


def _equipped() -> dict:
    """含两把同类武器各带一条剑武学增伤（_stack: max）——向量内环最容易与
    内核分叉的地方，也是 bab06005 修过的回归类型。"""
    return {
        "main_weapon": {
            "type": "剑", "name": "主剑", "level": 110, "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100},
            "affix_2": {"name": "剑武学增伤", "value": 9.0, "unit": "%"},
            "affix_3": {"name": "会意率", "value": 5.0, "unit": "%"},
        },
        "sub_weapon": {
            "type": "剑", "name": "副剑", "level": 105, "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 90},
            "affix_2": {"name": "剑武学增伤", "value": 8.0, "unit": "%"},
            "affix_3": {"name": "劲", "value": 50},
        },
        "ring": {
            "type": "环", "name": "环", "level": 110, "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100},
            "affix_2": {"name": "全武学增效", "value": 4.0, "unit": "%"},
        },
        "pendant": {
            "type": "佩", "name": "佩", "level": 110, "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100},
            "affix_2": {"name": "会心率", "value": 8.0, "unit": "%"},
        },
    }


@pytest.fixture
def calculator():
    invalidate_graduation_cache()
    calc = get_graduation_calculator("鸣金·虹", "基础方案")
    assert calc is not None
    return calc


def test_every_entry_point_scores_the_same(calculator):
    gc = get_game_config()
    base = CombatAttributes(min_outer=1200, max_outer=3000, intent_rate=0.2)
    assumptions = Assumptions(full_level=110, full_chengyin=True,
                              season_chengyin=True)
    equipped = _equipped()
    projected = assumptions.project(equipped, gc)

    scorer = LoadoutScorer(calculator, base, "鸣金·虹", gc)
    reference = scorer.rate(projected)

    # 智能调律的方案评分
    context = _PlanContext(
        "p", "方案", "鸣金·虹", calculator, base, projected, 0.0)
    assert _rate(context, projected, gc) == pytest.approx(reference)
    # 转律建议的基线（同一份假设由优化器自行投影）
    result = optimize_transmutes(TransmuteSearchRequest(
        equipped=copy.deepcopy(equipped), calculator=calculator,
        base_attrs=base, school="鸣金·虹", game_config=gc,
        full_level=110, full_chengyin=True, season_chengyin=True))
    assert result.baseline_rate == pytest.approx(reference)
    # 最优组合：每槽只有这一件候选，搜索结果就是这套装备
    combos = search_optimal_combo(
        {slot: [equip] for slot, equip in equipped.items()},
        calculator, base, use_dominance_pruning=False,
        full_level=110, full_chengyin=True, season_chengyin=True,
        season_level=110)
    assert combos and combos[0]["rate"] == pytest.approx(reference)
    # 两把剑的剑武学增伤只生效一条：内核与向量内环都不得相加
    assert scorer.attrs(projected).extra_attrs["剑武学增伤"] < 0.1


def test_scorer_caches_by_attribute_signature(calculator):
    counting = SimpleNamespace(calls=0)
    real = calculator.calculate

    def counted(attrs):
        counting.calls += 1
        return real(attrs)

    scorer = LoadoutScorer(
        SimpleNamespace(calculate=counted),
        CombatAttributes(min_outer=1200, max_outer=3000, intent_rate=0.2),
        "鸣金·虹")
    equipped = _equipped()
    first = scorer.rate(equipped)
    # 同一属性输入（不同槽位摆放、词条顺序不同）不再求值
    swapped = copy.deepcopy(equipped)
    swapped["ring"], swapped["pendant"] = (
        {**swapped["pendant"], "type": "环"}, {**swapped["ring"], "type": "佩"})
    assert scorer.rate(swapped) == first
    assert scorer.rate(copy.deepcopy(equipped)) == first
    assert scorer.evaluated == counting.calls == 1
    # 属性真的变了才求值
    changed = copy.deepcopy(equipped)
    changed["pendant"]["affix_1"]["value"] = 60
    assert scorer.rate(changed) != first
    assert scorer.evaluated == 2


def test_scorer_budget_and_stop_check(calculator):
    scorer = LoadoutScorer(
        calculator, CombatAttributes(), "鸣金·虹", stop_check=lambda: True)
    with pytest.raises(BudgetExceeded):
        scorer.rate(_equipped())


def test_plan_context_from_plan_includes_fixed_gongjue(monkeypatch):
    gc = get_game_config()
    base = {"min_outer": 1200.0, "max_outer": 3000.0, "intent_rate": 0.2}
    # 基础属性是会话数据，测试环境为空，打桩成一份固定值
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.context.get_play_styles",
        lambda school: {"无名1.0": base} if school == "鸣金·虹" else {})
    plan = LoadoutPlan(
        id="p1", name="测试方案", main_martial_art="无名剑法",
        sub_martial_art="无名枪法", playstyle="无名",
        base_attribute="无名1.0", gongjue="会意", graduation_scheme="基础方案")
    context = PlanScoringContext.from_plan(plan, game_config=gc)
    assert context.school == "鸣金·虹" and context.attribute == "鸣金"
    assert context.gongjue == "会意" and context.playstyle == "无名"
    expected = gongjue_attrs("会意", gc)
    assert expected.intent_rate > 0
    assert context.base_attrs.intent_rate == pytest.approx(
        0.2 + expected.intent_rate)
    assert context.scorer(game_config=gc).school == "鸣金·虹"

    with pytest.raises(PlanContextError):
        PlanScoringContext.from_plan(
            LoadoutPlan(id="p2", name="无流派"), game_config=gc)
    with pytest.raises(PlanContextError):
        PlanScoringContext.from_plan(LoadoutPlan(
            id="p3", name="无方案", main_martial_art="无名剑法",
            sub_martial_art="无名枪法", base_attribute="不存在",
            graduation_scheme="基础方案"), game_config=gc)
