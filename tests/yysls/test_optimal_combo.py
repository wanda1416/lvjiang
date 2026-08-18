"""Tests for optimal equipment combination search.

Covers:
- Dominance pruning correctness
- Generator enumeration completeness
- Top-R leaderboard ordering
- Delta pre-computation against real calculator
- End-to-end search: result >= any random combination
- Performance benchmark: 8 slots × 20 candidates < 5s
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    build_graduation_attrs,
)
from lvjiang.apps.yysls.core.graduation.combo_rules import (
    CandidateRuleContext,
    TuningJunkRule,
)
from lvjiang.apps.yysls.core.graduation.optimal_combo import (
    SLOT_KEYS,
    TopRLeaderboard,
    _dominates,
    _generate_combos,
    _score_vector,
    compute_slot_deltas,
    prune_dominated,
    search_optimal_combo,
)

# ---------------------------------------------------------------------------
# Unit tests — pure logic (no game data needed)
# ---------------------------------------------------------------------------

class TestDominance:
    """_dominates / prune_dominated"""

    def test_equal_attrs_dominate_each_other(self) -> None:
        a = CombatAttributes(min_outer=100, max_outer=200)
        assert _dominates(a, a) is True

    def test_strictly_better_dominates(self) -> None:
        better = CombatAttributes(min_outer=200, max_outer=300, crit_dmg=0.5)
        worse = CombatAttributes(min_outer=100, max_outer=200, crit_dmg=0.3)
        assert _dominates(better, worse) is True
        assert _dominates(worse, better) is False

    def test_partial_advantage_no_dominance(self) -> None:
        a = CombatAttributes(min_outer=200, max_outer=100)  # better min, worse max
        b = CombatAttributes(min_outer=100, max_outer=200)
        assert _dominates(a, b) is False
        assert _dominates(b, a) is False

    def test_extra_attrs_dominance(self) -> None:
        a = CombatAttributes(
            min_outer=100,
            extra_attrs={"剑武学增伤": 0.1},
        )
        b = CombatAttributes(
            min_outer=100,
            extra_attrs={"剑武学增伤": 0.05},
        )
        assert _dominates(a, b) is True
        assert _dominates(b, a) is False

    def test_extra_attrs_missing_key_treated_as_zero(self) -> None:
        a = CombatAttributes(
            min_outer=100,
            extra_attrs={"剑武学增伤": 0.1},
        )
        b = CombatAttributes(min_outer=100)
        # a has extra key with 0.1, b has 0 (missing) → a dominates b
        assert _dominates(a, b) is True
        # b does not dominate a (a has higher extra attr)
        assert _dominates(b, a) is False

    def test_prune_dominated_removes_inferior(self) -> None:
        good = ({"name": "good"}, CombatAttributes(min_outer=200, max_outer=300), [200, 300])
        bad = ({"name": "bad"}, CombatAttributes(min_outer=100, max_outer=200), [100, 200])
        slot_deltas = {"main_weapon": [good, bad]}
        pruned = prune_dominated(slot_deltas)
        names = [e[0]["name"] for e in pruned["main_weapon"]]
        assert "good" in names
        assert "bad" not in names

    def test_prune_dominated_keeps_non_dominated(self) -> None:
        a = ({"name": "a"}, CombatAttributes(min_outer=200, max_outer=100), [200, 100])
        b = ({"name": "b"}, CombatAttributes(min_outer=100, max_outer=200), [100, 200])
        slot_deltas = {"main_weapon": [a, b]}
        pruned = prune_dominated(slot_deltas)
        assert len(pruned["main_weapon"]) == 2

    def test_prune_dominated_handles_equal_entries(self) -> None:
        e1 = ({"name": "e1"}, CombatAttributes(min_outer=100), [100])
        e2 = ({"name": "e2"}, CombatAttributes(min_outer=100), [100])
        slot_deltas = {"main_weapon": [e1, e2]}
        pruned = prune_dominated(slot_deltas)
        # Equal entries: keep first (by index tiebreak)
        assert len(pruned["main_weapon"]) == 1


class TestGenerateCombos:
    """_generate_combos — lazy enumeration"""

    def test_empty_slots(self) -> None:
        combos = list(_generate_combos([], []))
        assert combos == []

    def test_single_slot(self) -> None:
        combos = list(_generate_combos(["a"], [3]))
        assert len(combos) == 3
        assert [0] in combos
        assert [1] in combos
        assert [2] in combos

    def test_two_slots_full_product(self) -> None:
        combos = list(_generate_combos(["a", "b"], [2, 3]))
        assert len(combos) == 6
        # All combinations present
        expected = {(i, j) for i in range(2) for j in range(3)}
        actual = {tuple(c) for c in combos}
        assert actual == expected

    def test_eight_slots_cartesian_product(self) -> None:
        sizes = [2, 2, 2, 2, 2, 2, 2, 2]
        keys = SLOT_KEYS[:8]
        combos = list(_generate_combos(keys, sizes))
        assert len(combos) == 2**8  # 256

    def test_each_combo_is_list_of_correct_length(self) -> None:
        sizes = [3, 2, 4]
        for combo in _generate_combos(["a", "b", "c"], sizes):
            assert len(combo) == 3
            assert 0 <= combo[0] < 3
            assert 0 <= combo[1] < 2
            assert 0 <= combo[2] < 4


class TestTopRLeaderboard:
    """TopRLeaderboard — fixed-capacity ranking"""

    def test_insert_within_capacity(self) -> None:
        board = TopRLeaderboard(capacity=3)
        assert board.insert(0.8, [0], 1000) is True
        assert board.insert(0.7, [1], 900) is True
        assert board.insert(0.9, [2], 1100) is True
        assert len(board.top()) == 3

    def test_top_returns_sorted_descending(self) -> None:
        board = TopRLeaderboard(capacity=5)
        board.insert(0.5, [0], 500)
        board.insert(0.9, [1], 900)
        board.insert(0.7, [2], 700)
        top = board.top()
        rates = [r for r, _, _ in top]
        assert rates == sorted(rates, reverse=True)

    def test_capacity_overflow_discards_worst(self) -> None:
        board = TopRLeaderboard(capacity=2)
        board.insert(0.5, [0], 500)
        board.insert(0.9, [1], 900)
        # Board full; inserting worse should fail
        assert board.insert(0.3, [2], 300) is False
        # Inserting better should succeed and push out worst
        assert board.insert(0.95, [3], 950) is True
        top = board.top()
        assert top[0][0] == 0.95
        assert top[1][0] == 0.9

    def test_top_n_limits_results(self) -> None:
        board = TopRLeaderboard(capacity=10)
        for i in range(10):
            board.insert(0.5 + i * 0.01, [i], 500 + i * 10)
        assert len(board.top(3)) == 3
        assert len(board.top(5)) == 5


class TestScoreVector:
    """_score_vector — linear ranking helper"""

    def test_sum_of_abs(self) -> None:
        assert _score_vector([1.0, -2.0, 3.0]) == 6.0

    def test_zero_vector(self) -> None:
        assert _score_vector([0.0, 0.0]) == 0.0


class TestCandidateRules:
    def test_tuning_rule_junk_is_removed(self) -> None:
        good = {
            "type": "环", "quality": "gold",
            "affix_1": {"name": "最小外功攻击", "value": 100},
            "affix_2": {"name": "全武学增效", "value": 4.9},
        }
        junk = {
            "type": "环", "quality": "gold",
            "affix_1": {"name": "最小外功攻击", "value": 100},
            "affix_2": {"name": "气血最大值", "value": 1000},
        }
        kept = TuningJunkRule("huixin_small", "双切").apply(
            "ring", [good, junk], CandidateRuleContext("裂石·钧"),
        )
        assert kept == [good]


class TestApplyComboThroughInventoryApi:
    @staticmethod
    def _inventory(tmp_path):
        from lvjiang.apps.yysls.core.combat.equipment import EquipmentInventory
        from lvjiang.apps.yysls.core.loadout import LoadoutRepository

        inventory = EquipmentInventory.__new__(EquipmentInventory)
        inventory._repo = LoadoutRepository("test", tmp_path)
        state = inventory._repo.load()
        inventory._repo.assign_equipment(
            state.active_plan_id, "ring",
            {"name": "old_ring", "type": "环", "_fp": "old"})
        inventory._repo.assign_equipment(
            state.active_plan_id, "head",
            {"name": "same_head", "type": "冠胄", "_fp": "head"})
        inventory._repo.upsert_item(
            {"name": "new_ring", "type": "环", "_fp": "new"})
        inventory.reload()
        return inventory

    def test_only_changed_slots_are_applied_and_saved_once(
        self, tmp_path,
    ) -> None:
        inventory = self._inventory(tmp_path)

        inventory.apply_combos({
            "ring": inventory.bag_items["ring"]["new"],
            "head": inventory.equipped["head"].copy(),
        })

        assert inventory.equipped["ring"]["name"] == "new_ring"
        assert inventory.equipped["head"]["name"] == "same_head"
        assert inventory._repo.load().revision > 0

# ---------------------------------------------------------------------------
# Integration tests — require real graduation calculator
# ---------------------------------------------------------------------------

def _get_calculator():
    """Get a real GenericCalculator for testing."""
    from lvjiang.apps.yysls.core.graduation import (
        get_graduation_calculator,
        invalidate_graduation_cache,
    )
    invalidate_graduation_cache()
    calc = get_graduation_calculator("鸣金·虹", "基础方案")
    assert calc is not None
    return calc


def _make_synthetic_candidates(
    per_slot: int = 5,
    slots: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Create synthetic equipment candidates for testing.

    Each equipment has affixes that contribute to combat attributes.
    """
    if slots is None:
        slots = list(SLOT_KEYS)
    candidates: dict[str, list[dict]] = {}
    for si, slot in enumerate(slots):
        equips: list[dict] = []
        for j in range(per_slot):
            equip: dict[str, Any] = {
                "name": f"{slot}_{j}",
                "type": "剑" if "weapon" in slot else slot,
                "level": 110,
                "quality": "gold",
                "part": _slot_to_part(slot),
            }
            # Varying affixes to create different attribute profiles
            base_val = 50 + si * 10 + j * 5
            equip["affix_1"] = {"name": "最小外功攻击", "value": base_val}
            equip["affix_2"] = {"name": "最大外功攻击", "value": base_val * 2}
            if j % 2 == 0:
                equip["affix_3"] = {"name": "会心率", "value": 2.0 + j * 0.5}
            if j % 3 == 0:
                equip["affix_4"] = {"name": "会心伤害加成", "value": 3.0 + j}
            equips.append(equip)
        candidates[slot] = equips
    return candidates


def _slot_to_part(slot: str) -> str:
    """Map slot_key to equipment part name."""
    mapping = {
        "main_weapon": "剑", "sub_weapon": "剑",
        "head": "冠胄", "chest": "胸甲",
        "ring": "环", "pendant": "佩",
        "leg": "胫甲", "wrist": "腕甲",
    }
    return mapping.get(slot, slot)


class TestComputeSlotDeltas:
    """Delta pre-computation with real game config"""

    def test_single_equip_delta_matches_manual(self) -> None:
        """A single equipment's delta should equal aggregate + base attrs."""
        from lvjiang.apps.yysls.config import get_game_config
        from lvjiang.apps.yysls.core.combat.combat_attrs import (
            aggregate_equipment_attrs,
            compute_equip_base_attrs,
        )

        calc = _get_calculator()
        input_specs = calc._data["program"]["inputs"]
        gc = get_game_config()

        equip = {
            "name": "测试剑",
            "type": "剑",
            "level": 110,
            "quality": "gold",
            "part": "剑",
            "affix_1": {"name": "最小外功攻击", "value": 100},
            "affix_2": {"name": "最大外功攻击", "value": 200},
        }
        candidates = {"main_weapon": [equip]}
        slot_deltas = compute_slot_deltas(
            candidates, input_specs, gc.get_base_attr_values,
        )
        assert "main_weapon" in slot_deltas
        assert len(slot_deltas["main_weapon"]) == 1

        _, delta, vec = slot_deltas["main_weapon"][0]

        # Manual computation
        single = {"main_weapon": equip}
        expected_affix = aggregate_equipment_attrs(single)
        expected_base = compute_equip_base_attrs(single, gc.get_base_attr_values)
        expected_delta = expected_affix + expected_base

        # Compare fixed fields
        from dataclasses import fields as dc_fields
        for f in dc_fields(CombatAttributes):
            if f.name == "extra_attrs":
                continue
            assert getattr(delta, f.name) == pytest.approx(
                getattr(expected_delta, f.name), abs=1e-9,
            ), f"mismatch on {f.name}"

        # Input vector should match _attrs_to_input(delta, input_specs)
        assert len(vec) == len(input_specs)


class TestSearchOptimalCombo:
    """End-to-end search tests"""

    def test_search_returns_results(self) -> None:
        """Search with small candidate set should return results."""
        calc = _get_calculator()
        candidates = _make_synthetic_candidates(per_slot=3)
        base_attrs = CombatAttributes(
            min_outer=1500, max_outer=5000,
            min_mingjin=500, max_mingjin=1300,
            precision=0.80, crit_rate=0.18, intent_rate=0.39,
            crit_dmg=0.5, intent_dmg=0.4,
            outer_bonus=0.05, mingjin_bonus=0.10,
            all_skill_bonus=0.08, boss_bonus=0.08,
            extra_attrs={"剑武学增伤": 0.08, "无名剑法蓄力技增伤": 0.32},
        )
        results = search_optimal_combo(candidates, calc, base_attrs)
        assert len(results) > 0
        assert len(results) <= 5
        # Results sorted by rate descending
        for i in range(len(results) - 1):
            assert results[i]["rate"] >= results[i + 1]["rate"]

    def test_search_result_ge_any_random_combo(self) -> None:
        """The optimal result's rate should be >= rate of any random combination."""
        calc = _get_calculator()
        candidates = _make_synthetic_candidates(per_slot=2)
        base_attrs = CombatAttributes(
            min_outer=1500, max_outer=5000,
            min_mingjin=500, max_mingjin=1300,
            precision=0.80, crit_rate=0.18, intent_rate=0.39,
            crit_dmg=0.5, intent_dmg=0.4,
            outer_bonus=0.05, mingjin_bonus=0.10,
            all_skill_bonus=0.08, boss_bonus=0.08,
            extra_attrs={"剑武学增伤": 0.08, "无名剑法蓄力技增伤": 0.32},
        )
        results = search_optimal_combo(
            candidates, calc, base_attrs, use_dominance_pruning=False,
        )
        assert results
        best_rate = results[0]["rate"]

        # Exhaustive check: 2^8 = 256 combos — all must be <= best
        from lvjiang.apps.yysls.core.graduation.graduation_program import ProgramRuntime
        program = calc._data["program"]
        input_specs = program["inputs"]
        baseline = calc.baseline_dps()

        from lvjiang.apps.yysls.core.graduation.optimal_combo import (
            _attrs_to_input,
            _make_base_attr_lookup,
        )
        slot_deltas = compute_slot_deltas(
            candidates, input_specs, _make_base_attr_lookup(),
        )
        n_dims = len(input_specs)

        runtime = ProgramRuntime(program, [0.0] * n_dims)
        output_nodes = program["outputs"]
        for combo in _generate_combos(
            list(SLOT_KEYS),
            [len(slot_deltas.get(k, [])) for k in SLOT_KEYS],
        ):
            equipment_attrs = CombatAttributes()
            for si, idx in enumerate(combo):
                slot_key = SLOT_KEYS[si]
                if slot_key in slot_deltas:
                    equipment_attrs = (
                        equipment_attrs + slot_deltas[slot_key][idx][1]
                    )
            effective = build_graduation_attrs(
                base_attrs, equipment_attrs, calc._school,
            )
            acc = _attrs_to_input(effective, input_specs)
            runtime.inputs = acc
            runtime.cache.clear()
            dps = float(runtime.value(output_nodes["dps"]))
            rate = dps / baseline
            assert rate <= best_rate + 1e-9, (
                f"Found combo with rate {rate:.6f} > best {best_rate:.6f}"
            )

    def test_search_with_dominance_pruning(self) -> None:
        """Pruning should not change the optimal result."""
        calc = _get_calculator()
        candidates = _make_synthetic_candidates(per_slot=2)
        base_attrs = CombatAttributes(
            min_outer=1500, max_outer=5000,
            min_mingjin=500, max_mingjin=1300,
            precision=0.80, crit_rate=0.18, intent_rate=0.39,
            crit_dmg=0.5, intent_dmg=0.4,
            outer_bonus=0.05, mingjin_bonus=0.10,
            all_skill_bonus=0.08, boss_bonus=0.08,
            extra_attrs={"剑武学增伤": 0.08, "无名剑法蓄力技增伤": 0.32},
        )
        results_pruned = search_optimal_combo(
            candidates, calc, base_attrs, use_dominance_pruning=True,
        )
        results_full = search_optimal_combo(
            candidates, calc, base_attrs, use_dominance_pruning=False,
        )
        assert results_pruned and results_full
        # Best rate should be identical (pruning only removes dominated)
        assert results_pruned[0]["rate"] == pytest.approx(
            results_full[0]["rate"], rel=1e-9,
        )

    def test_search_cancel_flag(self) -> None:
        """Search should respect cancel flag."""
        calc = _get_calculator()
        candidates = _make_synthetic_candidates(per_slot=3)
        base_attrs = CombatAttributes(
            min_outer=1500, max_outer=5000,
            min_mingjin=500, max_mingjin=1300,
        )
        cancelled = [False]
        call_count = [0]

        def on_progress(evaluated: int, total: int, msg: str) -> None:
            call_count[0] += 1
            if call_count[0] >= 2:
                cancelled[0] = True

        results = search_optimal_combo(
            candidates, calc, base_attrs,
            progress_cb=on_progress,
            cancel_flag=lambda: cancelled[0],
        )
        # Should have returned (possibly partial results)
        assert isinstance(results, list)

    def test_search_empty_candidates(self) -> None:
        """Empty candidates should return empty results."""
        calc = _get_calculator()
        results = search_optimal_combo({}, calc, CombatAttributes())
        assert results == []


class TestPerformanceBenchmark:
    """Performance tests — verify search completes within time budget"""

    def test_8x20_candidates_under_5_seconds(self) -> None:
        """8 slots × 20 candidates should complete in < 5 seconds."""
        calc = _get_calculator()
        candidates = _make_synthetic_candidates(per_slot=20)
        base_attrs = CombatAttributes(
            min_outer=1500, max_outer=5000,
            min_mingjin=500, max_mingjin=1300,
            precision=0.80, crit_rate=0.18, intent_rate=0.39,
            crit_dmg=0.5, intent_dmg=0.4,
            outer_bonus=0.05, mingjin_bonus=0.10,
            all_skill_bonus=0.08, boss_bonus=0.08,
            extra_attrs={"剑武学增伤": 0.08, "无名剑法蓄力技增伤": 0.32},
        )

        start = time.monotonic()
        results = search_optimal_combo(
            candidates, calc, base_attrs,
            use_dominance_pruning=True,
            max_per_slot=10,  # safety net to keep combo count manageable
        )
        elapsed = time.monotonic() - start

        assert results, "Should return at least one result"
        assert elapsed < 5.0, f"Search took {elapsed:.2f}s, expected < 5s"
