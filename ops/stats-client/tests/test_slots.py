"""``scripts/telemetry_analysis/slots.py`` 的终态重建 + 槽位查询测试。

放在这里（而不是 ``scripts/``）是因为主项目的 ``tests/`` 不覆盖 ``scripts/``
（见 ``scripts/analyze_telemetry_rolls.py`` 本身也没有专门测试），而
ops/stats-client 已经建立了"sys.path 注入后直接测 telemetry_analysis"的
先例（见 ``test_metrics_and_bridge.py``）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from telemetry_analysis.slots import (  # noqa: E402
    conditional_slot_distribution,
    observed_slots,
    parse_slot_range,
    reconstruct_final_state,
    slot_distribution,
    slot_range_distribution,
)


def _affix(name, is_transferred=False, cap_pct=10.0):
    return {"affix": name, "cap_pct": cap_pct, "is_transferred": is_transferred}


def _roll(slot, affix, resets=0, is_transferred=False):
    return {"slot": slot, "affix": affix, "resets": resets,
           "is_transferred": is_transferred, "cap_pct": 20.0, "food": "none"}


def _session(part="leg", initial=None, rolls=None):
    return {"part": part, "weapon_type": "", "level": 80, "quality": "orange",
           "mode": "normal", "active_rule": "default",
           "initial_affixes": initial or [], "rolls": rolls or []}


class TestReconstructFinalState:
    def test_no_rolls_uses_initial_affixes_as_is(self):
        s = _session(initial=[_affix("劲"), _affix("会心")])
        item = reconstruct_final_state(s)
        assert item.slots == {1: "劲", 2: "会心"}
        assert not item.is_transferred_any

    def test_later_roll_overwrites_earlier_roll_on_same_slot(self):
        """核心场景：第 2 格先调出「攻击」，又重调成「会心」——终态应该是
        「会心」，不是两次都算一次（这正是旧口径"按每次观测计数"的问题）。"""
        s = _session(initial=[_affix("劲")], rolls=[
            _roll(2, "攻击"), _roll(2, "会心"),
        ])
        item = reconstruct_final_state(s)
        assert item.slots == {1: "劲", 2: "会心"}

    def test_reset_clears_back_to_first_affix_only(self):
        """重置后只剩首词条，重置前调出的其它格子不该留在终态里。"""
        s = _session(initial=[_affix("劲")], rolls=[
            _roll(2, "攻击", resets=0),
            _roll(3, "命中", resets=0),
            _roll(2, "会心", resets=1),  # 重置发生，清回只剩首词条，再调出这一格
        ])
        item = reconstruct_final_state(s)
        assert item.slots == {1: "劲", 2: "会心"}  # 第 3 格（命中）应该被重置清掉

    def test_transferred_roll_excluded_and_flags_whole_item(self):
        s = _session(initial=[_affix("劲")], rolls=[
            _roll(2, "会心"),
            _roll(3, "外功", is_transferred=True),
        ])
        item = reconstruct_final_state(s)
        assert 3 not in item.slots  # 转律那一格不计入
        assert item.is_transferred_any

    def test_transferred_initial_affix_flags_item(self):
        s = _session(initial=[_affix("劲"), _affix("会心", is_transferred=True)])
        item = reconstruct_final_state(s)
        assert item.slots == {1: "劲"}
        assert item.is_transferred_any


class TestSlotDistribution:
    def _items(self):
        sessions = [
            _session(part="leg", initial=[_affix("劲")],
                     rolls=[_roll(2, "会心"), _roll(3, "外功")]),
            _session(part="leg", initial=[_affix("劲")],
                     rolls=[_roll(2, "会心"), _roll(3, "命中")]),
            _session(part="leg", initial=[_affix("身法")],
                     rolls=[_roll(2, "攻击"), _roll(3, "外功")]),
            _session(part="weapon", initial=[_affix("劲")],
                     rolls=[_roll(2, "破防"), _roll(3, "会心")]),
        ]
        from telemetry_analysis.slots import reconstruct_all
        return [it for it in reconstruct_all(sessions) if not it.is_transferred_any]

    def test_marginal_ignores_transferred_items(self):
        items = self._items()
        stat = slot_distribution(items, 2)
        assert stat.n == 4  # 全部普通装备都算

    def test_filter_by_part(self):
        items = self._items()
        stat = slot_distribution(items, 2, part="leg")
        assert stat.n == 3
        assert stat.counts["会心"] == 2

    def test_filter_by_first_affix(self):
        items = self._items()
        stat = slot_distribution(items, 2, part="leg", first_affix="劲")
        assert stat.n == 2
        assert stat.counts == {"会心": 2}

    def test_range_union_counts_item_once_even_with_two_hits(self):
        """一件装备在区间内的两格都出现同一词条时，并集口径只算一次。"""
        sessions = [_session(part="leg", initial=[_affix("劲")],
                             rolls=[_roll(2, "会心"), _roll(3, "会心")])]
        from telemetry_analysis.slots import reconstruct_all
        items = reconstruct_all(sessions)
        stat = slot_range_distribution(items, 2, 3)
        assert stat.n == 1
        assert stat.counts["会心"] == 1  # 不是 2

    def test_range_union_denominator_excludes_items_with_no_known_slot(self):
        sessions = [
            _session(part="leg", initial=[_affix("劲")], rolls=[_roll(2, "会心")]),
            _session(part="leg", initial=[_affix("劲")], rolls=[]),  # 2-3 格都没调过
        ]
        from telemetry_analysis.slots import reconstruct_all
        items = reconstruct_all(sessions)
        stat = slot_range_distribution(items, 2, 3)
        assert stat.n == 1  # 第二件在区间内完全没有已知格，不进分母

    def test_conditional_distribution_filters_on_given_slot_first(self):
        # 两件 leg/劲 的第 2 格都是「会心」，第 3 格分别是 外功/命中
        items = self._items()
        stat = conditional_slot_distribution(
            items, given_slot=2, given_affix="会心", target_lo=3, target_hi=3,
            part="leg", first_affix="劲")
        assert stat.n == 2
        assert stat.counts == {"外功": 1, "命中": 1}

    def test_observed_slots_reports_only_slots_actually_seen(self):
        items = self._items()
        assert observed_slots(items) == [1, 2, 3]


class TestParseSlotRange:
    def test_single_number(self):
        assert parse_slot_range("3") == (3, 3)

    def test_range(self):
        assert parse_slot_range("2-5") == (2, 5)

    def test_invalid_range_raises(self):
        import pytest
        with pytest.raises(ValueError):
            parse_slot_range("5-2")

    def test_invalid_format_raises(self):
        import pytest
        with pytest.raises(ValueError):
            parse_slot_range("abc")
