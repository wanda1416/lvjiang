"""通用装备评估引擎

由 RuleConfig 驱动，全流派共用。
支持 evaluate()（评级）和 check_tuning_worthiness()（Mock 最差重评级熔断）。
"""

from __future__ import annotations

import copy
from dataclasses import replace

from .base import BaseEvaluator, EvaluationResult, Rating, TuningAdvice
from .rule_config import RuleConfig, DeductionRule, DivineAffixRule
from src.apps.yysls.equip_parser import EquipmentData, Affix


# 属性攻击词条集合（用于 max_attribute_attack 约束）
_ATTRIBUTE_ATTACKS = {"最大无相攻击", "最大鸣金攻击"}


class GenericEvaluator(BaseEvaluator):
    """通用规则引擎评估器

    通过 RuleConfig 配置驱动，无需为每个流派编写子类。
    """

    def __init__(self, config: RuleConfig):
        self.config = config

    # ─── 主评估流程 ──────────────────────────────────────

    def evaluate(self, equip: EquipmentData, *, mock: bool = False) -> EvaluationResult:
        """评估装备（支持 1-N 条词条）

        Args:
            mock: True 时跳过品阶和神力检查，仅评估词条组合扣分
        """
        result = EvaluationResult(equipment=equip)

        if not mock:
            # 1. 品阶检查
            if not self._check_quality(equip, result):
                return result

        # 2. 首词条检查
        if not self._check_first_affix(equip, result):
            return result

        if not mock:
            # 3. 神力/特殊要求
            if not self._check_divine_affixes(equip, result):
                return result

        # 4. 有效词条 + 扣分
        self._score_affixes(equip, result)

        # 5. 评级
        if result.disqualified:
            result.rating = Rating.JUNK
        else:
            result.rating = self._to_rating(result.deductions)

        return result

    def check_tuning_worthiness(
        self, equip: EquipmentData
    ) -> TuningAdvice:
        """调律熔断：Mock 最差词条为最佳后重评级"""
        advice = TuningAdvice(equipment=equip)

        # ── 首词条检查 ──
        if not equip.affixes:
            advice.reasons.append("无词条数据")
            return advice

        first = equip.affixes[0]
        possible = self.config.get_first_affix(equip.type)
        if first.name not in possible:
            advice.should_continue = False
            advice.reasons.append(
                f"首词条异常: {equip.type} 不可能出现 "
                f"{first.name}（可能为 {'/'.join(possible)}）"
            )
            return advice

        # 只有首词条，无后续 → 继续
        if len(equip.affixes) <= 1:
            return advice

        # ── Mock 最差重评级 ──
        non_first = equip.affixes[1:]
        non_first_names = [af.name for af in non_first]

        # 当前实际评分
        current_result = self.evaluate(equip)
        advice.current_deductions = current_result.deductions
        advice.invalid_count = sum(
            1 for n in non_first_names if n not in self.config.valid_affix_set
        )

        if advice.invalid_count:
            invalid_names = [
                n for n in non_first_names
                if n not in self.config.valid_affix_set
            ]
            advice.reasons.append(
                f"不合格词条 × {len(invalid_names)}: {', '.join(invalid_names)}"
            )
        if advice.current_deductions > 0:
            advice.reasons.append(f"当前扣分: {advice.current_deductions}")

        # 逐一试移每条非首词条，找「移除后评分最高」的 → 最差词条
        best_score = -1
        worst_idx = -1
        for i in range(len(non_first)):
            remaining = non_first[:i] + non_first[i + 1:]
            mock_equip = self._make_mock_equip(equip, [first] + remaining)
            r = self.evaluate(mock_equip, mock=True)
            score = self._rating_score(r)
            if score > best_score:
                best_score = score
                worst_idx = i

        # 用最佳替换词条替换最差
        remaining_after_remove = [
            non_first[j]
            for j in range(len(non_first))
            if j != worst_idx
        ]
        remaining_names = [af.name for af in remaining_after_remove]
        best_replacement = self._get_best_replacement(
            equip, remaining_names
        )

        # 构造 mock 集合：移除最差 + 补最佳
        mock_affixes = [first] + remaining_after_remove + [
            Affix(name=best_replacement, value=0)
        ]
        mock_equip = self._make_mock_equip(equip, mock_affixes)
        mock_result = self.evaluate(mock_equip, mock=True)

        if mock_result.rating == Rating.JUNK:
            advice.should_continue = False
            advice.reasons.append(
                f"熔断: 最佳替换({best_replacement})后仍为垃圾装备"
            )

        return advice

    # ─── 品阶检查 ──────────────────────────────────────────

    def _check_quality(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> bool:
        quality = equip.quality
        if quality is None:
            result.details.append("品阶未知，暂按通过处理")
            return True

        q_cfg = self.config.quality
        if equip.category in ("weapon", "jewelry"):
            required = q_cfg.get("weapon_jewelry", "gold")
            if isinstance(required, str):
                required = [required]
            if quality not in required:
                result.disqualified = True
                result.disqualify_reasons.append(
                    f"武器/首饰需{'/'.join(required)}，当前为{quality}"
                )
                return False
        elif equip.category == "armor":
            required = q_cfg.get("armor", ["gold", "purple"])
            if isinstance(required, str):
                required = [required]
            if quality not in required:
                result.disqualified = True
                result.disqualify_reasons.append(
                    f"防具需{'/'.join(required)}，当前为{quality}"
                )
                return False
        return True

    # ─── 首词条检查 ──────────────────────────────────────────

    def _check_first_affix(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> bool:
        if not equip.affixes:
            result.disqualified = True
            result.disqualify_reasons.append("无词条数据")
            return False

        first = equip.affixes[0]
        possible = self.config.get_first_affix(equip.type)

        if first.name not in possible:
            result.disqualified = True
            result.disqualify_reasons.append(
                f"首词条异常: {equip.type} 不可能出现 "
                f"{first.name}（可能为 {'/'.join(possible)}）"
            )
            return False

        result.details.append(f"首词条 ✓ {first.name}")
        return True

    # ─── 神力/特殊要求 ──────────────────────────────────────

    def _check_divine_affixes(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> bool:
        affix_names = {af.name for af in equip.affixes}

        for rule in self.config.divine_affixes:
            if not self._match_divine_rule(rule, equip):
                continue

            has_any = any(a in affix_names for a in rule.affixes)
            has_alt = any(a in affix_names for a in rule.alt) if rule.alt else False

            if rule.required:
                if not has_any and not has_alt:
                    alt_text = ""
                    if rule.alt:
                        alt_text = f"（或 {'/'.join(rule.alt)} PVP）"
                    result.disqualified = True
                    result.disqualify_reasons.append(
                        f"{'/'.join(rule.affixes)} 必须存在{alt_text}"
                    )
                    return False
                if has_any:
                    result.details.append(
                        f"神力 ✓ {'/'.join(rule.affixes)}"
                    )
                elif has_alt:
                    result.details.append(
                        f"神力 △ {'/'.join(rule.alt)}（PVP 可用）"
                    )
            else:
                # required=False: 有则不合格
                if has_any:
                    result.disqualified = True
                    result.disqualify_reasons.append(
                        f"不能有 {'/'.join(rule.affixes)}"
                    )
                    return False
                result.details.append(
                    f"神力 ✓ 无{'/'.join(rule.affixes)}"
                )

        return True

    def _match_divine_rule(
        self, rule: DivineAffixRule, equip: EquipmentData
    ) -> bool:
        """检查装备是否匹配神力规则"""
        if rule.match_type and equip.type == rule.match_type:
            return True
        if rule.match_slot and equip.type in rule.match_slot:
            return True
        return False

    # ─── 有效词条 + 扣分 ──────────────────────────────────

    def _score_affixes(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> None:
        non_first = equip.affixes[1:]
        non_first_names = [af.name for af in non_first]
        valid_set = self.config.valid_affix_set

        # 有效词条检查
        for af in non_first:
            if af.name not in valid_set:
                result.disqualified = True
                result.disqualify_reasons.append(f"无效词条: {af.name}")
                return

        if non_first:
            result.details.append(
                f"有效词条 ✓ {len(non_first)}/{len(non_first)}"
            )

        # 扣分计算
        deductions = self._calc_deductions(non_first_names)
        result.deductions = deductions

        # 记录扣分详情
        for rule in self.config.deduction_rules:
            detail = self._deduction_detail(rule, non_first_names)
            if detail:
                result.details.append(detail)

    def _calc_deductions(self, non_first_names: list[str]) -> int:
        """计算非首词条的总扣分"""
        deductions = 0
        for rule in self.config.deduction_rules:
            deductions += self._apply_rule(rule, non_first_names)
        return deductions

    def _apply_rule(
        self, rule: DeductionRule, names: list[str]
    ) -> int:
        """应用单条扣分规则，返回扣分值"""
        if rule.type == "existence":
            return sum(
                rule.deduction for n in names if n in rule.affixes
            )

        elif rule.type == "invalid_count":
            count = sum(1 for n in names if n in rule.affixes)
            return count * rule.deduction

        elif rule.type == "combo_count":
            all_present = all(a in names for a in rule.affixes)
            if all_present and rule.count_affix:
                return names.count(rule.count_affix)
            return 0

        return 0

    def _deduction_detail(
        self, rule: DeductionRule, names: list[str]
    ) -> str | None:
        """生成扣分详情文本"""
        if rule.type == "existence":
            count = sum(1 for n in names if n in rule.affixes)
            if count > 0:
                return f"扣分 -{count * rule.deduction}: {rule.description}"

        elif rule.type == "invalid_count":
            count = sum(1 for n in names if n in rule.affixes)
            if count > 0:
                return f"扣分 -{count * rule.deduction}: {rule.description}"

        elif rule.type == "combo_count":
            all_present = all(a in names for a in rule.affixes)
            if all_present and rule.count_affix:
                c = names.count(rule.count_affix)
                return f"扣分 -{c}: {rule.description}"

        return None

    # ─── Mock 辅助 ──────────────────────────────────────────

    def _get_best_replacement(
        self,
        equip: EquipmentData,
        remaining_non_first_names: list[str],
    ) -> str:
        """从当前可用转律词库中选最优替换词条"""
        is_weapon = equip.category == "weapon"
        pool = self.config.get_tuning_pool(is_weapon)
        constraints = self.config.tuning_constraints

        # 排除已有非首词条（不重复约束）
        if constraints.no_duplicate:
            pool = [a for a in pool if a not in remaining_non_first_names]

        # 属性攻击上限约束
        atk_count = sum(
            1 for n in remaining_non_first_names if n in _ATTRIBUTE_ATTACKS
        )
        if atk_count >= constraints.max_attribute_attack:
            pool = [a for a in pool if a not in _ATTRIBUTE_ATTACKS]

        if not pool:
            # 兜底：返回优先级最高的有效词条
            return self.config.priority[-1] if self.config.priority else "最大外功攻击"

        # 从可用中选优先级最高的
        rank = self.config.priority_rank
        return max(pool, key=lambda a: rank.get(a, -1))

    def _make_mock_equip(
        self, equip: EquipmentData, affixes: list[Affix]
    ) -> EquipmentData:
        """构造 mock 装备（替换词条列表，其余不变）"""
        return EquipmentData(
            type=equip.type,
            name=equip.name,
            level=equip.level,
            quality=equip.quality,
            is_chengyin=equip.is_chengyin,
            base_attr=equip.base_attr,
            base_attr_2=equip.base_attr_2,
            affixes=affixes,
        )

    def _rating_score(self, result: EvaluationResult) -> int:
        """将评级转为可比较的分数（越高越好）"""
        if result.disqualified:
            return -1
        order = {
            Rating.HEIRLOOM: 3,
            Rating.QUALIFIED: 2,
            Rating.MARGINAL: 1,
            Rating.JUNK: 0,
        }
        return order.get(result.rating, 0)

    def _to_rating(self, deductions: int) -> Rating:
        """扣分 → 评级"""
        cfg = self.config.rating
        if deductions <= cfg.get("heirloom", 0):
            return Rating.HEIRLOOM
        if deductions <= cfg.get("qualified", 1):
            return Rating.QUALIFIED
        if deductions <= cfg.get("marginal", 2):
            return Rating.MARGINAL
        return Rating.JUNK
