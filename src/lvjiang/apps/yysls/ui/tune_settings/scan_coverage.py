"""扫描处理规则在部位 × 金/紫品阶上的静态覆盖检查。"""

from __future__ import annotations

from dataclasses import dataclass

from lvjiang.apps.yysls.core.tuning_rules import QUALITY_PARTS, BehaviorRule


@dataclass(frozen=True)
class ScanCoverage:
    part: str
    quality: str
    rule_numbers: tuple[int, ...]


def scan_quality_coverage(rules: list[BehaviorRule]) -> list[ScanCoverage]:
    """列出每个部位/品阶可能适用的启用规则，保留原规则序号。

    判定结果与首词条数值没有实际装备就无法确定；只检查规则在
    这两个维度上是否有非空交集，不把候选规则等同实际命中。
    用 BehaviorRule.matches 复用规则本身的部位/品阶语义，并给
    其余条件传入该规则允许的一个值，避免维护第二套品阶映射。
    """
    result: list[ScanCoverage] = []
    for part in QUALITY_PARTS:
        for quality in ("gold", "purple"):
            matching = []
            for number, rule in enumerate(rules, 1):
                if not rule.enabled:
                    continue
                rating = (rule.ratings[0] if rule.ratings
                          and rule.judge_scope != "affix" else None)
                affixes = rule.ratings if rule.judge_scope == "affix" else None
                if rule.matches(part, quality, float(rule.pct),
                                rating, affixes):
                    matching.append(number)
            result.append(ScanCoverage(part, quality, tuple(matching)))
    return result
