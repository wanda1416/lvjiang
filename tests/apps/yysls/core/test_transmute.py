"""模拟转律：资格、候选、目标合法性与三满投影。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.models import LevelConfig
from lvjiang.apps.yysls.core.combat.combat_attrs import apply_hypothetical_caps
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.loadout.transmute import (
    REASON_FIRST_TRANSFERRED,
    REASON_ILLEGAL,
    REASON_MULTIPLE_TRANSFERRED,
    REASON_NO_LEVEL_CONFIG,
    REASON_NO_RETRANSFER,
    REASON_NO_RETRANSFER_AFTER_CHENGYIN,
    REASON_UNKNOWN_ORIGINAL_LEVEL,
    TARGET_NAME_KEY,
    TARGET_VALUE_KEY,
    judge_transmute_eligibility,
    project_transmute_targets,
    strip_transmute_targets,
    transmute_candidates,
    transmute_pool_union,
    transmute_target_value,
    validate_saved_target,
)


class _ConfigProxy:
    """代理真实配置，只覆盖等级能力，用来模拟未来 115/120 等级。"""

    def __init__(self, levels: dict[int, LevelConfig]) -> None:
        self._real = get_game_config()
        self._levels = levels

    def level_config_for(self, level: int):
        if level in self._levels:
            return self._levels[level]
        return self._real.level_config_for(level)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _level(level: int, *, retransfer: bool, after_chengyin: bool) -> LevelConfig:
    return LevelConfig(
        level=level, allow_chengyin=True, allow_retransfer=retransfer,
        allow_retransfer_after_chengyin=after_chengyin,
    )


def _sword(**overrides) -> dict:
    equip = {
        "type": "剑", "name": "踏雪含光", "level": 110, "quality": "gold",
        "is_chengyin": False, "original_level": 0,
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "会心率", "value": 5.0, "unit": "%"},
        "affix_3": {"name": "劲", "value": 60},
        "affix_4": {"name": "最大无相攻击", "value": 70},
    }
    equip.update(overrides)
    return equip


def test_unchengyin_uses_current_level_capability():
    gc = get_game_config()
    assert judge_transmute_eligibility(_sword(level=110), gc).eligible
    assert judge_transmute_eligibility(_sword(level=105), gc).eligible
    judged = judge_transmute_eligibility(_sword(level=100), gc)
    assert not judged.eligible and judged.reason == REASON_NO_RETRANSFER


def test_chengyin_uses_original_level_and_both_flags():
    gc = get_game_config()
    judged = judge_transmute_eligibility(
        _sword(is_chengyin=True, original_level=105), gc)
    assert judged.reason == REASON_NO_RETRANSFER_AFTER_CHENGYIN
    assert judge_transmute_eligibility(
        _sword(is_chengyin=True, original_level=110), gc).eligible
    unknown = judge_transmute_eligibility(
        _sword(is_chengyin=True, original_level=0, name="不认识的名字"), gc)
    assert unknown.reason == REASON_UNKNOWN_ORIGINAL_LEVEL


@pytest.mark.parametrize(
    ("level", "retransfer", "after_chengyin", "chengyin_ok"),
    [(115, True, True, True), (120, True, False, False), (115, False, True, False)],
)
def test_future_levels_follow_capability_config(
    level, retransfer, after_chengyin, chengyin_ok,
):
    gc = _ConfigProxy({level: _level(
        level, retransfer=retransfer, after_chengyin=after_chengyin)})
    plain = judge_transmute_eligibility(_sword(level=level), gc)
    assert plain.eligible is retransfer
    chengyin = judge_transmute_eligibility(
        _sword(is_chengyin=True, original_level=level, level=level), gc)
    assert chengyin.eligible is chengyin_ok


def test_eligibility_is_judged_on_original_snapshot_not_projection():
    """105 未承音可参与；三满把副本升到 110 承音也不能反过来禁掉它。"""
    gc = get_game_config()
    original = {"main_weapon": _sword(level=105)}
    projected = apply_hypothetical_caps(
        original, full_level=110, full_chengyin=True)
    assert projected["main_weapon"]["is_chengyin"] is True
    assert judge_transmute_eligibility(original["main_weapon"], gc).eligible


def test_full_chengyin_never_reduces_an_existing_affix_value():
    gc = get_game_config()
    equip = _sword(is_chengyin=True)
    cap = gc.get_affix_caps(110, "最大外功攻击")["chengyin"]
    equip["affix_1"]["value"] = cap + 1

    projected = apply_hypothetical_caps(
        {"main_weapon": equip}, full_chengyin=True)

    assert projected["main_weapon"]["affix_1"]["value"] == cap + 1


def test_transferred_slot_is_the_only_option_and_bad_marks_are_untrusted():
    gc = get_game_config()
    once = _sword()
    once["affix_3"]["is_transferred"] = True
    judged = judge_transmute_eligibility(once, gc)
    assert judged.slots == (3,)

    twice = copy.deepcopy(once)
    twice["affix_4"]["is_transferred"] = True
    judged = judge_transmute_eligibility(twice, gc)
    assert judged.reason == REASON_MULTIPLE_TRANSFERRED and not judged.trusted

    first = _sword()
    first["affix_1"]["is_transferred"] = True
    judged = judge_transmute_eligibility(first, gc)
    assert judged.reason == REASON_FIRST_TRANSFERRED and not judged.trusted

    assert judge_transmute_eligibility(_sword(), gc).slots == (2, 3, 4)


def test_original_mark_pins_the_slot_like_a_transfer():
    """[原]：转律过、又切回原词条。槽位照样被锁死，未来只能转这一条。

    is_original 只影响"这条是不是转律产出"（神力校验），转律资格和
    可转槽位一律只看 is_transferred。
    """
    gc = get_game_config()
    reverted = _sword()
    reverted["affix_3"]["is_transferred"] = True
    reverted["affix_3"]["is_original"] = True

    judged = judge_transmute_eligibility(reverted, gc)
    assert judged.eligible and judged.slots == (3,)
    assert set(transmute_candidates(reverted, gc)) == {3}


def test_candidates_come_from_pool_union_minus_present_and_part_rules():
    gc = get_game_config()
    union = transmute_pool_union(gc)
    assert "会意率" in union and "会心率" in union
    candidates = transmute_candidates(_sword(), gc)
    assert set(candidates) == {2, 3, 4}
    for names in candidates.values():
        assert set(names) <= set(union)
        # 第 2～5 条已有的不再作为目标；首词条同名允许（装备自带词条）
        assert "会心率" not in names and "劲" not in names
        assert "最大外功攻击" in names
        # 武器上不会出现只允许防具的属攻
        assert "最大鸣金攻击" not in names
        assert all(
            gc.get_affix_category(name) not in ("增效类", "武器类")
            for name in names)


def test_candidates_respect_pool_union_only():
    """任何流派转律库都没有的词条永远不是目标（小裂石类）。"""
    gc = get_game_config()
    union = set(transmute_pool_union(gc))
    assert "最小裂石攻击" not in union
    for names in transmute_candidates(_sword(type="冠胄"), gc).values():
        assert "最小裂石攻击" not in names


def test_empty_pool_union_yields_no_candidates():
    class _NoPool(_ConfigProxy):
        def get_all_transmute_pools(self):
            return {"鸣金·虹": []}

    assert transmute_candidates(_sword(), _NoPool({})) == {}


def test_target_value_follows_chengyin_state():
    gc = get_game_config()
    caps = gc.get_affix_caps(110, "会意率")
    assert transmute_target_value("会意率", 110, False, gc) == caps["cap"]
    assert transmute_target_value("会意率", 110, True, gc) == caps["chengyin"]


def test_saved_target_validation_and_projection():
    gc = get_game_config()
    equip = _sword(level=105)
    equip["affix_4"][TARGET_NAME_KEY] = "会意率"
    equip["affix_4"][TARGET_VALUE_KEY] = transmute_target_value(
        "会意率", 105, False, gc)
    assert validate_saved_target(equip, gc) is None

    # 不投影：目标按实际等级普通上限
    plain = project_transmute_targets({"s": equip}, {"s": copy.deepcopy(equip)}, gc)
    assert plain["s"]["affix_4"]["name"] == "会意率"
    assert plain["s"]["affix_4"]["is_transferred"] is True
    assert plain["s"]["affix_4"]["value"] == gc.get_affix_caps(105, "会意率")["cap"]
    assert TARGET_NAME_KEY not in plain["s"]["affix_4"]

    # 满等级 + 满承音：副本已是 110 承音，目标取 110 承音上限
    projected = apply_hypothetical_caps(
        {"s": equip}, full_level=110, full_chengyin=True,
        simulate_transmute=True)
    assert projected["s"]["affix_4"]["name"] == "会意率"
    assert projected["s"]["affix_4"]["value"] == gc.get_affix_caps(
        110, "会意率")["chengyin"]
    # 原始装备未被改写
    assert equip["affix_4"]["name"] == "最大无相攻击"


def test_invalid_saved_target_is_skipped_not_applied():
    gc = get_game_config()
    equip = _sword()
    equip["affix_2"][TARGET_NAME_KEY] = "劲"  # 第 3 条已有劲 → 重复
    equip["affix_2"][TARGET_VALUE_KEY] = 10.0
    assert validate_saved_target(equip, gc) == REASON_ILLEGAL
    projected = apply_hypothetical_caps({"s": equip}, simulate_transmute=True)
    assert projected["s"]["affix_2"]["name"] == "会心率"


def test_simulate_transmute_without_targets_returns_equivalent_data():
    equipped = {"s": _sword()}
    projected = apply_hypothetical_caps(equipped, simulate_transmute=True)
    assert projected["s"]["affix_2"] == equipped["s"]["affix_2"]


def test_affix_model_roundtrips_target_fields():
    affix = Affix.from_dict({
        "name": "劲", "value": 60,
        TARGET_NAME_KEY: "会意率", TARGET_VALUE_KEY: 7.0,
    })
    assert affix.target_transmute_name == "会意率"
    assert affix.to_dict()[TARGET_VALUE_KEY] == 7.0
    assert TARGET_NAME_KEY not in Affix("劲", 60).to_dict()
    data = EquipmentData(type="剑", affixes=[affix]).to_dict(include_fp=False)
    assert data["affix_1"][TARGET_NAME_KEY] == "会意率"
    assert strip_transmute_targets(data)["affix_1"].get(TARGET_NAME_KEY) is None


def test_target_fields_do_not_change_fingerprint():
    from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint

    equip = _sword()
    before = make_fingerprint(equip)
    equip["affix_2"][TARGET_NAME_KEY] = "会意率"
    equip["affix_2"][TARGET_VALUE_KEY] = 7.0
    assert make_fingerprint(equip) == before


def test_unknown_config_object_is_tolerated():
    gc = SimpleNamespace(
        level_config_for=lambda level: None,
        infer_original_equipment_level=lambda name: 0,
    )
    assert not judge_transmute_eligibility(_sword(), gc).eligible


def test_divine_affixes_in_pool_are_rejected_by_validator():
    """转律不产神力：即使把神力词条塞进 pool，整件校验（transferred_divine）
    也会挡住，不需要单独的分类过滤。"""
    from lvjiang.apps.yysls.core.loadout.transmute import transmute_targets

    gc = get_game_config()
    targets = transmute_targets(
        _sword(), 2, ["剑武学增伤", "全武学增效", "会意率"], gc)
    assert targets == ["会意率"]


def test_untransferred_without_level_config_is_not_eligible_even_for_tuning():
    gc = get_game_config()
    judged = judge_transmute_eligibility(
        _sword(level=99), gc, require_retransfer=False)
    assert not judged.eligible and judged.reason == REASON_NO_LEVEL_CONFIG
    assert judge_transmute_eligibility(
        _sword(level=100), gc, require_retransfer=False).eligible


def test_retransfer_capability_infers_original_level_from_name():
    """original_level 缺失（旧数据/模拟装备）时按名称等阶识别，与解析器同源。"""
    from lvjiang.apps.yysls.core.loadout.transmute import retransfer_capability

    gc = get_game_config()
    named = _sword(is_chengyin=True, original_level=0, name="吴钩霜甲")
    assert gc.infer_original_equipment_level("吴钩霜甲") == 110
    assert retransfer_capability(named, gc) == (True, "")
    unknown = _sword(is_chengyin=True, original_level=0, name="不认识的名字")
    assert retransfer_capability(unknown, gc)[1] == REASON_UNKNOWN_ORIGINAL_LEVEL
