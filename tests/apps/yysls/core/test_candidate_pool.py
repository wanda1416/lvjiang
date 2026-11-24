"""最优组合候选池：武器按方案武学分配、模拟装备开关、仓储记录一条一候选。"""
from lvjiang.apps.yysls.core.graduation.candidate_pool import (
    CandidateFilter,
    collect_candidates,
)


def _eq(name, type_, level=110, **extra):
    return {"name": name, "type": type_, "level": level, "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100}, **extra}


def test_weapons_route_by_plan_weapon_types_and_armor_by_group():
    sword, spear, sword2 = _eq("剑A", "剑"), _eq("枪B", "枪"), _eq("剑C", "剑")
    head = _eq("冠", "冠胄")
    pooled = collect_candidates(
        {"weapon": {"1": sword, "2": spear, "3": sword2}, "head": {"4": head}},
        None,
        main_weapon_type="剑", sub_weapon_type="枪", filters=CandidateFilter(),
    )
    assert [e["name"] for e in pooled["main_weapon"]] == ["剑A", "剑C"]
    assert [e["name"] for e in pooled["sub_weapon"]] == ["枪B"]
    assert [e["name"] for e in pooled["head"]] == ["冠"]
    assert pooled["ring"] == []


def test_mock_switch_excludes_worn_mock_and_keeps_chengyin_twins():
    """排除模拟时已穿戴的模拟件不在背包视图里，自然不进候选；
    承音 / 未承音的孪生件是仓储里两条记录，两件都要进候选池。"""
    plain = _eq("剑A", "剑", _fp="a1", is_chengyin=False)
    chengyin = _eq("剑A", "剑", _fp="a2", is_chengyin=True, original_level=100)
    pooled = collect_candidates(
        {"weapon": {"a1": plain, "a2": chengyin}},
        None,
        main_weapon_type="剑", sub_weapon_type="", filters=CandidateFilter(),
    )
    assert [e["_fp"] for e in pooled["main_weapon"]] == ["a1", "a2"]

    with_mock = collect_candidates(
        {}, {"weapon": {"m1": _eq("模拟剑", "剑", _fp="mock_1"),
                        "m2": _eq("模拟枪", "枪", _fp="mock_2")}},
        main_weapon_type="剑", sub_weapon_type="枪", filters=CandidateFilter(),
    )
    assert [e["name"] for e in with_mock["main_weapon"]] == ["模拟剑"]
    assert [e["name"] for e in with_mock["sub_weapon"]] == ["模拟枪"]


def test_filters_level_threshold_and_affix_modes():
    low = _eq("低", "冠胄", level=100)
    full = _eq("满", "冠胄", **{f"affix_{i}": {"name": "劲", "value": 1} for i in range(2, 6)})
    dingyin = _eq("定", "冠胄", dingyin={"name": "外功穿透", "value": 1})
    bag = {"head": {"1": low, "2": full, "3": dingyin}}

    def names(f):
        return [e["name"] for e in collect_candidates(
            bag, None, main_weapon_type="", sub_weapon_type="", filters=f)["head"]]

    assert names(CandidateFilter(level_threshold=105)) == ["满", "定"]
    assert names(CandidateFilter(affix_filter="full_tuning")) == ["满"]
    assert names(CandidateFilter(affix_filter="dingyin")) == ["定"]
