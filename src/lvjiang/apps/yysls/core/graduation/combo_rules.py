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


#: 评级由低到高。索引即档位，比较大小就是比档位。
RATING_ORDER: tuple[str, ...] = ("垃圾", "一般", "优秀", "顶级")


def rating_rank(rating: str) -> int:
    """评级的档位序号；不认识的评级当垃圾。"""
    try:
        return RATING_ORDER.index(rating)
    except ValueError:
        return 0


@dataclass(frozen=True)
class MultiRuleVerdict:
    """一件装备在**一组**规则+玩法下的综合结论。

    ``rating`` 是各条规则给出的**最高**评级——一件装备通常只对得上其中
    一两套玩法，用最高档意味着「有一种练法用得上它就留着」。取最低档
    等于要求它同时适配所有玩法，那几乎筛不出装备，正是只能选单条规则时
    的老毛病。

    一条规则都没给出评级（全部 skipped / not_applicable）时按**垃圾**算，
    见 :attr:`label`。
    """

    rating: str = ""          # 最高评级；一条都没形成结论时为空
    matched: str = ""         # 给出最高评级的那条「规则名-玩法」
    conclusive: bool = False  # 是否有任一规则给出了评级
    skipped: bool = False     # 有规则明确跳过（品阶无价值 / 无词条数据）

    @property
    def label(self) -> str:
        """展示用文案。

        一条规则都没给出评级时显示「垃圾」，而不是「不适用」或「跳过」：
        ``skipped`` 的实际含义就是品阶无调律价值或没有词条数据——那正是
        垃圾胚子。照实叫「不适用」的话，一大片垃圾会顶着一个看不出好坏
        的标签留在候选里。
        """
        return self.rating if self.conclusive else "垃圾"

    def meets(self, minimum: str) -> bool:
        """是否达到要求档位。

        一条规则都没给出评级 = 垃圾，达不到任何要求档位（要求里不提供
        「垃圾」，因为那等于没有要求）。
        """
        return rating_rank(self.label) >= rating_rank(minimum)


def judge_best_rating(
    equip: dict, pairs: Sequence[tuple[str, str]],
) -> MultiRuleVerdict:
    """在多条「规则+玩法」下判定一件装备，取最高评级。

    ``pairs`` 为空时返回一个空结论（``conclusive=False``），调用方据此
    维持「不应用规则」的行为。
    """
    best = MultiRuleVerdict()
    skipped = False
    for rule_key, playstyle in pairs:
        if not rule_key or not playstyle:
            continue
        result = judge_tuning_candidate(equip, rule_key, playstyle)
        if result.not_applicable:
            continue
        if result.skipped:
            skipped = True
            continue
        if result.rating is None:
            continue
        value = result.rating.value
        if not best.conclusive or rating_rank(value) > rating_rank(best.rating):
            best = MultiRuleVerdict(
                rating=value, matched=f"{rule_key}-{playstyle}",
                conclusive=True)
    if best.conclusive:
        return best
    return MultiRuleVerdict(skipped=skipped)


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
