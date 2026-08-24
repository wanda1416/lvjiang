"""Optimal equipment combination search for graduation rate.

Brute-force enumeration with dominance pruning and Top-R leaderboard,
using a pruned best-build search strategy.

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
    BONUS_PERCENT_FIELDS,
    CRIT_RATE_CAP,
    INTENT_RATE_CAP,
    PENETRATION_FIELDS,
    PRECISION_BASE,
    CombatAttributes,
    GraduationAttrContext,
    aggregate_equipment_attrs,
    apply_hypothetical_caps,
    compute_equip_base_attrs,
    has_resistance,
)
from .graduation_program import ProgramRuntime

# 固定战斗属性字段名集合：extra_attrs 抗性循环需跳过（原逻辑只对
# BONUS_PERCENT_FIELDS/PENETRATION_FIELDS/THREE_RATE_FIELDS 固定字段套公式）。
_FIXED_ATTR_NAMES: frozenset[str] = frozenset(
    f.name for f in dataclass_fields(CombatAttributes)
) - {"extra_attrs"}

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


def _build_field_index_map(
    input_specs: list[dict[str, str]],
) -> dict[str, int]:
    """Map field name → input vector index for O(1) lookup."""
    return {spec["name"]: i for i, spec in enumerate(input_specs)}


def _compute_base_vec_raw(
    base_attrs: CombatAttributes,
    input_specs: list[dict[str, str]],
    field_index: dict[str, int],
) -> list[float]:
    """Compute base input vector WITHOUT resistance applied.

    This is the raw base contribution. Resistance will be applied
    after combining with equipment contributions.
    """
    vec = _zero_input(input_specs)

    # Fixed fields
    for f in dataclass_fields(CombatAttributes):
        if f.name == "extra_attrs":
            continue
        fname = f.name
        idx = field_index.get(fname)
        if idx is None:
            continue
        vec[idx] = getattr(base_attrs, fname)

    # Extra attrs
    for key, value in base_attrs.extra_attrs.items():
        idx = field_index.get(key)
        if idx is not None:
            vec[idx] = value

    return vec


def _compute_equip_vec_raw(
    equip_attrs: CombatAttributes,
    input_specs: list[dict[str, str]],
    field_index: dict[str, int],
    context: GraduationAttrContext,
) -> list[float]:
    """Compute equipment's contribution vector WITHOUT resistance applied.

    Resistance will be applied after combining all equipment contributions.
    """
    vec = _zero_input(input_specs)
    target_pen = context.target_pen_field

    # Fixed fields
    for f in dataclass_fields(CombatAttributes):
        if f.name == "extra_attrs":
            continue
        fname = f.name
        idx = field_index.get(fname)
        if idx is None:
            continue
        value = getattr(equip_attrs, fname)
        vec[idx] = value

    # Handle wuxiang_pen → target penetration mapping
    if target_pen and equip_attrs.wuxiang_pen != 0:
        idx = field_index.get(target_pen)
        if idx is not None:
            vec[idx] += equip_attrs.wuxiang_pen

    # Extra attrs
    for key, value in equip_attrs.extra_attrs.items():
        idx = field_index.get(key)
        if idx is not None:
            vec[idx] = value

    return vec


def _apply_resistance_to_vec(
    vec: list[float],
    base_vec: list[float],
    field_index: dict[str, int],
    context: GraduationAttrContext,
) -> None:
    """Apply resistance rules to the combined vector (in-place).

    与 build_graduation_attrs 逐字段等效（基准语义已核对）：
    - precision: 阈值与基准均为硬编码常量 PRECISION_BASE/100（非基础面板值）
    - crit_rate/intent_rate: 合并值整体除以 judge_divisor 后封顶
    - BONUS_PERCENT_FIELDS 与 extra_attrs 抗性键: 合并值整体除以 buff_divisor
      （apply_bonus_resistance 的 base 默认 0.0）
    - PENETRATION_FIELDS: 基础面板值不变，仅装备部分（含无相映射）除以 buff_divisor
    base_vec is needed for penetration fields where base stays unchanged.
    """
    judge_divisor = 1 + context.judge_resistance / 100  # 2.45
    buff_divisor = 1 + context.buff_resistance / 100    # 1.15
    precision_base = PRECISION_BASE / 100  # 0.65

    # precision: 阈值/基准都是常量 0.65，combined <= 0.65 时不变
    idx = field_index.get("precision")
    if idx is not None and vec[idx] > precision_base:
        vec[idx] = precision_base + (vec[idx] - precision_base) / judge_divisor

    idx = field_index.get("crit_rate")
    if idx is not None:
        vec[idx] = min(vec[idx] / judge_divisor, CRIT_RATE_CAP)

    idx = field_index.get("intent_rate")
    if idx is not None:
        vec[idx] = min(vec[idx] / judge_divisor, INTENT_RATE_CAP)

    # 增效字段: apply_bonus_resistance(combined, base=0.0) → 合并值整体除
    for fname in BONUS_PERCENT_FIELDS:
        idx = field_index.get(fname)
        if idx is not None:
            vec[idx] = vec[idx] / buff_divisor

    # 穿透字段: 基础值不变，装备部分（_compute_equip_vec_raw 已含无相映射）除
    for fname in PENETRATION_FIELDS:
        idx = field_index.get(fname)
        if idx is not None:
            equipment_part = vec[idx] - base_vec[idx]
            vec[idx] = base_vec[idx] + equipment_part / buff_divisor

    # extra_attrs 抗性键: 合并值整体除；跳过固定字段（由以上分支处理，
    # 且原逻辑的 extra_attrs 循环不会触及固定字段名）
    for key, idx in field_index.items():
        if key in _FIXED_ATTR_NAMES:
            continue
        if has_resistance(key):
            vec[idx] = vec[idx] / buff_divisor


# ---------------------------------------------------------------------------
# Delta pre-computation
# ---------------------------------------------------------------------------

def compute_slot_deltas(
    candidates: dict[str, list[dict]],
    input_specs: list[dict[str, str]],
    base_attr_lookup: Callable,
    *,
    field_index: dict[str, int] | None = None,
    context: GraduationAttrContext | None = None,
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
    field_index:
        Optional pre-computed field index map for resistance-aware vectors.
    context:
        Optional graduation context for resistance-aware vectors.

    Returns
    -------
    dict mapping slot_key to list of ``(equip, combat_attrs_delta, input_vector)``.
    If field_index and context are provided, input_vector is the raw contribution
    vector (resistance NOT yet applied — caller must call _apply_resistance_to_vec).
    """
    result: dict[str, list[tuple[dict, CombatAttributes, list[float]]]] = {}
    use_resistance = field_index is not None and context is not None

    for slot_key, equips in candidates.items():
        slot_data: list[tuple[dict, CombatAttributes, list[float]]] = []
        for equip in equips:
            single = {slot_key: equip}
            affix = aggregate_equipment_attrs(single)
            base = compute_equip_base_attrs(single, base_attr_lookup)
            delta = affix + base
            if use_resistance:
                # Use raw vector (resistance applied later in inner loop via _apply_resistance_to_vec)
                assert field_index is not None
                assert context is not None
                vec = _compute_equip_vec_raw(delta, input_specs, field_index, context)
            else:
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
# Generator — lazy enumeration, avoids materializing the full candidate space
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
    cancel_flag: Callable[[], bool] | None = None,
    full_chengyin: bool = False,
    full_dingyin: bool = False,
    full_level: int = 0,
    progress_counter: Any = None,  # 具有 evaluated, total, message 属性的对象
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
    cancel_flag:
        Callable returning True to abort search early.
    progress_counter:
        具有 evaluated, total, message 属性的对象，用于轮询进度。

    Returns
    -------
    List of up to 5 result dicts, each containing:
    ``rate``, ``dps``, ``total_damage``, ``equipped`` (per-slot equip dicts).
    """
    # 立即反馈，避免 UI 看起来卡住
    if progress_counter:
        total_items = sum(len(v) for v in candidates.values())
        progress_counter.message = f"准备中… 共 {total_items} 件候选装备"

    program = calculator._data["program"]
    input_specs = program["inputs"]
    baseline = calculator.baseline_dps()

    # -- Phase 0: apply hypothetical caps if requested --
    if full_chengyin or full_dingyin or full_level > 0:
        for slot_key in list(candidates):
            candidates[slot_key] = [
                e for _k, e in sorted(
                    apply_hypothetical_caps(
                        {i: e for i, e in enumerate(candidates[slot_key])},
                        full_chengyin, full_dingyin, full_level,
                    ).items()
                )
            ]

    # -- Phase 1: pre-compute deltas --
    slot_keys = [k for k in SLOT_KEYS if k in candidates and candidates[k]]
    graduation_context = GraduationAttrContext.from_school(calculator._school)
    field_index = _build_field_index_map(input_specs)

    slot_deltas = compute_slot_deltas(
        {k: candidates[k] for k in slot_keys},
        input_specs,
        # We need a base_attr_lookup — extract from calculator context
        _make_base_attr_lookup(),
        field_index=field_index,
        context=graduation_context,
    )

    # Compute base vector (raw, no resistance)
    base_vec = _compute_base_vec_raw(base_attrs, input_specs, field_index)

    # Filter out empty slots
    slot_keys = [k for k in slot_keys if slot_deltas.get(k)]
    if not slot_keys:
        return []

    total_combos = 1
    for k in slot_keys:
        total_combos *= len(slot_deltas[k])

    if progress_counter:
        progress_counter.total = total_combos
        progress_counter.message = f"搜索空间: {total_combos:,} 种组合"

    # -- Phase 1b: dominance pruning --
    if use_dominance_pruning:
        slot_deltas = prune_dominated(slot_deltas)
        pruned_combos = 1
        for k in slot_keys:
            pruned_combos *= max(len(slot_deltas.get(k, [])), 1)
        if progress_counter and pruned_combos < total_combos:
            progress_counter.total = pruned_combos
            progress_counter.message = f"支配剪枝: {total_combos:,} → {pruned_combos:,}"
        total_combos = pruned_combos

    # -- Phase 1c: Top-K safety net --
    if max_per_slot > 0:
        slot_deltas = _apply_top_k_safety(slot_deltas, max_per_slot)
        safe_combos = 1
        for k in slot_keys:
            safe_combos *= max(len(slot_deltas.get(k, [])), 1)
        if safe_combos < total_combos:
            if progress_counter:
                progress_counter.total = safe_combos
                progress_counter.message = f"Top-K 缩减: {total_combos:,} → {safe_combos:,}"
            total_combos = safe_combos

    # -- Prepare fast-lookup arrays for inner loop --
    active_slots = [k for k in slot_keys if slot_deltas.get(k)]
    slot_equip_arrays: list[list[dict]] = []
    slot_vec_arrays: list[list[list[float]]] = []  # Changed from CombatAttributes to vectors
    for k in active_slots:
        entries = slot_deltas[k]
        slot_equip_arrays.append([e[0] for e in entries])
        slot_vec_arrays.append([e[2] for e in entries])  # raw vectors, resistance applied in-loop
    slot_sizes = [len(v) for v in slot_vec_arrays]
    n_dims = len(input_specs)

    # -- Phase 2: enumerate + evaluate --
    board = TopRLeaderboard(top_r)
    evaluated = 0

    # Reusable accumulation buffer and runtime
    acc = [0.0] * n_dims
    runtime = ProgramRuntime(program, acc)
    output_nodes = program["outputs"]
    cache_clear = runtime.cache.clear

    # ✅ 向量结果缓存：tuple(属性向量) → (dps, total_damage)
    # 防止相同属性向量被重复计算毕业率
    vec_result_cache: dict[tuple, tuple[float, float]] = {}
    cache_hits = 0

    for combo_indices in _generate_combos(active_slots, slot_sizes):
        # Check cancel every iteration (threading.Event.is_set is fast)
        if cancel_flag and cancel_flag():
            break

        # 性能关键路径：纯数组累加 + 直接评估
        # 1. 从 base_vec 开始
        for d in range(n_dims):
            acc[d] = base_vec[d]
        # 2. 累加每件装备的原始贡献（抗性在第3步统一应用）
        for si, idx in enumerate(combo_indices):
            vec = slot_vec_arrays[si][idx]
            for d in range(n_dims):
                acc[d] += vec[d]
        # 3. 应用抗性规则（向量化等效 build_graduation_attrs）
        _apply_resistance_to_vec(acc, base_vec, field_index, graduation_context)

        # ✅ 检查向量缓存
        vec_tuple = tuple(acc)
        if vec_tuple in vec_result_cache:
            dps, total_damage = vec_result_cache[vec_tuple]
            cache_hits += 1
        else:
            # 缓存未命中，计算毕业率
            cache_clear()
            dps = float(runtime.value(output_nodes["dps"]))
            total_damage = float(runtime.value(output_nodes["total_damage"]))
            # 存储结果到缓存
            vec_result_cache[vec_tuple] = (dps, total_damage)

        rate = dps / baseline

        # Insert into leaderboard
        if len(board._entries) < top_r or rate > board.worst_rate:
            board.insert(rate, list(combo_indices), dps)

        evaluated += 1
        # 每完成一条就更新计数器，前台定时器会轮询读取
        if progress_counter:
            progress_counter.evaluated = evaluated

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

    # ✅ 输出缓存统计信息
    cache_hit_rate = f"{cache_hits*100//max(evaluated, 1)}%" if evaluated > 0 else "N/A"
    logger.info(
        f"最优组合搜索完成: 评估 {evaluated:,} 种组合, "
        f"向量缓存命中 {cache_hits:,} 次 ({cache_hit_rate}), "
        f"最佳毕业率 {results[0]['rate']:.2%}" if results else "无结果"
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
