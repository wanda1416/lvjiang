"""Optimal equipment combination search for graduation rate.

Brute-force enumeration with dominance pruning and Top-R leaderboard,
inspired by leoq7's findBestBuild strategy.

Key insight: each equipment's contribution to CombatAttributes is a fixed
vector independent of other slots.  We pre-compute these "delta vectors",
optionally prune dominated candidates, then enumerate combinations lazily
via a generator, keeping only the Top-R results by graduation rate.
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields
from typing import Any, Callable

from loguru import logger

from ..combat.combat_attrs import (
    CombatAttributes,
    GraduationAttrContext,
    aggregate_equipment_attrs,
    build_graduation_attrs,
    compute_equip_base_attrs,
)
from .graduation_program import ProgramRuntime

# The 8 equipment slots in enumeration order.
SLOT_KEYS: list[str] = [
    "main_weapon", "sub_weapon", "head", "chest",
    "ring", "pendant", "leg", "wrist",
]


# ---------------------------------------------------------------------------
# Input-vector helpers
# ---------------------------------------------------------------------------

def _attrs_to_input(
    attrs: CombatAttributes,
    input_specs: list[dict[str, str]],
) -> list[float]:
    """Project *CombatAttributes* onto the program's input vector."""
    return [
        float(getattr(attrs, spec["name"], 0.0))
        if spec["kind"] == "field"
        else float(attrs.extra_attrs.get(spec["name"], 0.0))
        for spec in input_specs
    ]


def _zero_input(input_specs: list[dict[str, str]]) -> list[float]:
    """Zero vector matching the program's input dimensionality."""
    return [0.0] * len(input_specs)


# ---------------------------------------------------------------------------
# Delta pre-computation
# ---------------------------------------------------------------------------

def compute_slot_deltas(
    candidates: dict[str, list[dict]],
    input_specs: list[dict[str, str]],
    base_attr_lookup: Callable,
) -> dict[str, list[tuple[dict, CombatAttributes, list[float]]]]:
    """Pre-compute each candidate's attribute contribution.

    Parameters
    ----------
    candidates:
        ``{slot_key: [equip_dict, ...]}`` — candidate equipment per slot.
    input_specs:
        The program's input specification list (from ``data["program"]["inputs"]``).
    base_attr_lookup:
        ``GameConfigManager.get_base_attr_values`` for base-attack lookup.

    Returns
    -------
    dict mapping slot_key to list of ``(equip, combat_attrs_delta, input_vector)``.
    """
    result: dict[str, list[tuple[dict, CombatAttributes, list[float]]]] = {}
    for slot_key, equips in candidates.items():
        slot_data: list[tuple[dict, CombatAttributes, list[float]]] = []
        for equip in equips:
            single = {slot_key: equip}
            affix = aggregate_equipment_attrs(single)
            base = compute_equip_base_attrs(single, base_attr_lookup)
            delta = affix + base
            vec = _attrs_to_input(delta, input_specs)
            slot_data.append((equip, delta, vec))
        result[slot_key] = slot_data
    return result


# ---------------------------------------------------------------------------
# Dominance pruning
# ---------------------------------------------------------------------------

def _dominates(
    a: CombatAttributes,
    b: CombatAttributes,
) -> bool:
    """Return True if *a* is >= *b* on **every** combat-attribute field."""
    for f in dataclass_fields(CombatAttributes):
        if f.name == "extra_attrs":
            continue
        if getattr(a, f.name) < getattr(b, f.name):
            return False
    # Extra-attrs: union of keys from both; missing → 0
    all_keys = set(a.extra_attrs) | set(b.extra_attrs)
    for key in all_keys:
        if a.extra_attrs.get(key, 0.0) < b.extra_attrs.get(key, 0.0):
            return False
    return True


def prune_dominated(
    slot_deltas: dict[str, list[tuple[dict, CombatAttributes, list[float]]]],
) -> dict[str, list[tuple[dict, CombatAttributes, list[float]]]]:
    """Remove candidates that are dominated by another in the same slot."""
    result: dict[str, list[tuple[dict, CombatAttributes, list[float]]]] = {}
    for slot_key, entries in slot_deltas.items():
        n = len(entries)
        kept: list[int] = []
        for i in range(n):
            dominated = False
            for j in range(n):
                if i != j and _dominates(entries[j][1], entries[i][1]):
                    # j dominates i; break ties by index to avoid mutual removal
                    if j < i or not _dominates(entries[i][1], entries[j][1]):
                        dominated = True
                        break
            if not dominated:
                kept.append(i)
        result[slot_key] = [entries[i] for i in kept]
    return result


# ---------------------------------------------------------------------------
# Generator — lazy enumeration (mirrors leoq7's function* pattern)
# ---------------------------------------------------------------------------

def _generate_combos(
    slot_keys: list[str],
    slot_sizes: list[int],
):
    """Yield every combination as ``list[int]`` (one index per slot).

    Iterative DFS — O(slots) memory instead of materialising N^8.
    """
    n = len(slot_keys)
    if n == 0:
        return
    stack: list[tuple[list[int], int]] = [([], 0)]
    while stack:
        combo, depth = stack.pop()
        if depth == n:
            yield combo
            continue
        size = slot_sizes[depth]
        if size == 0:
            # Slot with no candidates — skip with index 0 (shouldn't happen)
            stack.append((combo + [0], depth + 1))
        else:
            for i in range(size - 1, -1, -1):
                stack.append((combo + [i], depth + 1))


# ---------------------------------------------------------------------------
# Top-R Leaderboard
# ---------------------------------------------------------------------------

class TopRLeaderboard:
    """Fixed-capacity leaderboard sorted by graduation rate descending."""

    __slots__ = ("_capacity", "_entries")

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._entries: list[tuple[float, list[int], float]] = []

    @property
    def worst_rate(self) -> float:
        if not self._entries:
            return -1.0
        return self._entries[-1][0]

    @property
    def is_full(self) -> bool:
        return len(self._entries) >= self._capacity

    def insert(self, rate: float, combo: list[int], dps: float) -> bool:
        """Try to insert; return True if it made the leaderboard."""
        if len(self._entries) >= self._capacity:
            if rate <= self._entries[-1][0]:
                return False
            self._entries.pop()
        # Binary-ish insert: list is short (<=200), linear scan is fine
        pos = len(self._entries)
        for i, (r, *_rest) in enumerate(self._entries):
            if rate > r:
                pos = i
                break
        self._entries.insert(pos, (rate, combo, dps))
        return True

    def top(self, n: int = 5) -> list[tuple[float, list[int], float]]:
        return self._entries[:n]


# ---------------------------------------------------------------------------
# Linear-score Top-K safety net
# ---------------------------------------------------------------------------

def _score_vector(vec: list[float]) -> float:
    """Simple sum-of-abs for ranking candidates by linear contribution."""
    return sum(abs(v) for v in vec)


def _apply_top_k_safety(
    slot_deltas: dict[str, list[tuple[dict, CombatAttributes, list[float]]]],
    max_per_slot: int = 10,
) -> dict[str, list[tuple[dict, CombatAttributes, list[float]]]]:
    """If any slot still has > max_per_slot candidates, keep only top-K by linear score."""
    result: dict[str, list[tuple[dict, CombatAttributes, list[float]]]] = {}
    for slot_key, entries in slot_deltas.items():
        if len(entries) > max_per_slot:
            scored = sorted(entries, key=lambda e: _score_vector(e[2]), reverse=True)
            result[slot_key] = scored[:max_per_slot]
        else:
            result[slot_key] = entries
    return result


# ---------------------------------------------------------------------------
# Main search entry point
# ---------------------------------------------------------------------------

def search_optimal_combo(
    candidates: dict[str, list[dict]],
    calculator: Any,  # GenericCalculator
    base_attrs: CombatAttributes,
    *,
    top_r: int = 200,
    use_dominance_pruning: bool = True,
    max_per_slot: int = 0,  # 0 = no limit
    progress_cb: Callable[[int, int, str], None] | None = None,
    cancel_flag: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """Search for the best equipment combinations.

    Parameters
    ----------
    candidates:
        ``{slot_key: [equip_dict, ...]}`` per slot.
    calculator:
        A ``GenericCalculator`` instance (already constructed).
    base_attrs:
        Combat attributes *without* equipment (base + gongjue).
    top_r:
        Leaderboard capacity.
    use_dominance_pruning:
        Whether to remove dominated candidates.
    max_per_slot:
        If > 0, apply linear-score Top-K safety net after pruning.
    progress_cb:
        ``callback(evaluated, total, message)`` for progress updates.
    cancel_flag:
        Callable returning True to abort search early.

    Returns
    -------
    List of up to 5 result dicts, each containing:
    ``rate``, ``dps``, ``total_damage``, ``equipped`` (per-slot equip dicts).
    """
    program = calculator._data["program"]
    input_specs = program["inputs"]
    baseline = calculator.baseline_dps()

    # -- Phase 1: pre-compute deltas --
    slot_keys = [k for k in SLOT_KEYS if k in candidates and candidates[k]]
    slot_deltas = compute_slot_deltas(
        {k: candidates[k] for k in slot_keys},
        input_specs,
        # We need a base_attr_lookup — extract from calculator context
        _make_base_attr_lookup(),
    )

    # Filter out empty slots
    slot_keys = [k for k in slot_keys if slot_deltas.get(k)]
    if not slot_keys:
        return []

    total_combos = 1
    for k in slot_keys:
        total_combos *= len(slot_deltas[k])

    if progress_cb:
        progress_cb(0, total_combos, f"搜索空间: {total_combos:,} 种组合")

    # -- Phase 1b: dominance pruning --
    if use_dominance_pruning:
        slot_deltas = prune_dominated(slot_deltas)
        pruned_combos = 1
        for k in slot_keys:
            pruned_combos *= max(len(slot_deltas.get(k, [])), 1)
        if progress_cb and pruned_combos < total_combos:
            progress_cb(
                0, pruned_combos,
                f"支配剪枝: {total_combos:,} → {pruned_combos:,}",
            )
        total_combos = pruned_combos

    # -- Phase 1c: Top-K safety net --
    if max_per_slot > 0:
        slot_deltas = _apply_top_k_safety(slot_deltas, max_per_slot)
        safe_combos = 1
        for k in slot_keys:
            safe_combos *= max(len(slot_deltas.get(k, [])), 1)
        if safe_combos < total_combos:
            if progress_cb:
                progress_cb(
                    0, safe_combos,
                    f"Top-K 缩减: {total_combos:,} → {safe_combos:,}",
                )
            total_combos = safe_combos

    # -- Prepare fast-lookup arrays for inner loop --
    active_slots = [k for k in slot_keys if slot_deltas.get(k)]
    slot_equip_arrays: list[list[dict]] = []
    slot_attr_arrays: list[list[CombatAttributes]] = []
    for k in active_slots:
        entries = slot_deltas[k]
        slot_equip_arrays.append([e[0] for e in entries])
        slot_attr_arrays.append([e[1] for e in entries])
    slot_sizes = [len(v) for v in slot_attr_arrays]
    n_dims = len(input_specs)

    graduation_context = GraduationAttrContext.from_school(calculator._school)

    # -- Phase 2: enumerate + evaluate --
    board = TopRLeaderboard(top_r)
    evaluated = 0
    batch = 0
    BATCH_SIZE = 4096

    # Reusable accumulation buffer and runtime
    acc = [0.0] * n_dims
    runtime = ProgramRuntime(program, acc)
    output_nodes = program["outputs"]
    cache_clear = runtime.cache.clear

    for combo_indices in _generate_combos(active_slots, slot_sizes):
        # Check cancel every iteration (threading.Event.is_set is fast)
        if cancel_flag and cancel_flag():
            break

        # 所有调用方必须通过统一的毕业率属性预处理（抗性/无相转换）。
        equipment_attrs = CombatAttributes()
        for si, idx in enumerate(combo_indices):
            equipment_attrs = equipment_attrs + slot_attr_arrays[si][idx]
        effective_attrs = build_graduation_attrs(
            base_attrs, equipment_attrs, calculator._school,
            context=graduation_context,
        )
        effective_vec = _attrs_to_input(effective_attrs, input_specs)
        for d in range(n_dims):
            acc[d] = effective_vec[d]

        # Evaluate via ProgramRuntime (reuse object, just clear cache)
        cache_clear()
        dps = float(runtime.value(output_nodes["dps"]))
        rate = dps / baseline

        # Insert into leaderboard
        if len(board._entries) < top_r or rate > board.worst_rate:
            board.insert(rate, list(combo_indices), dps)

        evaluated += 1
        batch += 1
        if batch >= BATCH_SIZE:
            batch = 0
            if progress_cb:
                progress_cb(evaluated, total_combos, "")

    # -- Phase 3: build results --
    results: list[dict[str, Any]] = []
    for rate, combo_indices, dps in board.top(5):
        equipped: dict[str, dict] = {}
        for si, idx in enumerate(combo_indices):
            equipped[active_slots[si]] = slot_equip_arrays[si][idx]
        results.append({
            "rate": rate,
            "dps": dps,
            "total_damage": dps * calculator.combat_time(),
            "equipped": equipped,
        })

    logger.info(
        f"最优组合搜索完成: 评估 {evaluated:,} 种组合, "
        f"最佳毕业率 {results[0]['rate']:.2%}" if results else "无结果",
    )
    return results


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_base_attr_lookup() -> Callable:
    """Create a base-attr lookup function from the global GameConfigManager."""
    from ...config import get_game_config
    gc = get_game_config()
    return gc.get_base_attr_values
