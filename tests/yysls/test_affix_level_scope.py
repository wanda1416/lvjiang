"""词条生效等级范围、赛季承音作废线与跨等级升级。

新赛季会移除一批旧词条、加入新词条，但旧词条不能从配置里删掉——历史装备
上仍然存着它，OCR 仍会扫到，低等阶装备上它依然合法。所以这里锁住的是
「词条在系统里存不存在」和「这个等级还能不能新产出它」两件事必须分开。
"""

from __future__ import annotations

from lvjiang.apps.yysls.config.affix_levels import (
    LevelRange,
    dump_entry,
    parse_entry,
    parse_range,
)
from lvjiang.apps.yysls.config.models import AffixUpgrade

# ─── 区间原语 ──────────────────────────────────────────────

def test_open_range_covers_everything():
    assert LevelRange().covers(96) and LevelRange().covers(115)


def test_through_level_retires_the_affix_above_it():
    retired = LevelRange(through_level=110)

    assert retired.covers(110)
    assert not retired.covers(115)


def test_from_level_hides_the_affix_below_it():
    fresh = LevelRange(from_level=115)

    assert not fresh.covers(110)
    assert fresh.covers(115)


def test_unknown_level_is_never_judged():
    """等级未知就放行：范围是用来收窄新产出的，不是给缺失数据判罪的。"""
    retired = LevelRange(through_level=110)

    assert retired.covers(None) and retired.covers(0)


def test_bare_string_entry_means_open_range():
    assert parse_entry("体") == ("体", LevelRange())


def test_entry_round_trips_through_config():
    name, level_range = parse_entry({"name": "体", "through_level": 110})

    assert (name, level_range) == ("体", LevelRange(0, 110))
    assert dump_entry(name, level_range) == {"name": "体",
                                             "through_level": 110}


def test_open_range_dumps_back_to_a_bare_string():
    """绝大多数词条没有范围，不该平白多两层结构。"""
    assert dump_entry("势", LevelRange()) == "势"


def test_garbage_range_falls_back_to_open():
    assert parse_range("110") == LevelRange()
    assert parse_range({"through_level": "x"}) == LevelRange()


# ─── 升级规则 ──────────────────────────────────────────────

def _merge_rule() -> AffixUpgrade:
    return AffixUpgrade(from_level=110, to_level=115,
                        from_name="单体类奇术增伤", to_name="全奇术增伤")


def test_upgrade_triggers_on_crossing_the_span():
    """「从 110 到 115」判的是跨过这段：105 一路承音到 115 同样要触发。"""
    rule = _merge_rule()

    assert rule.applies("单体类奇术增伤", 110, 115)
    assert rule.applies("单体类奇术增伤", 105, 115)
    assert rule.applies("单体类奇术增伤", 105, 120)


def test_upgrade_does_not_fire_short_of_the_target():
    rule = _merge_rule()

    assert not rule.applies("单体类奇术增伤", 105, 110)


def test_upgrade_does_not_fire_above_the_span():
    """已经在坎上面的装备再升阶不该再被这条规则动。"""
    assert not _merge_rule().applies("单体类奇术增伤", 115, 120)


def test_upgrade_ignores_other_affixes():
    assert not _merge_rule().applies("体", 110, 115)
