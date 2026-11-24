"""词条上限比例现算入口。

cap_pct 是给调律 DSL 快查用的派生缓存，py 侧一律现算——这些用例锁住
「现算结果与来源口径无关」和「缓存脏了也不影响现算」两条性质。
"""

from __future__ import annotations

from lvjiang.apps.yysls.core.affix_cap import (
    affix_cap_pct,
    affix_cap_ratio,
    affix_dict_cap_pct,
    equip_affix_cap_pcts,
)

# 110 级上限（game_config.yaml）
_CAP_ATK = 121.4
_CAP_JIN = 76.8


def test_two_source_precisions_collapse_to_one_ratio():
    """手填承音上限（2 位小数）与游戏实测（1 位小数）现算出同一个百分比。"""
    assert affix_cap_pct(110, "最大外功攻击", 114.12) == 94.0
    assert affix_cap_pct(110, "最大外功攻击", 114.1) == 94.0
    assert affix_cap_pct(110, "劲", 72.19) == 94.0
    assert affix_cap_pct(110, "劲", 72.2) == 94.0


def test_dingyin_is_not_capped_by_chengyin_ratio():
    assert affix_cap_pct(110, "外功穿透", 16.8) == 100.0


def test_ratio_at_cap_is_one():
    assert affix_cap_ratio(110, "最大外功攻击", _CAP_ATK) == 1.0


def test_missing_cap_data_returns_none():
    assert affix_cap_ratio(110, "根本不存在的词条", 1.0) is None
    assert affix_cap_pct(110, "最大外功攻击", None) is None
    assert affix_cap_pct(None, "最大外功攻击", 100.0) is None
    assert affix_cap_pct(110, "", 100.0) is None


def test_bool_is_not_a_numeric_value():
    assert affix_cap_ratio(110, "最大外功攻击", True) is None


def test_stale_cache_does_not_affect_computation():
    affix = {"name": "最大外功攻击", "value": _CAP_ATK, "cap_pct": 90.8}
    assert affix_dict_cap_pct(affix, 110) == 100.0


def test_equip_pcts_skip_unknown_affixes_only():
    equip = {
        "level": 110,
        "affix_1": {"name": "最大外功攻击", "value": _CAP_ATK},
        "affix_2": {"name": "劲", "value": _CAP_JIN},
        "affix_3": {"name": "根本不存在的词条", "value": 1.0},
        "affix_4": None,
    }
    assert equip_affix_cap_pcts(equip) == [100.0, 100.0]


def test_equip_pcts_accept_string_level():
    equip = {"level": "110",
             "affix_1": {"name": "劲", "value": _CAP_JIN}}
    assert equip_affix_cap_pcts(equip) == [100.0]
