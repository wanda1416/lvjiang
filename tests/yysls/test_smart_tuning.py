from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.core.graduation.smart_search import (
    GreedySwapStrategy,
    SearchBudget,
    SearchOutcome,
    SearchProblem,
    SearchStatus,
    SearchStrategy,
    floor_rate,
    get_strategy,
    passes,
    register_strategy,
)
from lvjiang.apps.yysls.core.graduation.smart_tuning import (
    SmartPlanResult,
    SmartTuningEvaluator,
    SmartTuningStatus,
    _PlanContext,
    _SelectedTarget,
)
from lvjiang.apps.yysls.core.tuning_rules import (
    SmartTuningConfig,
    SmartTuningEvaluation,
    parse_tune_config,
)


def _config(*, operator: str = "gt") -> SmartTuningConfig:
    return SmartTuningConfig(
        enabled=True,
        evaluation=SmartTuningEvaluation(enabled=True, operator=operator),
    )


def _bare_evaluator() -> SmartTuningEvaluator:
    evaluator = SmartTuningEvaluator.__new__(SmartTuningEvaluator)
    evaluator.config = _config()
    evaluator._stop_check = lambda: False
    evaluator._game_config = SimpleNamespace(
        get_affix_caps=lambda _level, name: {
            "cap": 100.0, "chengyin": 94.0 + len(name), "unit": "%",
        },
        get_playstyle=lambda _name: None,
        current_equip_level=lambda: 110,
        # 转律库并集：转律分支只能转入这里有的词条；“垃圾”“池外”都不在库里
        get_all_transmute_pools=lambda: {"流派": ["A", "B", "C", "D", "E"]},
    )
    evaluator._contexts = ()
    evaluator._disabled_reason = ""
    evaluator._other_equipped_cache = {}
    evaluator._strategy = get_strategy()
    return evaluator


def _patch_affix_rules(monkeypatch, candidates, *, legal=True):
    """候选/校验在智能调律与公共转律过滤链两处都被引用，需同时打桩。"""
    for module in (
        "lvjiang.apps.yysls.core.graduation.smart_tuning",
        "lvjiang.apps.yysls.core.loadout.transmute",
    ):
        monkeypatch.setattr(
            f"{module}.validate_combination_dict", lambda _equip: [])
        monkeypatch.setattr(
            f"{module}.normal_affix_candidates",
            lambda _equip, _gc, _c=candidates: list(_c))


def _context(plan_id: str, main_type: str, sub_type: str) -> _PlanContext:
    return _PlanContext(
        plan_id, plan_id, "流派", object(), object(),
        {
            "main_weapon": {"type": main_type},
            "sub_weapon": {"type": sub_type},
        },
        1.0,
        affix_pool=("A", "B", "C"),
        plan_maximum_rate=1.0,
    )


def test_parse_smart_tuning_public_capability_defaults_to_enabled(
        base_tune_config):
    parsed = parse_tune_config(base_tune_config)
    assert parsed.smart_tuning.enabled is True
    assert parsed.smart_tuning.plan_scope == "incoming"
    assert parsed.smart_tuning.evaluation.operator == "gt"
    assert parsed.smart_tuning.evaluation.precision == 0.001
    assert parsed.smart_tuning.failure_action.action == "skip"
    assert parsed.smart_tuning.failure_action.keep_min_rating == "excellent"


def test_parse_smart_tuning_rejects_unknown_operator(base_tune_config):
    base_tune_config["smart_tuning"] = {
        "enabled": True,
        "evaluation": {"enabled": True, "operator": "equal"},
    }
    with pytest.raises(ValueError, match="operator"):
        parse_tune_config(base_tune_config)


def test_parse_smart_tuning_rejects_unknown_precision(base_tune_config):
    base_tune_config["smart_tuning"] = {
        "enabled": True,
        "evaluation": {"enabled": True, "precision": 0.005},
    }
    with pytest.raises(ValueError, match="precision"):
        parse_tune_config(base_tune_config)


def test_parse_smart_tuning_keep_min_rating(base_tune_config):
    base_tune_config["smart_tuning"] = {
        "failure_action": {
            "action": "tune_full_recycle",
            "keep_min_rating": "top",
        },
    }
    parsed = parse_tune_config(base_tune_config)
    assert parsed.smart_tuning.failure_action.keep_min_rating == "top"


def test_parse_smart_tuning_rejects_unknown_keep_min_rating(
        base_tune_config):
    base_tune_config["smart_tuning"] = {
        "failure_action": {"keep_min_rating": "great"},
    }
    with pytest.raises(ValueError, match="keep_min_rating"):
        parse_tune_config(base_tune_config)


def test_graduation_rate_precision_floors_both_sides_before_comparison():
    assert str(floor_rate(0.9459, 0.001)) == "0.945"
    assert str(floor_rate(0.9599, 0.01)) == "0.95"
    assert passes(0.9459, 0.9451, "gte", 0.001) is True
    assert passes(0.9459, 0.9451, "gt", 0.001) is False
    assert passes(0.9501, 0.9499, "gt", 0.001) is True


def test_empty_incoming_playstyles_match_rule_judge_all_semantics(monkeypatch):
    evaluator = _bare_evaluator()
    rule = SimpleNamespace(
        name="会意", playstyles=["纯唐", "威威"],
        affix_pool=["最大外功攻击", "势", "会意率"],
    )
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.smart_tuning.get_tuning_rule_manager",
        lambda: SimpleNamespace(get_rules=lambda: {"huiyi": rule}),
    )
    targets = evaluator._selected_targets({"huiyi": {"playstyles": []}})
    assert targets == (
        _SelectedTarget(
            "huiyi", "会意", "纯唐",
            ("最大外功攻击", "势", "会意率")),
        _SelectedTarget(
            "huiyi", "会意", "威威",
            ("最大外功攻击", "势", "会意率")),
    )


def test_weapon_candidate_only_evaluates_matching_plan_slot(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._contexts = (
        _context("威威", "陌刀", "枪"),
        _context("纯唐", "横刀", "剑"),
    )
    seen = []

    def fake_evaluate(context, slot, _equipment):
        seen.append((context.plan_id, slot))
        return SmartPlanResult(
            context.plan_id, context.plan_name,
            SmartTuningStatus.NO_IMPROVEMENT,
        )

    monkeypatch.setattr(evaluator, "_evaluate_plan", fake_evaluate)
    outcome = evaluator.evaluate("main_weapon", {"type": "枪"})
    assert outcome.status is SmartTuningStatus.NO_IMPROVEMENT
    assert seen == [("威威", "sub_weapon")]


def test_same_weapon_type_evaluates_both_slots(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._contexts = (_context("双剑", "剑", "剑"),)
    seen = []

    def fake_evaluate(context, slot, _equipment):
        seen.append(slot)
        return SmartPlanResult(
            context.plan_id, context.plan_name,
            SmartTuningStatus.NO_IMPROVEMENT,
        )

    monkeypatch.setattr(evaluator, "_evaluate_plan", fake_evaluate)
    outcome = evaluator.evaluate("main_weapon", {"type": "剑"})
    assert outcome.status is SmartTuningStatus.NO_IMPROVEMENT
    assert seen == ["main_weapon", "sub_weapon"]


def test_improving_plan_does_not_hide_later_plan_details(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._contexts = (
        _context("方案A", "剑", "枪"),
        _context("方案B", "剑", "枪"),
    )
    seen = []

    def fake_evaluate(context, _slot, _equipment):
        seen.append(context.plan_id)
        status = (SmartTuningStatus.IMPROVES if context.plan_id == "方案A"
                  else SmartTuningStatus.NO_IMPROVEMENT)
        return SmartPlanResult(context.plan_id, context.plan_name, status)

    monkeypatch.setattr(evaluator, "_evaluate_plan", fake_evaluate)
    outcome = evaluator.evaluate("main_weapon", {"type": "剑"})

    assert outcome.status is SmartTuningStatus.IMPROVES
    assert seen == ["方案A", "方案B"]
    assert [item.plan_name for item in outcome.plans] == ["方案A", "方案B"]


def test_nonweapon_first_affix_mismatch_is_not_applicable(monkeypatch):
    evaluator = _bare_evaluator()
    context = _context("纯奶", "剑", "枪")
    context = _PlanContext(
        **{**context.__dict__, "first_affixes": {"ring": ("劲", "敏")}},
    )
    evaluator._contexts = (context,)
    monkeypatch.setattr(
        evaluator, "_evaluate_plan",
        lambda *_args: pytest.fail("首词条不匹配不应进入毕业率计算"),
    )

    outcome = evaluator.evaluate("ring", {
        "type": "环", "affix_1": {"name": "体", "value": 100},
    })

    assert outcome.status is SmartTuningStatus.UNKNOWN
    assert outcome.plans[0].status is SmartTuningStatus.NOT_APPLICABLE
    assert outcome.plans[0].reason == "当前装备不适用该方案：首词条不符合"


def test_weapon_not_used_by_any_plan_fails_open(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._contexts = (_context("威威", "陌刀", "枪"),)
    monkeypatch.setattr(
        evaluator, "_evaluate_plan",
        lambda *_args: pytest.fail("不适用方案不应进入毕业率计算"),
    )
    outcome = evaluator.evaluate("main_weapon", {"type": "剑"})
    assert outcome.status is SmartTuningStatus.UNKNOWN
    assert "能装备" in outcome.reason


def test_pause_blocking_time_does_not_consume_plan_budget(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator.config = _config(operator="gt")
    evaluator._stop_check = lambda: (time.sleep(0.02) or False)
    evaluator._candidate_rate = lambda *_args: 2.0
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.smart_tuning._MAX_PLAN_SECONDS",
        0.005,
    )
    _patch_affix_rules(monkeypatch, ["A"])
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        **{
            f"affix_{index}": {"name": f"词条{index}", "value": 1}
            for index in range(1, 5)
        },
    }
    result = evaluator._evaluate_plan(
        _context("方案", "剑", "枪"), "ring", equipment)
    assert result.status is SmartTuningStatus.IMPROVES


def test_full_equipment_does_not_run_smart_graduation_search(monkeypatch):
    evaluator = _bare_evaluator()

    class Unexpected(SearchStrategy):
        def search(self, _problem):
            pytest.fail("五词条终局装备不应执行智能毕业率推演")

    evaluator._strategy = Unexpected()
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.smart_tuning.validate_combination_dict",
        lambda _equip: [],
    )
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        **{
            f"affix_{index}": {"name": f"词条{index}", "value": 1}
            for index in range(1, 6)
        },
    }

    result = evaluator._evaluate_plan(
        _context("方案", "剑", "枪"), "ring", equipment)

    assert result.status is SmartTuningStatus.UNKNOWN
    assert "词条已满" in result.reason


def test_hypothetical_affix_uses_exact_chengyin_cap(monkeypatch):
    evaluator = _bare_evaluator()
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.smart_tuning.validate_combination_dict",
        lambda _equip: [],
    )
    result = evaluator._with_affixes(
        {"type": "环", "level": 110}, ("会心率",))
    assert result is not None
    # 必须直接使用 caps["chengyin"]，不得再用 cap * 0.94 推导。
    assert result["affix_1"]["value"] == 97.0


def test_plan_candidates_are_intersection_of_part_and_rule_pool(monkeypatch):
    evaluator = _bare_evaluator()
    captured = {}

    class Capture(SearchStrategy):
        def search(self, problem):
            captured["candidates"] = problem.candidates
            return SearchOutcome(SearchStatus.IMPROVES, "ok", 2.0, 1)

    evaluator._strategy = Capture()
    _patch_affix_rules(monkeypatch, ["最大牵丝攻击", "最大鸣金攻击", "敏", "会心率"])
    context = _PlanContext(
        "p", "方案", "牵丝·翊", object(), object(), {}, 1.0,
        rule_key="huixin", rule_name="会心",
        affix_pool=("最大本属攻击", "敏", "会心率"),
        attribute="牵丝",
        plan_maximum_rate=1.0,
    )
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "X", "value": 1},
    }

    result = evaluator._evaluate_plan(context, "ring", equipment)

    assert result.status is SmartTuningStatus.IMPROVES
    assert captured["candidates"] == ["最大牵丝攻击", "敏", "会心率"]


def test_transmute_search_removes_only_first_affix_outside_rule_pool(
        monkeypatch):
    evaluator = _bare_evaluator()
    seen = []

    class Capture(SearchStrategy):
        def search(self, problem):
            seen.append({
                str(affix.get("name"))
                for key, affix in problem.equipment.items()
                if key.startswith("affix_") and isinstance(affix, dict)
            })
            return SearchOutcome(
                SearchStatus.NO_IMPROVEMENT, "checked", 0.5, 1)

    evaluator._strategy = Capture()
    _patch_affix_rules(monkeypatch, ["A", "B", "C", "D", "E"])
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 1.0,
        affix_pool=("A", "B", "C", "D", "E"), plan_maximum_rate=1.0)
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "首词条", "value": 1},
        "affix_2": {"name": "池外一", "value": 1},
        "affix_3": {"name": "池外二", "value": 1},
        "affix_4": {"name": "A", "value": 1},
    }

    evaluator._evaluate_plan(context, "ring", equipment)

    # 第 0 分支 + 第一条池外词条转成库内每个可用目标（B/C/D/E）的分支
    assert len(seen) == 5
    assert "池外一" in seen[0]
    for names in seen[1:]:
        assert "池外一" not in names and "池外二" in names
    assert {tuple(sorted(names - {"首词条", "池外二", "A"})) for names in seen[1:]} == {
        ("B",), ("C",), ("D",), ("E",)}


def test_transmute_search_tries_each_existing_affix_when_all_are_usable(
        monkeypatch):
    evaluator = _bare_evaluator()
    seen = []

    class Capture(SearchStrategy):
        def search(self, problem):
            seen.append(tuple(
                str(problem.equipment.get(f"affix_{i}", {}).get("name") or "")
                for i in range(2, 5)))
            return SearchOutcome(
                SearchStatus.NO_IMPROVEMENT, "checked", 0.5, 1)

    evaluator._strategy = Capture()
    _patch_affix_rules(monkeypatch, ["A", "B", "C", "D", "E"])
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 1.0,
        affix_pool=("A", "B", "C", "D", "E"), plan_maximum_rate=1.0)
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "首词条", "value": 1},
        "affix_2": {"name": "A", "value": 1},
        "affix_3": {"name": "B", "value": 1},
        "affix_4": {"name": "C", "value": 1},
    }

    evaluator._evaluate_plan(context, "ring", equipment)

    # 不转律 + 第 2、3、4 条各转成库内两个未出现的词条（D/E），转入落回原槽
    assert len(seen) == 7
    assert seen[0] == ("A", "B", "C")
    assert seen[1:] == [
        ("D", "B", "C"), ("E", "B", "C"),
        ("A", "D", "C"), ("A", "E", "C"),
        ("A", "B", "D"), ("A", "B", "E"),
    ]


def test_transmute_branch_can_rescue_candidate_without_mutating_source(
        monkeypatch):
    evaluator = _bare_evaluator()

    class Rescue(SearchStrategy):
        def search(self, problem):
            names = {
                str(affix.get("name"))
                for key, affix in problem.equipment.items()
                if key.startswith("affix_") and isinstance(affix, dict)
            }
            if "垃圾" not in names:
                return SearchOutcome(
                    SearchStatus.IMPROVES, "可挽救", 1.1, 1, ("A", "B"))
            return SearchOutcome(
                SearchStatus.NO_IMPROVEMENT, "原词条不可提升", 0.8, 1)

    evaluator._strategy = Rescue()
    _patch_affix_rules(monkeypatch, ["A", "B", "C", "D"])
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 1.0,
        affix_pool=("A", "B", "C", "D"), plan_maximum_rate=1.0)
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "首词条", "value": 1},
        "affix_2": {"name": "垃圾", "value": 1},
        "affix_3": {"name": "A", "value": 1},
    }

    result = evaluator._evaluate_plan(context, "ring", equipment)

    assert result.status is SmartTuningStatus.IMPROVES
    assert result.maximum_rate == 1.1
    assert "第 2 条「垃圾」转为「" in result.reason
    assert equipment["affix_2"]["name"] == "垃圾"


def test_playstyle_required_affix_bypasses_normal_rule_pool(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._game_config.get_playstyle = lambda _name: {
        "all_skill_requirement": "需要",
    }
    captured = {}

    class Capture(SearchStrategy):
        def search(self, problem):
            captured["equipment"] = problem.equipment
            captured["candidates"] = problem.candidates
            captured["missing"] = problem.missing
            return SearchOutcome(SearchStatus.IMPROVES, "ok", 2.0, 1)

    evaluator._strategy = Capture()
    _patch_affix_rules(monkeypatch, ["全武学增效", "A"])
    context = _PlanContext(
        "p", "方案", "鸣金·虹", object(), object(), {}, 1.0,
        rule_key="r", rule_name="规则", affix_pool=("A",),
        playstyle="无名",
        plan_maximum_rate=1.0,
    )
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "X", "value": 1},
    }

    result = evaluator._evaluate_plan(context, "ring", equipment)

    assert result.status is SmartTuningStatus.IMPROVES
    assert captured["equipment"]["affix_2"]["name"] == "全武学增效"
    assert captured["candidates"] == ["A"]
    assert captured["missing"] == 3


def test_evaluation_compares_candidate_maximum_with_plan_maximum(monkeypatch):
    """实际穿戴率只作展示；判定必须是三满候选对三满原方案。"""
    evaluator = _bare_evaluator()
    captured = {}

    class Capture(SearchStrategy):
        def search(self, problem):
            captured["baseline"] = problem.baseline
            return SearchOutcome(SearchStatus.NO_IMPROVEMENT, "checked", 0.8, 1)

    evaluator._strategy = Capture()
    evaluator._candidate_rate = lambda *_args: 0.8
    _patch_affix_rules(monkeypatch, ["A"])
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 0.5,
        affix_pool=("A",),
        plan_maximum_rate=0.9,
    )
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        **{
            f"affix_{index}": {"name": f"词条{index}", "value": 1}
            for index in range(1, 5)
        },
    }

    result = evaluator._evaluate_plan(context, "ring", equipment)

    assert result.status is SmartTuningStatus.NO_IMPROVEMENT
    assert captured["baseline"] == 0.9
    assert result.baseline_rate == 0.5  # 实际值仍保留给界面观察


def test_candidate_maximum_applies_all_three_assumptions(monkeypatch):
    evaluator = _bare_evaluator()
    calls = []

    def fake_caps(equipped, **kwargs):
        calls.append((equipped, kwargs))
        return equipped

    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.graduation.assumptions.apply_hypothetical_caps",
        fake_caps)
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 0.5,
        playstyle="双切", plan_maximum_rate=0.9,
    )

    evaluator._apply_maximum_assumptions(context, {"pendant": {"type": "佩"}})

    assert calls[0][1] == {
        "full_chengyin": True,
        "full_dingyin": True,
        "full_level": 110,
        "playstyle": "双切",
        "simulate_transmute": False,
    }


def test_candidate_maximum_keeps_current_season_native_equipment_native():
    """同等级承音只属于最优组合分支，不得污染智能调律的三满静态值。"""
    from lvjiang.apps.yysls.config import get_game_config

    evaluator = _bare_evaluator()
    evaluator._game_config = get_game_config()
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 0.5,
        playstyle="", plan_maximum_rate=0.9,
    )
    original = {
        "pendant": {
            # 当前赛季等级的原生装备：换赛季后钉死 110 会让用例名和事实脱节
            "type": "佩", "level": get_game_config().current_equip_level(),
            "is_chengyin": False,
            "affix_1": {"name": "最小外功攻击", "value": 87.1},
        },
    }

    projected = evaluator._apply_maximum_assumptions(context, original)

    assert projected["pendant"]["is_chengyin"] is False
    assert projected["pendant"]["affix_1"]["value"] == 87.1
    assert original["pendant"]["is_chengyin"] is False


def _ring_problem(rate, *, candidates=("A", "B", "C"), missing=2, baseline=15.0,
                  operator="gt", budget=None):
    def complete(equipment, names):
        result = dict(equipment)
        empty = [i for i in range(1, 6) if f"affix_{i}" not in result]
        if len(names) > len(empty):
            return None
        for index, name in zip(empty, names, strict=False):
            result[f"affix_{index}"] = {"name": name, "value": 1.0}
        return result

    return SearchProblem(
        equipment={"type": "环", "affix_1": {"name": "X", "value": 1},
                   "affix_2": {"name": "Y", "value": 1}, "affix_3": {"name": "Z", "value": 1}},
        candidates=list(candidates), missing=missing, baseline=baseline,
        operator=operator, complete=complete, rate=rate,
        budget=budget or SearchBudget(5.0),
    )


def _names(equipment) -> set[str]:
    return {v["name"] for k, v in equipment.items()
            if k.startswith("affix_") and isinstance(v, dict)} & {"A", "B", "C"}


def test_strategy_greedy_reports_no_improvement_without_exhaustive_search():
    calls = []

    def rate(equipment):
        calls.append(_names(equipment))
        return 10.0

    outcome = GreedySwapStrategy().search(_ring_problem(rate))

    assert outcome.status is SearchStatus.NO_IMPROVEMENT
    assert outcome.evaluated == 3 + 2  # 首轮 3 个候选，次轮 2 个候选
    assert outcome.maximum_rate == 10.0


def _threshold_rate(equipment) -> float:
    """复现真实反例（用户C/无名PVE/佩）：势 单条边际最大（半条会意 + 攻击），
    但 40% 以上的会意率是浪费的，且 劲/最大外功 这类攻击加成会稀释 势 的攻击
    份额——补满四条后，"整条会意率刚好顶到 40%" 反而比 势 更高。"""
    names = [v["name"] for k, v in sorted(equipment.items())
             if k.startswith("affix_") and isinstance(v, dict)]
    attack = 1.0 + 0.012 * names.count("最大外功攻击") + 0.03 * names.count("劲") \
        + 0.009 * names.count("势")
    bonus = 1.0 + 0.0144 * names.count("全武学增效")
    intent = 0.3796 + 0.0112 * names.count("势") + 0.0204 * names.count("会意率")
    return attack * bonus * (1 + min(intent, 0.40) * 1.5) * 0.4


def _exact_best(problem):
    from itertools import combinations
    best = (float("-inf"), ())
    for names in combinations(problem.candidates, problem.missing):
        completed = problem.complete(problem.equipment, names)
        if completed is not None:
            best = max(best, (problem.rate(completed), names))
    return best


def test_greedy_swap_recovers_rate_threshold_case():
    problem = _ring_problem(
        _threshold_rate, missing=4, baseline=10**9,
        candidates=("势", "会意率", "最大外功攻击", "劲", "全武学增效", "体"))
    problem.equipment = {"type": "佩"}

    plain, _calls = GreedySwapStrategy._greedy(problem)
    assert plain is not None and "势" in plain[0] and "会意率" not in plain[0]

    outcome = GreedySwapStrategy().search(problem)

    assert outcome.status is SearchStatus.NO_IMPROVEMENT       # 基准不可达
    assert "会意率" in outcome.winning_affixes and "势" not in outcome.winning_affixes
    assert outcome.maximum_rate > plain[1]
    assert "三率临界交换" in outcome.reason
    # 与穷举最优一致
    exact_rate, _names = _exact_best(problem)
    assert abs(exact_rate - outcome.maximum_rate) < 1e-12


@pytest.mark.parametrize(("hybrid", "direct"), [("势", "会意率"), ("会心率", "敏")])
def test_swap_is_bidirectional_and_bounded(hybrid, direct):
    """反向（整条率 → 五维）同样验证；一对最多产生一个交换变体。"""
    variants = GreedySwapStrategy._swap_variants(
        (hybrid, "攻击"), [hybrid, direct, "攻击"])
    assert variants == [(direct, "攻击")]
    assert GreedySwapStrategy._swap_variants(
        (hybrid, direct), [hybrid, direct, "攻击"]) == []


def test_strategy_without_swap_pairs_stays_pure_greedy():
    problem = _ring_problem(lambda equipment: 10.0 + len(_names(equipment)))
    outcome = GreedySwapStrategy().search(problem)
    assert outcome.status is SearchStatus.NO_IMPROVEMENT
    assert "三率临界交换" not in outcome.reason


def test_strategy_gte_lets_equal_rate_pass():
    outcome = GreedySwapStrategy().search(
        _ring_problem(lambda _e: 15.0, operator="gte"))
    assert outcome.status is SearchStatus.IMPROVES


def test_strategy_full_equipment_only_rates_current_state():
    problem = _ring_problem(lambda _e: 20.0, missing=0)
    outcome = GreedySwapStrategy().search(problem)
    assert outcome.status is SearchStatus.IMPROVES and outcome.evaluated == 1


def test_strategy_stop_returns_unknown_not_failure():
    outcome = GreedySwapStrategy().search(_ring_problem(
        lambda _e: 5.0, budget=SearchBudget(5.0, stop_check=lambda: True)))
    assert outcome.status is SearchStatus.UNKNOWN
    assert "中断" in outcome.reason


def test_budget_excludes_time_blocked_in_stop_check(monkeypatch):
    """暂停时 stop_check 会阻塞，阻塞时间不能算成超时。"""
    clock = [0.0]
    monkeypatch.setattr("lvjiang.apps.yysls.core.graduation.smart_search.time.perf_counter",
                        lambda: clock[0])

    def paused_stop_check():
        clock[0] += 30.0          # 模拟在暂停检查点里阻塞了 30 秒
        return False

    budget = SearchBudget(5.0, paused_stop_check)
    assert not budget.exhausted()
    clock[0] += 1.0               # 真正计算 1 秒
    assert not budget.exhausted()
    clock[0] += 6.0               # 累计 7 秒真实计算 → 超预算
    assert budget.exhausted() and budget.expired


def test_custom_strategy_can_replace_default_without_touching_evaluator(monkeypatch):
    class Always(SearchStrategy):
        name = "always_improves"

        def search(self, problem):
            from lvjiang.apps.yysls.core.graduation.smart_search import SearchOutcome
            return SearchOutcome(SearchStatus.IMPROVES, "测试策略", 99.0, 1, ("A",))

    register_strategy(Always())
    evaluator = _bare_evaluator()
    evaluator._strategy = get_strategy("always_improves")
    _patch_affix_rules(monkeypatch, ["A"])
    context = _PlanContext(
        "p", "方案", "鸣金·虹", object(), object(), {}, 15.0,
        affix_pool=("A",), plan_maximum_rate=15.0)

    result = evaluator._evaluate_plan(
        context, "ring",
        {"type": "环", "level": 110, "quality": "gold", "affix_1": {"name": "X", "value": 1}})

    assert result.status is SmartTuningStatus.IMPROVES
    assert result.maximum_rate == 99.0 and "测试策略：A" == result.reason
    assert get_strategy("no_such_strategy").name == "greedy_swap"


def test_weapon_candidates_match_plan_slot_by_type(monkeypatch):
    """全部武器都在 main_weapon 槽遍历：枪对威威方案应替换副武器，横刀对该方案
    不适用；不适用的方案不参与聚合。"""
    evaluator = _bare_evaluator()
    _patch_affix_rules(monkeypatch, ["A"])
    seen_slots = []
    evaluator._candidate_rate = lambda _c, slot, _e: seen_slots.append(slot) or 1.0
    weiwei = _PlanContext(
        "w", "威威", "裂石·威", object(), object(),
        {"main_weapon": {"type": "陌刀"}, "sub_weapon": {"type": "枪"}}, 50.0,
        affix_pool=("A",), plan_maximum_rate=50.0)
    evaluator._contexts = (weiwei,)
    spear = {"type": "枪", "level": 110, "quality": "gold",
             "affix_1": {"name": "X", "value": 1}, "affix_2": {"name": "Y", "value": 1},
             "affix_3": {"name": "Z", "value": 1}, "affix_4": {"name": "W", "value": 1}}

    outcome = evaluator.evaluate("main_weapon", spear)

    assert set(seen_slots) == {"sub_weapon"}
    assert outcome.status is SmartTuningStatus.NO_IMPROVEMENT

    sword = dict(spear, type="横刀")
    outcome = evaluator.evaluate("main_weapon", sword)
    assert outcome.plans[0].status is SmartTuningStatus.NOT_APPLICABLE
    assert outcome.status is SmartTuningStatus.UNKNOWN


def test_any_unknown_plan_prevents_destructive_failure(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._contexts = (
        SimpleNamespace(plan_id="a"), SimpleNamespace(plan_id="b"),
    )

    def result(context, _slot, _equipment):
        if context.plan_id == "a":
            return SmartPlanResult(
                "a", "A", SmartTuningStatus.NO_IMPROVEMENT, reason="no")
        return SmartPlanResult(
            "b", "B", SmartTuningStatus.UNKNOWN, reason="broken")

    monkeypatch.setattr(evaluator, "_evaluate_plan", result)
    outcome = evaluator.evaluate("ring", {"type": "环"})
    assert outcome.status is SmartTuningStatus.UNKNOWN


@pytest.fixture
def base_tune_config():
    return {
        "quality_thresholds": {
            part: ["gold"]
            for part in ("武器", "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲")
        },
        "switches": {},
    }


def test_transmute_branch_only_transmutes_into_pool_union(monkeypatch):
    """规则池里的神力词条可以由调律补出，但不能作为转律转入目标。"""
    evaluator = _bare_evaluator()
    evaluator._game_config.get_all_transmute_pools = lambda: {"流派": ["B", "C"]}
    seen = []

    class Capture(SearchStrategy):
        def search(self, problem):
            seen.append(str(problem.equipment.get("affix_2", {}).get("name") or ""))
            return SearchOutcome(
                SearchStatus.NO_IMPROVEMENT, "checked", 0.5, 1)

    evaluator._strategy = Capture()
    _patch_affix_rules(monkeypatch, ["A", "B", "C", "神"])
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 1.0,
        affix_pool=("A", "B", "C", "神"), plan_maximum_rate=1.0)
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "首词条", "value": 1},
        "affix_2": {"name": "垃圾", "value": 1},
        "affix_3": {"name": "A", "value": 1},
    }

    evaluator._evaluate_plan(context, "ring", equipment)

    assert seen[0] == "垃圾"
    assert sorted(seen[1:]) == ["B", "C"]


def test_no_transmute_pool_means_no_transmute_branch(monkeypatch):
    evaluator = _bare_evaluator()
    evaluator._game_config.get_all_transmute_pools = lambda: {}
    seen = []

    class Capture(SearchStrategy):
        def search(self, problem):
            seen.append(problem.equipment)
            return SearchOutcome(
                SearchStatus.NO_IMPROVEMENT, "checked", 0.5, 1)

    evaluator._strategy = Capture()
    _patch_affix_rules(monkeypatch, ["A", "B", "C"])
    context = _PlanContext(
        "p", "方案", "流派", object(), object(), {}, 1.0,
        affix_pool=("A", "B", "C"), plan_maximum_rate=1.0)
    equipment = {
        "type": "环", "level": 110, "quality": "gold",
        "affix_1": {"name": "首词条", "value": 1},
        "affix_2": {"name": "垃圾", "value": 1},
    }

    evaluator._evaluate_plan(context, "ring", equipment)

    assert len(seen) == 1


def test_evaluator_uses_injected_state_and_rules_without_touching_storage(monkeypatch):
    """备战方案快照与规则表由调用方注入时，评估器不读仓储、不问规则管理器。"""
    from lvjiang.apps.yysls.core.graduation import smart_tuning as module
    from lvjiang.apps.yysls.core.loadout import LoadoutState

    def _boom(*_args, **_kwargs):
        raise AssertionError("不应读取备战方案仓储")

    monkeypatch.setattr(module, "LoadoutRepository", _boom)
    monkeypatch.setattr(module, "get_tuning_rule_manager", _boom)
    rule = SimpleNamespace(
        name="会意", playstyles={"纯唐": SimpleNamespace()},
        affix_pool=["最大外功攻击"], patterns={},
    )
    game_config = _bare_evaluator()._game_config
    game_config.get_schools = lambda: {}
    evaluator = SmartTuningEvaluator(
        _config(), username="tester", incoming_rule_configs={"huiyi": {}},
        state=LoadoutState.empty(), rules={"huiyi": rule}, game_config=game_config,
    )
    # 注入的空方案集：目标玩法找不到方案 → 明确禁用原因，而不是读盘异常
    assert not evaluator.active
    assert evaluator.disabled_reason == "没有可靠的备战方案可用于毕业率判定"
    assert [info["status"] for info in evaluator.plan_infos] == ["missing"]


def test_candidate_problem_reasons_keep_priority_order():
    """校验顺序即原因优先级：基础字段 → 词条数据 → 组合合法性 → 词条已满。"""
    problem = SmartTuningEvaluator._candidate_problem
    assert problem({"type": "剑"}) == "当前装备缺少类型、等级或品阶"
    base = {"type": "剑", "level": 110, "quality": "gold"}
    assert problem({**base, "affix_2": {"name": "劲", "value": 0}}) == (
        "当前装备第 2 条词条数据不完整")
    assert problem({**base, "affix_1": {"name": "最大外功攻击", "value": 1}}) == ""


def _plan(plan_id: str, name: str, combat_type: str):
    from lvjiang.apps.yysls.core.loadout.models import LoadoutPlan

    return LoadoutPlan(
        id=plan_id, name=name, main_martial_art="无名剑法",
        sub_martial_art="无名枪法", playstyle="无名",
        combat_type=combat_type,
    )


def test_pvp_plans_are_filtered_out_with_a_visible_reason(monkeypatch):
    """同玩法的 PVP 方案不能一起加载，但也不能静默丢掉。

    没有 PVP 调律方案，PVP 方案基线低，一起加载会让大量够不到 PVE 标准的装备
    被判成有提升——这就是要过滤的原因。而只有 PVP 方案的用户如果只看到
    「没有匹配的备战方案」，根本不知道发生了什么，所以必须留下诊断记录。

    这里让 PVE 方案在后续的装备完整性检查上失败：PVP 记成 skipped、PVE 记成
    incomplete，正好同时证明「PVP 被挡在门外」和「PVE 照常走完整条管线」，
    不必为此搭出整套评分上下文。
    """
    from lvjiang.apps.yysls.core.graduation import smart_tuning as module
    from lvjiang.apps.yysls.core.loadout.models import (
        COMBAT_TYPE_PVE,
        COMBAT_TYPE_PVP,
    )

    evaluator = _bare_evaluator()
    evaluator._plan_infos = []
    evaluator._injected_rules = {}
    evaluator._game_config.get_schools = lambda: {}
    evaluator._game_config.get_playstyles_for_arts = lambda _arts: ["无名"]
    target = _SelectedTarget("key", "规则", "无名", ())
    monkeypatch.setattr(evaluator, "_selected_targets", lambda _i: (target,))
    monkeypatch.setattr(
        module.PlanScoringContext, "from_plan",
        classmethod(lambda _cls, plan, **_kw: SimpleNamespace(school="流派")))
    state = SimpleNamespace(
        plans={
            "pve": _plan("pve", "无名PVE", COMBAT_TYPE_PVE),
            "pvp": _plan("pvp", "无名PVP", COMBAT_TYPE_PVP),
        },
        resolved_equipment=lambda _plan_id: {},
    )

    contexts = evaluator._load_contexts("alice", {}, None, state=state)

    assert contexts == ()
    by_status = {info["status"]: info for info in evaluator._plan_infos}
    assert "PVP" in by_status["skipped"]["reason"]
    assert by_status["skipped"]["plan_name"] == "无名PVP"
    # PVE 方案没有被过滤掉，是走到装备完整性检查才停的
    assert by_status["incomplete"]["plan_name"] == "无名PVE"
