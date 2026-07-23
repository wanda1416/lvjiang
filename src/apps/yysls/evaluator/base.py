"""装备评估公共基类

定义评估结果数据结构和评估器接口。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from src.apps.yysls.equip_parser import EquipmentData


# ─── 评级枚举 ──────────────────────────────────────────────

class Rating(Enum):
    """装备评级（扣分制）"""
    HEIRLOOM = "传家宝"       # 0 扣分
    QUALIFIED = "合格装备"    # ≤ 1 扣分
    MARGINAL = "凑合装备"     # ≤ 2 扣分
    JUNK = "垃圾装备"         # > 2 扣分 或不合格


@dataclass
class EvaluationResult:
    """单件装备评估结果"""
    equipment: EquipmentData
    rating: Rating = Rating.JUNK
    deductions: int = 0
    disqualified: bool = False
    disqualify_reasons: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.equipment.name,
            "type": self.equipment.type,
            "level": self.equipment.level,
            "is_chengyin": self.equipment.is_chengyin,
            "rating": self.rating.value,
            "deductions": self.deductions,
            "disqualified": self.disqualified,
        }
        if self.disqualify_reasons:
            d["disqualify_reasons"] = self.disqualify_reasons
        if self.details:
            d["details"] = self.details
        return d


# ─── 调律建议 ──────────────────────────────────────────────

@dataclass
class TuningAdvice:
    """调律过程中的实时判断建议

    用于调律过程中，根据当前已出现的词条判断是否值得继续调律。
    输入数据可能缺失后续字段（尚未转律的槽位为空）。
    """
    equipment: EquipmentData
    should_continue: bool = True     # 是否值得继续调律
    current_deductions: int = 0      # 当前扣分数
    invalid_count: int = 0           # 当前不合格词条数（非首词条中）
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {
            "name": self.equipment.name,
            "type": self.equipment.type,
            "should_continue": self.should_continue,
            "current_deductions": self.current_deductions,
            "invalid_count": self.invalid_count,
        }
        if self.reasons:
            d["reasons"] = self.reasons
        return d


# ─── 评估器基类 ────────────────────────────────────────────

class BaseEvaluator(ABC):
    """装备评估器公共基类

    子类需实现:
    - evaluate: 对完整装备进行最终评分/评级
    - check_tuning_worthiness: 调律过程中实时判断是否值得继续
    """

    @abstractmethod
    def evaluate(self, equip: EquipmentData) -> EvaluationResult:
        """评估单件装备（最终评级）

        Args:
            equip: 已解析的标准装备数据

        Returns:
            EvaluationResult
        """
        ...

    @abstractmethod
    def check_tuning_worthiness(
        self, equip: EquipmentData
    ) -> TuningAdvice:
        """调律过程中实时判断是否值得继续

        传入的装备数据可能缺失后续字段（尚未转律的槽位为空）。
        基于当前已出现的词条，判断是否值得继续调律。

        熔断规则（参考 tuning-mechanics.md）:
        - 第 2~5 条词条可以转律
        - 出现 2 个不合格词条 → 停止
        - 扣分已经 ≥ 2 → 停止

        Args:
            equip: 可能缺失后续字段的装备数据

        Returns:
            TuningAdvice
        """
        ...

    def evaluate_all(
        self, equips: dict[str, EquipmentData]
    ) -> dict[str, EvaluationResult]:
        """批量评估所有部位"""
        return {
            slot: self.evaluate(equip)
            for slot, equip in equips.items()
        }
