"""生效等级范围与赛季承音作废线接进真实配置后的行为。

锁住新赛季的三件事：115 不再首出哪些词条、115 调律库换掉了哪些词条、低于
赛季作废线的装备不再参与满等级假设。数值可以晚到，这些结构规则先立住。
"""

from __future__ import annotations

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.models import SeasonConfig
from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates
from lvjiang.apps.yysls.core.combat.combat_attrs import apply_hypothetical_caps
from lvjiang.apps.yysls.core.equip_validator import validate_equipment_dict


@pytest.fixture
def gc():
    return get_game_config()


# ─── 首词条按等级 ──────────────────────────────────────────

def test_weapon_stops_first_rolling_min_wuxiang_at_115(gc):
    assert "最小无相攻击" in gc.get_first_affixes("weapon", 110)
    assert "最小无相攻击" not in gc.get_first_affixes("weapon", 115)
    # 同名的「最大无相攻击」没被移除，不能一起误杀
    assert "最大无相攻击" in gc.get_first_affixes("weapon", 115)


@pytest.mark.parametrize("part", ["leg", "wrist"])
def test_leg_and_wrist_stop_first_rolling_ti_at_115(gc, part):
    assert "体" in gc.get_first_affixes(part, 110)
    assert "体" not in gc.get_first_affixes(part, 115)


def test_first_affixes_without_a_level_stay_complete(gc):
    """不给等级就是全集：配置编辑器要能看到退役词条并改它的范围。"""
    assert "最小无相攻击" in gc.get_first_affixes("weapon")


# ─── 调律候选按等级 ────────────────────────────────────────

def _head(level: int) -> dict:
    return {"type": "冠胄", "level": level}


def test_115_head_swaps_the_two_qishu_affixes_for_the_merged_one(gc):
    at_110 = normal_affix_candidates(_head(110), gc)
    at_115 = normal_affix_candidates(_head(115), gc)

    assert "单体类奇术增伤" in at_110 and "群体类奇术增伤" in at_110
    assert "全奇术增伤" not in at_110
    assert "全奇术增伤" in at_115
    assert "单体类奇术增伤" not in at_115
    assert "群体类奇术增伤" not in at_115


def test_candidates_without_a_level_are_not_narrowed(gc):
    """没带等级的历史数据不按等级收窄，宁可宽也不误杀。"""
    names = normal_affix_candidates({"type": "冠胄"}, gc)

    assert "单体类奇术增伤" in names and "全奇术增伤" in names


def test_retired_affix_is_still_a_known_affix(gc):
    """退役只挡新产出。旧装备上存着它、OCR 扫到它，都还得认。"""
    assert "单体类奇术增伤" in gc.get_normal_affix_names()
    assert gc.get_affix_caps(110, "单体类奇术增伤") is not None


# ─── 首词条校验按原生等级 ──────────────────────────────────

def _leg_with_first(first_name: str, level: int, original: int) -> dict:
    return {
        "type": "胫甲", "level": level, "original_level": original,
        "affix_1": {"name": first_name, "value": 1.0},
    }


def test_chengyin_equipment_keeps_its_original_first_affix():
    """原生 110 首出的「体」承音到 115 仍然合法——首词条不会被换掉。"""
    reasons = validate_equipment_dict(_leg_with_first("体", 115, 110))

    assert not [r for r in reasons if "首词条" in r.message]


def test_natively_115_equipment_cannot_have_a_retired_first_affix():
    reasons = validate_equipment_dict(_leg_with_first("体", 115, 115))

    assert [r for r in reasons if "首词条" in r.message]


# ─── 赛季承音作废线 ────────────────────────────────────────

@pytest.fixture
def season_floor(monkeypatch, gc):
    """把当前赛季固定成「最低承音 105」，不依赖机器日期。"""
    def _apply(floor: int | None):
        monkeypatch.setattr(
            gc, "current_season",
            lambda: SeasonConfig(season_number=9, equip_level=115,
                                 min_chengyin_level=floor))
    return _apply


def test_levels_below_the_floor_cannot_chengyin(gc, season_floor):
    season_floor(105)

    assert not gc.can_chengyin_this_season(100)
    assert gc.can_chengyin_this_season(105)
    assert gc.can_chengyin_this_season(110)


def test_no_floor_allows_every_level(gc, season_floor):
    season_floor(None)

    assert gc.can_chengyin_this_season(100)


def test_unknown_level_is_not_written_off(gc, season_floor):
    season_floor(105)

    assert gc.can_chengyin_this_season(None)


# ─── 满等级投影 ────────────────────────────────────────────

def _qishu_head(level: int) -> dict:
    return {
        "type": "冠胄", "level": level, "original_level": level,
        "affix_2": {"name": "单体类奇术增伤", "value": 10.0},
    }


def test_abandoned_equipment_is_not_projected_to_full_level(season_floor):
    """低于作废线的装备本赛季已经升不动了，算进满等级会把毕业率算高。"""
    season_floor(105)

    result = apply_hypothetical_caps({"head": _qishu_head(100)},
                                     full_level=115)

    assert result["head"]["level"] == 100
    assert not result["head"].get("is_chengyin")


def test_equipment_at_the_floor_still_projects(season_floor):
    season_floor(105)

    result = apply_hypothetical_caps({"head": _qishu_head(105)},
                                     full_level=115)

    assert result["head"]["level"] == 115
    assert result["head"]["is_chengyin"] is True


def test_projection_never_touches_the_original(season_floor):
    """投影是派生副本：真实装备必须保持游戏里的样子。"""
    season_floor(105)
    original = _qishu_head(110)

    apply_hypothetical_caps({"head": original}, full_level=115)

    assert original["level"] == 110


# ─── 神力归属 ──────────────────────────────────────────────

def test_merged_qishu_affix_is_registered_as_divine(gc):
    """奇术类是神力词条：每件装备最多一条，且转律不会产出。

    新词条不登记进增效类的话，这两条铁律对它都不生效。
    """
    categories = gc.get_raw().get("affix_categories") or {}
    assert "全奇术增伤" in categories.get("增效类", [])


def test_two_divine_affixes_on_one_equipment_are_illegal():
    reasons = validate_equipment_dict({
        "type": "冠胄", "level": 115, "original_level": 115,
        "affix_2": {"name": "全奇术增伤", "value": 10.0},
        "affix_3": {"name": "全武学增效", "value": 10.0},
    })

    assert [r for r in reasons if "神力" in r.message]
