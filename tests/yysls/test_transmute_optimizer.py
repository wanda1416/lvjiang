"""八件装备联合转律建议：逐件精确评估的局部贪心。"""
from __future__ import annotations

import copy
from itertools import product
from types import SimpleNamespace

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    apply_hypothetical_caps,
)
from lvjiang.apps.yysls.core.graduation.affix_impact import (
    _effective_equipped,
    _graduation_rate,
)
from lvjiang.apps.yysls.core.graduation.transmute_optimizer import (
    TransmuteSearchRequest,
    optimize_transmutes,
)
from lvjiang.apps.yysls.core.loadout.transmute import (
    TARGET_NAME_KEY,
    TARGET_VALUE_KEY,
    transmute_candidates,
    transmute_target_value,
    with_transmuted_affix,
)


class _RateCalculator:
    """会意率/会心率封顶 + 外攻的非线性测试计算器，能体现跨件耦合。"""

    def __init__(self) -> None:
        self.calls = 0

    def calculate(self, attrs: CombatAttributes):
        self.calls += 1
        # 三率在属性里是小数（7% → 0.07），40% 封顶即 0.4
        intent = min(attrs.intent_rate, 0.4)
        crit = min(attrs.crit_rate, 0.4)
        rate = (
            attrs.max_outer / 4000
            + attrs.min_outer / 8000
            + intent
            + crit / 2
        )
        return SimpleNamespace(graduation_rate=rate)


def _ring(level: int = 110, **affixes) -> dict:
    equip = {
        "type": "环", "name": "环", "level": level, "quality": "gold",
        "is_chengyin": False, "original_level": 0,
        "affix_1": {"name": "最大外功攻击", "value": 100},
    }
    for index, (name, value) in enumerate(affixes.items(), start=2):
        equip[f"affix_{index}"] = {"name": name, "value": value}
    return equip


def _pendant(level: int = 110, **affixes) -> dict:
    equip = _ring(level, **affixes)
    equip.update({"type": "佩", "name": "佩"})
    return equip


def _request(equipped: dict, **overrides) -> TransmuteSearchRequest:
    gc = get_game_config()
    kwargs = dict(
        equipped=equipped,
        calculator=_RateCalculator(),
        base_attrs=CombatAttributes(),
        school="鸣金·虹",
        game_config=gc,
        school_pool=tuple(gc.get_transmute_pool("鸣金·虹")),
    )
    kwargs.update(overrides)
    return TransmuteSearchRequest(**kwargs)


def test_recommends_at_most_one_move_per_equipment_and_improves():
    equipped = {
        "ring": _ring(最小外功攻击=30, 敏=50, 劲=50),
        "pendant": _pendant(最小外功攻击=30, 敏=50),
    }
    result = optimize_transmutes(_request(equipped))
    assert result.trusted and result.exhausted
    assert result.final_rate > result.baseline_rate
    slots = [move.slot_key for move in result.moves]
    assert len(slots) == len(set(slots))
    for move in result.moves:
        assert move.affix_index in (2, 3, 4)
        assert move.to_name != move.from_name
        assert move.marginal_gain > 0 and move.swap_gain > 0
        # 目标值按实际装备状态：未承音 = 普通上限
        assert move.to_value == transmute_target_value(
            move.to_name, 110, False, get_game_config())


def test_result_matches_exhaustive_search_on_two_items():
    gc = get_game_config()
    equipped = {
        "ring": _ring(最小外功攻击=30, 敏=50),
        "pendant": _pendant(最小外功攻击=30, 劲=50),
    }
    result = optimize_transmutes(_request(equipped))

    options: dict[str, list[tuple[int, str] | None]] = {}
    for slot, equip in equipped.items():
        options[slot] = [None] + [
            (index, name)
            for index, names in transmute_candidates(equip, gc).items()
            for name in names
        ]
    calc = _RateCalculator()
    best = -1.0
    for choice in product(options["ring"], options["pendant"]):
        state = copy.deepcopy(equipped)
        for slot, move in zip(("ring", "pendant"), choice, strict=True):
            if move is None:
                continue
            index, name = move
            state[slot] = with_transmuted_affix(
                state[slot], index, name,
                transmute_target_value(name, 110, False, gc) or 0.0, gc)
        rate = _graduation_rate(
            calc, CombatAttributes(), _effective_equipped(state, gc),
            "鸣金·虹", gc)
        best = max(best, rate)
    assert abs(result.final_rate - best) < 1e-9


def test_intent_cap_makes_second_intent_transmute_worthless():
    """第一件转会意率封到 40% 后，第二件再转会意率没有收益，应换别的目标。"""
    gc = get_game_config()
    equipped = {
        "ring": _ring(最小外功攻击=30, 会意率=36.0),
        "pendant": _pendant(最小外功攻击=30, 敏=50),
    }
    cap = transmute_target_value("会意率", 110, False, gc) or 0.0
    assert 36.0 + cap > 40.0
    result = optimize_transmutes(_request(equipped))
    intent_moves = [m for m in result.moves if m.to_name == "会意率"]
    assert len(intent_moves) <= 1


def test_threshold_suppresses_negligible_gains():
    equipped = {"ring": _ring(最小外功攻击=30, 敏=50)}
    tiny = optimize_transmutes(_request(equipped, precision=0.5))
    assert tiny.moves == ()
    assert tiny.final_rate == tiny.baseline_rate
    normal = optimize_transmutes(_request(equipped))
    assert normal.moves


def test_value_only_gain_is_not_reported_as_transmute_advice():
    """低品质的好词条：拉满数值就够了，不应被推荐换成别的满值词条。"""

    class _IntentOnly:
        def calculate(self, attrs: CombatAttributes):
            return SimpleNamespace(graduation_rate=min(attrs.intent_rate, 0.4))

    equipped = {"ring": _ring(会意率=1.0, 敏=50)}
    result = optimize_transmutes(_request(equipped, calculator=_IntentOnly()))
    # 敏 → 会心率/势 等对该计算器无收益；会意率槽只有“数值收益”，不推荐转律
    assert all(move.from_name != "会意率" for move in result.moves)


def test_saved_targets_rate_and_ineligible_slots_are_reported():
    gc = get_game_config()
    ring = _ring(最小外功攻击=30, 敏=50)
    ring["affix_2"][TARGET_NAME_KEY] = "会意率"
    ring["affix_2"][TARGET_VALUE_KEY] = transmute_target_value("会意率", 110, False, gc)
    pendant = _pendant(level=100, 最小外功攻击=30)
    result = optimize_transmutes(_request({"ring": ring, "pendant": pendant}))
    assert result.saved_rate is not None and result.saved_rate > result.baseline_rate
    status = {item.slot_key: item for item in result.slots}
    assert not status["pendant"].eligible
    assert status["pendant"].reason == "no_retransfer"
    assert status["pendant"].move is None
    assert result.missing_slots == (
        "main_weapon", "sub_weapon", "head", "chest", "leg", "wrist")


def test_untrusted_equipment_blocks_application():
    ring = _ring(最小外功攻击=30, 敏=50)
    ring["affix_1"]["is_transferred"] = True
    result = optimize_transmutes(_request({"ring": ring}))
    assert not result.trusted and not result.applicable


def test_budget_stop_returns_partial_but_valid_result():
    equipped = {
        "ring": _ring(最小外功攻击=30, 敏=50, 劲=50),
        "pendant": _pendant(最小外功攻击=30, 敏=50),
    }
    calls = {"n": 0}

    def stop_after_first_evaluations() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    result = optimize_transmutes(_request(
        equipped, stop_check=stop_after_first_evaluations))
    assert not result.exhausted
    assert result.final_rate >= result.baseline_rate
    slots = [move.slot_key for move in result.moves]
    assert len(slots) == len(set(slots))


def test_projection_matches_dialog_prediction_after_apply():
    """应用后勾选“模拟转律”，页面按同样三满得到的毕业率应等于推荐值。"""
    gc = get_game_config()
    equipped = {
        "ring": _ring(level=105, 最小外功攻击=30, 敏=50),
        "pendant": _pendant(最小外功攻击=30, 劲=50),
    }
    flags = {"full_level": 110, "full_chengyin": True}
    result = optimize_transmutes(_request(equipped, **flags))
    assert result.moves
    applied = copy.deepcopy(equipped)
    for move in result.moves:
        affix = applied[move.slot_key][f"affix_{move.affix_index}"]
        affix[TARGET_NAME_KEY] = move.to_name
        affix[TARGET_VALUE_KEY] = move.to_value
    projected = apply_hypothetical_caps(
        applied, simulate_transmute=True, **flags)
    rate = _graduation_rate(
        _RateCalculator(), CombatAttributes(),
        _effective_equipped(projected, gc), "鸣金·虹", gc)
    assert abs(rate - result.final_rate) < 1e-9
