"""八件装备联合转律建议：逐件精确评估的局部贪心。

每件合格装备至多一条 (槽, 目标)。每一步对每件装备枚举全部合法替换、固定
其余七件（含它们已选的转律）重算整套毕业率，取全局收益最大的一件写入，
再重算下一步；复查轮次可以替换某件已选的那条，不能再加一条。收益必须同时
超过“不变”和“同名词条拉到同口径满值”两个对照，否则该槽的正确建议是喂满
数值而不是转律。

不用词条收益率（理论敏感度）做选择依据：它是单独扣一条/加一条的一阶量，
在三率封顶等拐点附近与真实替换差分不一致，而且并不比精确枚举便宜。
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field

from ..combat.combat_attrs import CombatAttributes
from ..loadout.models import EQUIPMENT_SLOTS
from ..loadout.transmute import (
    judge_transmute_eligibility,
    saved_transmute_target,
    transmute_candidates,
    transmute_pool_union,
    transmute_target_value,
    validate_saved_target,
    with_transmuted_affix,
)
from .assumptions import Assumptions
from .scoring import BudgetExceeded, LoadoutScorer
from .smart_search import floor_rate

#: 搜索预算：先到者停止。数值集中在这里，实现阶段按实测调整。
DEFAULT_TIME_BUDGET_SECONDS = 10.0
DEFAULT_PRECISION = 0.001
#: 复查轮数：每件定一次之后再多跑几轮，允许替换已选转律。
EXTRA_REVIEW_ROUNDS = 2


@dataclass(frozen=True)
class TransmuteMove:
    """一件装备的推荐转律。"""

    slot_key: str
    fp: str
    affix_index: int
    from_name: str
    from_value: float
    to_name: str
    to_value: float          # 按实际装备等级/承音状态保存的目标值
    marginal_gain: float     # 最终组合中撤销这一条的损失（执行顺序依据）
    swap_gain: float         # 相对“同名词条拉到同口径满值”的净收益


@dataclass(frozen=True)
class TransmuteSlotStatus:
    slot_key: str
    fp: str
    equipment_name: str
    eligible: bool
    reason: str = ""          # 不合格原因代码；合格且无推荐时为空
    move: TransmuteMove | None = None
    trusted: bool = True


@dataclass(frozen=True)
class TransmutePlanResult:
    baseline_rate: float
    saved_rate: float | None
    final_rate: float
    slots: tuple[TransmuteSlotStatus, ...]
    evaluated: int
    exhausted: bool           # False：预算耗尽，结果是当前搜索最好结果
    trusted: bool             # False：装备数据自相矛盾，禁止应用
    equipped_fps: frozenset[str]
    missing_slots: tuple[str, ...] = ()

    @property
    def gain(self) -> float:
        return self.final_rate - self.baseline_rate

    @property
    def moves(self) -> tuple[TransmuteMove, ...]:
        return tuple(
            status.move for status in self.slots if status.move is not None)

    @property
    def applicable(self) -> bool:
        return self.trusted and bool(self.moves)


@dataclass
class TransmuteSearchRequest:
    """冻结的计算上下文：流派、模型、基础属性（含弓玦）、三满设置。"""

    equipped: dict[str, dict]
    calculator: object
    base_attrs: CombatAttributes
    school: str
    game_config: object
    full_chengyin: bool = False
    full_dingyin: bool = False
    full_level: int = 0
    season_chengyin: bool = False
    playstyle: str = ""
    precision: float = DEFAULT_PRECISION
    time_budget: float = DEFAULT_TIME_BUDGET_SECONDS
    stop_check: Callable[[], bool] | None = None
    school_pool: tuple[str, ...] = field(default_factory=tuple)

    def assumptions(self, *, simulate_transmute: bool = False) -> Assumptions:
        return Assumptions(
            full_level=self.full_level,
            full_chengyin=self.full_chengyin,
            full_dingyin=self.full_dingyin,
            season_chengyin=self.season_chengyin,
            simulate_transmute=simulate_transmute,
            playstyle=self.playstyle,
        )


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _with_full_value(
    equip: dict, index: int, game_config,
) -> dict | None:
    """同名词条拉到该副本口径的满值（不标转律），作为换词条收益的对照。"""
    affix = equip.get(f"affix_{index}")
    if not isinstance(affix, dict) or not affix.get("name"):
        return None
    value = transmute_target_value(
        str(affix["name"]), _int(equip.get("level")),
        bool(equip.get("is_chengyin")), game_config)
    if value is None:
        return None
    changed = copy.deepcopy(equip)
    changed[f"affix_{index}"] = {**affix, "value": value}
    return changed


def optimize_transmutes(request: TransmuteSearchRequest) -> TransmutePlanResult:
    gc = request.game_config
    precision = request.precision or DEFAULT_PRECISION

    def improves(candidate: float, baseline: float) -> bool:
        return floor_rate(candidate, precision) > floor_rate(baseline, precision)

    original: dict[str, dict] = {
        slot: copy.deepcopy(equip)
        for slot, equip in request.equipped.items()
        if isinstance(equip, dict)
    }
    missing = tuple(slot for slot in EQUIPMENT_SLOTS if slot not in original)
    pool_union = transmute_pool_union(gc)
    pool_rank = {name: rank for rank, name in enumerate(request.school_pool)}

    # 三满投影一次；所有候选都在这份副本上替换。
    projected = copy.deepcopy(request.assumptions().project(original, gc))

    eligibility = {
        slot: judge_transmute_eligibility(equip, gc)
        for slot, equip in original.items()
    }
    candidates: dict[str, dict[int, list[str]]] = {}
    for slot, judged in eligibility.items():
        if judged.eligible:
            candidates[slot] = transmute_candidates(
                original[slot], gc, pool_union, slots=judged.slots)
    trusted = all(judged.trusted for judged in eligibility.values())

    evaluator = LoadoutScorer(
        request.calculator, request.base_attrs, request.school, gc,
        stop_check=request.stop_check, time_budget=request.time_budget)
    baseline_rate = evaluator.rate(projected)

    saved_rate: float | None = None
    if any(
        saved_transmute_target(equip) is not None
        and validate_saved_target(equip, gc) is None
        for equip in original.values()
    ):
        saved_projected = request.assumptions(
            simulate_transmute=True).project(original, gc)
        saved_rate = evaluator.rate(saved_projected)

    def apply_move(state: dict[str, dict], slot: str, index: int, name: str) -> dict:
        equip = projected[slot]
        value = transmute_target_value(
            name, _int(equip.get("level")), bool(equip.get("is_chengyin")), gc)
        changed = dict(state)
        changed[slot] = with_transmuted_affix(
            equip, index, name, value or 0.0, gc)
        return changed

    # 当前状态 = 投影副本 + 已选转律；chosen 记录每件的 (槽, 目标)。
    state: dict[str, dict] = dict(projected)
    chosen: dict[str, tuple[int, str]] = {}
    exhausted = True
    max_rounds = len(candidates) + EXTRA_REVIEW_ROUNDS
    try:
        for _round in range(max_rounds):
            current_rate = evaluator.rate(state)
            best_key: tuple | None = None
            best: tuple[str, tuple[int, str] | None, float] | None = None
            for slot, per_slot in candidates.items():
                base_state = dict(state)
                base_state[slot] = projected[slot]
                base_rate = evaluator.rate(base_state)
                if slot in chosen and improves(base_rate, current_rate):
                    # 其他件改动后，这件撤销反而更好：也是一种“改动更少”。
                    key = (floor_rate(base_rate, precision), 1, 0, 0, "")
                    if best_key is None or key > best_key:
                        best_key, best = key, (slot, None, base_rate)
                for index, names in per_slot.items():
                    reference_state = dict(base_state)
                    full = _with_full_value(projected[slot], index, gc)
                    reference_rate = base_rate
                    if full is not None:
                        reference_state[slot] = full
                        reference_rate = evaluator.rate(reference_state)
                    for name in names:
                        if chosen.get(slot) == (index, name):
                            continue
                        rate = evaluator.rate(apply_move(base_state, slot, index, name))
                        if not improves(rate, current_rate):
                            continue
                        if not improves(rate, reference_rate):
                            continue
                        key = (
                            floor_rate(rate, precision), 0,
                            -pool_rank.get(name, len(pool_rank)), -index, name,
                        )
                        if best_key is None or key > best_key:
                            best_key, best = key, (slot, (index, name), rate)
            if best is None:
                break
            slot, move, _rate = best
            if move is None:
                chosen.pop(slot, None)
                state[slot] = projected[slot]
            else:
                chosen[slot] = move
                state = apply_move(state, slot, move[0], move[1])
    except BudgetExceeded:
        exhausted = False

    final_rate = evaluator.rate(state)

    statuses: list[TransmuteSlotStatus] = []
    for slot in EQUIPMENT_SLOTS:
        equip = original.get(slot)
        if equip is None:
            continue
        fp = str(equip.get("_fp") or "")
        name = str(equip.get("name") or equip.get("type") or slot)
        judged = eligibility[slot]
        move_result: TransmuteMove | None = None
        picked = chosen.get(slot)
        if picked is not None:
            index, target = picked
            reverted = dict(state)
            reverted[slot] = projected[slot]
            try:
                marginal = final_rate - evaluator.rate(reverted)
                full = _with_full_value(projected[slot], index, gc)
                swap = marginal
                if full is not None:
                    reverted[slot] = full
                    swap = final_rate - evaluator.rate(reverted)
            except BudgetExceeded:
                marginal = swap = 0.0
                exhausted = False
            source = equip.get(f"affix_{index}") or {}
            move_result = TransmuteMove(
                slot_key=slot,
                fp=fp,
                affix_index=index,
                from_name=str(source.get("name") or ""),
                from_value=_number(source.get("value")),
                to_name=target,
                to_value=transmute_target_value(
                    target, _int(equip.get("level")),
                    bool(equip.get("is_chengyin")), gc) or 0.0,
                marginal_gain=marginal,
                swap_gain=swap,
            )
        statuses.append(TransmuteSlotStatus(
            slot_key=slot,
            fp=fp,
            equipment_name=name,
            eligible=judged.eligible,
            reason=judged.reason,
            move=move_result,
            trusted=judged.trusted,
        ))

    return TransmutePlanResult(
        baseline_rate=baseline_rate,
        saved_rate=saved_rate,
        final_rate=final_rate,
        slots=tuple(statuses),
        evaluated=evaluator.evaluated,
        exhausted=exhausted,
        trusted=trusted,
        equipped_fps=frozenset(
            str(equip.get("_fp") or "") for equip in original.values()),
        missing_slots=missing,
    )
