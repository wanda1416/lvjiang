"""满定音 = 按玩法配齐，而不是「当前定音顶满」。"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import apply_hypothetical_caps


@pytest.fixture
def styled():
    """临时给「纯唐」配上输出/防御定音目标。"""
    g = get_game_config()
    styles = g.get_playstyles()
    g._playstyles = {**styles, "纯唐": {
        **styles["纯唐"],
        "output_dingyin": "外功穿透",
        "defense_dingyin": "无名剑法蓄力技增伤",
    }}
    try:
        yield g
    finally:
        g.reload()


def _equip(part, dingyin_name):
    return {"type": part, "level": 110, "is_chengyin": False,
            "dingyin": {"name": dingyin_name, "value": 1.0}}


def test_non_target_dingyin_is_replaced_not_kept(styled):
    """输出装备定了无相穿透——那个定音位是白定的，不能计入。"""
    out = apply_hypothetical_caps(
        {"main_weapon": _equip("主武器", "无相穿透")},
        full_dingyin=True, playstyle="纯唐")

    assert out["main_weapon"]["dingyin"]["name"] == "外功穿透"
    assert out["main_weapon"]["dingyin"]["value"] > 1.0


def test_defense_slots_converge_to_one_skill_affix(styled):
    """四件防具各定各的音 → 收敛成同一个技能增效。

    这正是「三个防具定音改写成一个」：以前只顶数值，三行各自变大；现在按玩法
    配齐，四件同名同值。
    """
    equipped = {
        "head": _equip("冠胄", "无名剑法武学技增伤"),
        "chest": _equip("胸甲", "无名剑法特殊技增伤"),
        "leg": _equip("胫甲", "无名枪法特殊技增伤"),
        "wrist": _equip("腕甲", "无名剑法蓄力技增伤"),
    }

    out = apply_hypothetical_caps(equipped, full_dingyin=True, playstyle="纯唐")

    names = {v["dingyin"]["name"] for v in out.values()}
    assert names == {"无名剑法蓄力技增伤"}
    values = {v["dingyin"]["value"] for v in out.values()}
    assert len(values) == 1 and values.pop() > 1.0


def test_without_a_playstyle_the_old_semantics_hold(styled):
    """没绑玩法时退回旧行为：保持原定音名，只把数值顶满。

    方案没绑玩法是正常状态（武学没登记进任何玩法），不该因此算不出数。
    """
    out = apply_hypothetical_caps(
        {"main_weapon": _equip("主武器", "无相穿透")},
        full_dingyin=True, playstyle="")

    assert out["main_weapon"]["dingyin"]["name"] == "无相穿透"
    assert out["main_weapon"]["dingyin"]["value"] > 1.0


def test_all_shipped_playstyles_use_outer_pen_for_output(styled):
    """全部内置玩法的输出部位都应换成外功穿透。"""
    out = apply_hypothetical_caps(
        {"main_weapon": _equip("主武器", "无相穿透")},
        full_dingyin=True, playstyle="双刀")

    assert out["main_weapon"]["dingyin"]["name"] == "外功穿透"
