"""词条生效等级范围的区间原语。

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
