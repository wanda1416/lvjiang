"""鸣金·虹流派装备评估器

评分规则（参考 docs/10-game/tuning-mechanics.md）：

1. 品阶检查
   - 武器/首饰：非金色 → 无调律价值
   - 防具：紫色及以上才有调律价值

2. 首词条检查（初始词条，不可更改）
   - 武器（主武器/副武器）：最大外功攻击（首选）或 势（次选）
   - 首饰（环/佩）：最大外功攻击
   - 冠胄/胸甲：会意率
   - 胫甲/腕甲：劲

3. 装备特殊要求
   - 剑：必须有 剑武学增伤
   - 枪：不能有 枪武学增伤
   - 首饰：必须有 全武学增效
   - 冠胄/胸甲：有 单体奇术增伤 视为有效（PVP）
   - 胫甲/腕甲：必须有 对首领单位增伤，对玩家单位增效 视为有效（PVP）

4. 有效词条集合（首词条之外）
   - 五维：劲、势
   - 攻击：最大外功攻击、最大无相攻击、最大鸣金攻击
   - 三率：会意率、会心率、精准率
   - 势 ≈ 0.5 最大外功攻击 + 0.5 会意率

5. 扣分规则（仅计非首词条）
   - 势 + 会意率 同时出现 → 扣 1 分
   - 会心率 或 精准率 → 各扣 1 分，≥ 2 条直接不合格
   - 最大无相攻击 或 最大鸣金攻击 → 各扣 1 分

6. 评级
   - 传家宝：0 扣分
   - 合格装备：≤ 1 扣分
   - 凑合装备：≤ 2 扣分
   - 垃圾装备：> 2 扣分 或不合格
"""

from .base import BaseEvaluator, EvaluationResult, Rating, TuningAdvice
from src.apps.yysls.equip_parser import EquipmentData, Affix


# 鸣金虹有效词条
# 神力词条（由装备要求决定有效性，此处列入有效集合）
_DIVINE_AFFIXES = {
    "剑武学增伤", "枪武学增伤",
    "全武学增效",
    "单体奇术增伤", "群体奇术增伤",
    "对首领单位增伤", "对玩家单位增效",
}

# 非神力有效词条
_VALID_AFFIXES = {
    "劲", "势",
    "最大外功攻击", "最大无相攻击", "最大鸣金攻击",
    "会意率", "会心率", "精准率",
} | _DIVINE_AFFIXES

# 鸣金虹首词条可能性（按装备类型）
# 首词条由装备类型决定，不在列表中的词条视为 OCR 异常
_FIRST_AFFIX_POSSIBLE = {
    "剑": ["最大外功攻击", "势"],
    "枪": ["最大外功攻击", "势"],
    "扇": ["最大外功攻击", "势"],
    "伞": ["最大外功攻击", "势"],
    "陌刀": ["最大外功攻击", "势"],
    "舞绫鼓": ["最大外功攻击", "势"],
    "双刀": ["最大外功攻击", "势"],
    "绳镖": ["最大外功攻击", "势"],
    "横刀": ["最大外功攻击", "势"],
    "手甲": ["最大外功攻击", "势"],
    "环":        ["最大外功攻击"],
    "佩":        ["最大外功攻击"],
    "冠胄":      ["会意率"],
    "胸甲":      ["会意率"],
    "胫甲":      ["劲"],
    "腕甲":      ["劲"],
}

# 首词条偏好（用于调律选择，是 possible 的子集）
_FIRST_AFFIX_PREFERRED = {
    "剑": ["最大外功攻击", "势"],
    "枪": ["最大外功攻击", "势"],
    "扇": ["最大外功攻击", "势"],
    "伞": ["最大外功攻击", "势"],
    "陌刀": ["最大外功攻击", "势"],
    "舞绫鼓": ["最大外功攻击", "势"],
    "双刀": ["最大外功攻击", "势"],
    "绳镖": ["最大外功攻击", "势"],
    "横刀": ["最大外功攻击", "势"],
    "手甲": ["最大外功攻击", "势"],
    "环":        ["最大外功攻击"],
    "佩":        ["最大外功攻击"],
    "冠胄":      ["会意率"],
    "胸甲":      ["会意率"],
    "胫甲":      ["劲"],
    "腕甲":      ["劲"],
}


class MingHongEvaluator(BaseEvaluator):
    """鸣金·虹流派装备评估器"""

    def evaluate(self, equip: EquipmentData) -> EvaluationResult:
        result = EvaluationResult(equipment=equip)

        # ── 1. 品阶检查 ──
        if not self._check_quality(equip, result):
            return result

        # ── 2. 首词条检查 ──
        if not self._check_first_affix(equip, result):
            return result

        # ── 3. 装备特殊要求（神力词条） ──
        if not self._check_special_requirements(equip, result):
            return result

        # ── 4. 有效词条 + 扣分 ──
        self._score_affixes(equip, result)

        # ── 5. 评级 ──
        if result.disqualified:
            result.rating = Rating.JUNK
        elif result.deductions == 0:
            result.rating = Rating.HEIRLOOM
        elif result.deductions <= 1:
            result.rating = Rating.QUALIFIED
        elif result.deductions <= 2:
            result.rating = Rating.MARGINAL
        else:
            result.rating = Rating.JUNK

        return result

    # ─── 调律熔断检查 ──────────────────────────────────────

    def check_tuning_worthiness(
        self, equip: EquipmentData
    ) -> TuningAdvice:
        """调律过程中实时判断是否值得继续

        传入数据可能缺失后续字段（尚未转律的槽位为空）。
        熔断规则：
        - 首词条不对 → 立即停止
        - 出现 2 个不合格词条 → 停止
        - 扣分已经 > 2 → 停止（扣分=2 仍可通过转律救回一条）
        """
        advice = TuningAdvice(equipment=equip)

        # ── 首词条检查（初始词条，不可更改） ──
        if equip.affixes:
            first = equip.affixes[0]
            possible = _FIRST_AFFIX_POSSIBLE.get(equip.type, [])
            if first.name not in possible:
                advice.should_continue = False
                advice.reasons.append(
                    f"首词条异常: {equip.type} 不可能出现 "
                    f"{first.name}（可能为 {'/'.join(possible)}）"
                )
                return advice
        else:
            # 无词条数据，无法判断
            advice.reasons.append("无词条数据")
            return advice

        # ── 非首词条检查（已转律的部分） ──
        non_first = equip.affixes[1:]
        non_first_names = [af.name for af in non_first]

        # 统计不合格词条
        invalid_names = [
            af.name for af in non_first
            if af.name not in _VALID_AFFIXES
        ]
        advice.invalid_count = len(invalid_names)
        if invalid_names:
            advice.reasons.append(
                f"不合格词条 × {len(invalid_names)}: "
                f"{', '.join(invalid_names)}"
            )

        # 计算当前扣分
        advice.current_deductions = self._calc_deductions(non_first_names)
        if advice.current_deductions > 0:
            advice.reasons.append(
                f"当前扣分: {advice.current_deductions}"
            )

        # ── 熔断判断 ──
        if advice.invalid_count >= 2:
            advice.should_continue = False
            advice.reasons.append("熔断: 不合格词条 ≥ 2")
        elif advice.current_deductions > 2:
            advice.should_continue = False
            advice.reasons.append("熔断: 扣分 > 2")

        return advice

    def _calc_deductions(self, non_first_names: list[str]) -> int:
        """计算非首词条的扣分（与 _score_affixes 相同的规则）"""
        deductions = 0

        # 势 + 会意率 扣分：同时出现时，扣分 = 势的条数
        shi_count = non_first_names.count("势")
        if shi_count > 0 and "会意率" in non_first_names:
            deductions += shi_count

        # 会心率 或 精准率 → 各扣 1 分
        bad_rate = sum(
            1 for n in non_first_names
            if n in ("会心率", "精准率")
        )
        deductions += bad_rate

        # 最大无相攻击 或 最大鸣金攻击 → 各扣 1 分
        bad_atk = sum(
            1 for n in non_first_names
            if n in ("最大无相攻击", "最大鸣金攻击")
        )
        deductions += bad_atk

        return deductions

    # ─── 品阶检查 ──────────────────────────────────────────

    def _check_quality(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> bool:
        """品阶检查：武器/首饰需金色，防具需紫色及以上

        注：当前 OCR 暂无法识别品阶，quality 为 None 时视为通过。
        """
        quality = equip.quality

        if quality is None:
            result.details.append("品阶未知，暂按通过处理")
            return True

        if equip.category in ("weapon", "jewelry"):
            if quality != "gold":
                result.disqualified = True
                result.disqualify_reasons.append(
                    f"武器/首饰需金色，当前为{quality}"
                )
                return False
        elif equip.category == "armor":
            if quality not in ("gold", "purple"):
                result.disqualified = True
                result.disqualify_reasons.append(
                    f"防具需紫色及以上，当前为{quality}"
                )
                return False

        return True

    # ─── 首词条检查 ──────────────────────────────────────────

    def _check_first_affix(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> bool:
        """检查初始词条是否符合流派偏好

        首词条由装备部位决定，不在可能列表中的词条视为 OCR 异常。
        """
        if not equip.affixes:
            result.disqualified = True
            result.disqualify_reasons.append("无词条数据")
            return False

        first = equip.affixes[0]
        possible = _FIRST_AFFIX_POSSIBLE.get(equip.type, [])

        if first.name not in possible:
            result.disqualified = True
            result.disqualify_reasons.append(
                f"首词条异常: {equip.type} 不可能出现 "
                f"{first.name}（可能为 {'/'.join(possible)}）"
            )
            return False

        result.details.append(f"首词条 ✓ {first.name}")
        return True

    # ─── 装备特殊要求 ──────────────────────────────────────

    def _check_special_requirements(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> bool:
        """检查装备特殊要求（神力词条等）"""
        affix_names = {af.name for af in equip.affixes}

        # 剑：必须有 剑武学增伤
        if equip.type == "剑":
            if "剑武学增伤" not in affix_names:
                result.disqualified = True
                result.disqualify_reasons.append("剑必须有剑武学增伤")
                return False
            result.details.append("神力 ✓ 剑武学增伤")

        # 枪：不能有 枪武学增伤
        if equip.type == "枪":
            if "枪武学增伤" in affix_names:
                result.disqualified = True
                result.disqualify_reasons.append("枪不能有枪武学增伤")
                return False
            result.details.append("神力 ✓ 无枪武学增伤")

        # 首饰：必须有 全武学增效
        if equip.category == "jewelry":
            if "全武学增效" not in affix_names:
                result.disqualified = True
                result.disqualify_reasons.append("首饰必须有全武学增效")
                return False
            result.details.append("神力 ✓ 全武学增效")

        # 冠胄/胸甲：不需要神力，有单体奇术增伤视为有效（PVP）
        if equip.type in ("冠胄", "胸甲"):
            if "单体奇术增伤" in affix_names:
                result.details.append("神力 △ 单体奇术增伤（PVP 可用）")

        # 胫甲/腕甲：必须有 对首领单位增伤，对玩家单位增效视为有效
        if equip.type in ("胫甲", "腕甲"):
            has_boss = "对首领单位增伤" in affix_names
            has_pvp = "对玩家单位增效" in affix_names
            if not has_boss and not has_pvp:
                result.disqualified = True
                result.disqualify_reasons.append(
                    "胫甲/腕甲必须有对首领单位增伤"
                    "（或对玩家单位增效 PVP）"
                )
                return False
            if has_boss:
                result.details.append("神力 ✓ 对首领单位增伤")
            elif has_pvp:
                result.details.append("神力 △ 对玩家单位增效（PVP 可用）")

        return True

    # ─── 有效词条 + 扣分 ──────────────────────────────────

    def _score_affixes(
        self, equip: EquipmentData, result: EvaluationResult
    ) -> None:
        """检查非首词条的有效性并计算扣分"""
        non_first = equip.affixes[1:]  # 跳过首词条
        non_first_names = [af.name for af in non_first]

        # ── 有效词条检查 ──
        for af in non_first:
            if af.name not in _VALID_AFFIXES:
                result.disqualified = True
                result.disqualify_reasons.append(
                    f"无效词条: {af.name}"
                )
                return

        result.details.append(
            f"有效词条 ✓ {len(non_first)}/{len(non_first)}"
        )

        # ── 扣分计算 ──
        deductions = 0

        # 势 + 会意率 扣分：同时出现时，扣分 = 势的条数
        shi_count = non_first_names.count("势")
        has_huiyi = "会意率" in non_first_names
        if shi_count > 0 and has_huiyi:
            deductions += shi_count
            result.details.append(
                f"扣分 -{shi_count}: 势 × {shi_count} + 会意率"
            )

        # 会心率 或 精准率 → 各扣 1 分，≥ 2 条直接不合格
        bad_rate_count = sum(
            1 for name in non_first_names
            if name in ("会心率", "精准率")
        )
        if bad_rate_count > 0:
            deductions += bad_rate_count
            result.details.append(
                f"扣分 -{bad_rate_count}: "
                f"会心率/精准率 × {bad_rate_count}"
            )
        if bad_rate_count >= 2:
            result.disqualified = True
            result.disqualify_reasons.append(
                f"会心率/精准率 ≥ 2 条（{bad_rate_count} 条）"
            )
            result.deductions = deductions
            return

        # 最大无相攻击 或 最大鸣金攻击 → 各扣 1 分
        bad_atk_count = sum(
            1 for name in non_first_names
            if name in ("最大无相攻击", "最大鸣金攻击")
        )
        if bad_atk_count > 0:
            deductions += bad_atk_count
            result.details.append(
                f"扣分 -{bad_atk_count}: "
                f"无相/鸣金攻击 × {bad_atk_count}"
            )

        result.deductions = deductions
