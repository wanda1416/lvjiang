"""可插拔的最优装备候选规则。

规则只处理系统内的标准装备/词条数据，不接受 Excel 别名。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class RuleStats:
    removed: dict[str, int] = field(default_factory=dict)

    def add(self, rule_id: str, count: int) -> None:
        if count:
            self.removed[rule_id] = self.removed.get(rule_id, 0) + count


@dataclass(frozen=True)
class CandidateRuleContext:
    school: str


class CandidateRule(Protocol):
    rule_id: str

    def apply(
        self, slot: str, candidates: list[dict], context: CandidateRuleContext,
    ) -> list[dict]: ...


def judge_tuning_candidate(equip: dict, rule_key: str, playstyle: str):
    """使用调律系统唯一判定入口返回装备的实际评级。"""
    from ..equip_parser import EquipmentData
    from ..evaluator import get_tuning_judge

    judge = get_tuning_judge(rule_key, {"playstyles": [playstyle]})
    return judge.judge(EquipmentData.from_dict(equip))


class TuningJunkRule:
    """复用调律系统的实际评级，排除被指定规则+玩法判为垃圾的装备。"""

    rule_id = "tuning_junk"

    def __init__(self, rule_key: str, playstyle: str) -> None:
        if not rule_key or not playstyle:
            raise ValueError("调律规则和玩法不能为空")
        self.rule_key = rule_key
        self.playstyle = playstyle

    def apply(self, slot, candidates, context):
        from ..equip_parser import EquipmentData
        from ..evaluator import Rating, get_tuning_judge

        judge = get_tuning_judge(
            self.rule_key, {"playstyles": [self.playstyle]})
        kept: list[dict] = []
        for equip in candidates:
            result = judge.judge(EquipmentData.from_dict(equip))
            # skipped / not_applicable 没有形成“垃圾”结论，不能当否决票。
            if (not result.skipped and not result.not_applicable
                    and result.rating is Rating.JUNK):
                continue
            kept.append(equip)
        return kept


def apply_candidate_rules(
    candidates: dict[str, list[dict]],
    rules: Sequence[CandidateRule],
    context: CandidateRuleContext,
) -> tuple[dict[str, list[dict]], RuleStats]:
    result: dict[str, list[dict]] = {}
    stats = RuleStats()
    for slot, entries in candidates.items():
        current = list(entries)
        for rule in rules:
            before = len(current)
            current = rule.apply(slot, current, context)
            stats.add(rule.rule_id, before - len(current))
        result[slot] = current
    return result, stats
