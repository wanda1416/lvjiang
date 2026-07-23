"""流派规则配置

从 YAML 文件加载流派评估规则，驱动通用评估引擎。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ─── 扣分规则数据类 ────────────────────────────────────────

@dataclass
class DeductionRule:
    """单条扣分规则"""
    type: str                          # existence | invalid_count | combo_count
    affixes: list[str]                 # 涉及的词条列表
    deduction: int = 1                 # 每条扣分（existence / invalid_count）
    disqualify_at: int | None = None   # 达到 N 条直接不合格（invalid_count）
    count_affix: str | None = None     # 计数的词条（combo_count）
    description: str = ""


# ─── 神力/特殊要求 ─────────────────────────────────────────

@dataclass
class DivineAffixRule:
    """神力词条规则"""
    match_type: str | None = None      # 匹配装备类型（如 "剑"）
    match_slot: list[str] = field(default_factory=list)  # 匹配部位
    affixes: list[str] = field(default_factory=list)
    required: bool = True              # True=必须有, False=有则不合格
    alt: list[str] = field(default_factory=list)  # 备选词条（PVP）


# ─── 转律约束 ──────────────────────────────────────────────

@dataclass
class TuningConstraints:
    """转律机制约束"""
    no_duplicate: bool = True
    max_attribute_attack: int = 2


# ─── 规则配置主类 ──────────────────────────────────────────

@dataclass
class RuleConfig:
    """流派规则配置"""

    name: str
    description: str = ""

    # 有效词条
    valid_affixes: list[str] = field(default_factory=list)

    # 转律词库
    tuning_pool: dict[str, list[str]] = field(default_factory=dict)
    tuning_constraints: TuningConstraints = field(default_factory=TuningConstraints)

    # 优先级（从最差到最好）
    priority: list[str] = field(default_factory=list)

    # 首词条（按部位）
    first_affix: dict[str, list[str]] = field(default_factory=dict)

    # 品阶要求
    quality: dict = field(default_factory=dict)

    # 神力/特殊要求
    divine_affixes: list[DivineAffixRule] = field(default_factory=list)

    # 扣分规则
    deduction_rules: list[DeductionRule] = field(default_factory=list)

    # 评级阈值
    rating: dict[str, int] = field(default_factory=dict)

    # 调律熔断阈值
    tuning: dict = field(default_factory=dict)

    # ── 便捷方法 ──

    @property
    def valid_affix_set(self) -> set[str]:
        """有效词条集合（含神力词条）"""
        s = set(self.valid_affixes)
        for rule in self.divine_affixes:
            s.update(rule.affixes)
            s.update(rule.alt)
        return s

    @property
    def priority_rank(self) -> dict[str, int]:
        """优先级排名（词条 → 排名，越高越好）"""
        return {name: i for i, name in enumerate(self.priority)}

    def get_tuning_pool(self, is_weapon: bool) -> list[str]:
        """获取转律词库"""
        key = "weapon" if is_weapon else "non_weapon"
        return self.tuning_pool.get(key, [])

    def get_first_affix(self, slot: str) -> list[str]:
        """获取指定部位的首词条可能性"""
        return self.first_affix.get(slot, [])

    def get_rating(self, deductions: int, disqualified: bool) -> str:
        """根据扣分和不合格状态返回评级"""
        if disqualified:
            return "垃圾装备"
        if deductions <= self.rating.get("heirloom", 0):
            return "传家宝"
        if deductions <= self.rating.get("qualified", 1):
            return "合格装备"
        if deductions <= self.rating.get("marginal", 2):
            return "凑合装备"
        return "垃圾装备"

    @property
    def max_deductions(self) -> int:
        """熔断阈值"""
        return self.tuning.get("max_deductions", 2)


# ─── YAML 加载 ─────────────────────────────────────────────

def _parse_divine_affix(d: dict) -> DivineAffixRule:
    """解析单条神力规则"""
    match = d.get("match", {})
    match_type = match.get("type")
    # 向后兼容：slot 列表转为单独的 type 规则（由上层拆分）
    match_slot = _ensure_list(match.get("slot", []))
    return DivineAffixRule(
        match_type=match_type,
        match_slot=match_slot,
        affixes=d.get("affixes", []),
        required=d.get("required", True),
        alt=d.get("alt", []),
    )


def _parse_deduction_rule(d: dict) -> DeductionRule:
    """解析单条扣分规则"""
    return DeductionRule(
        type=d["type"],
        affixes=d.get("affixes", []),
        deduction=d.get("deduction", 1),
        disqualify_at=d.get("disqualify_at"),
        count_affix=d.get("count_affix"),
        description=d.get("description", ""),
    )


def _ensure_list(v) -> list:
    """确保值为列表"""
    if isinstance(v, list):
        return v
    return [v] if v else []


def load_rule_config(path: str | Path) -> RuleConfig:
    """从 YAML 文件加载规则配置"""
    path = Path(path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    # 解析转律约束
    tc_data = data.get("tuning_constraints", {})
    tuning_constraints = TuningConstraints(
        no_duplicate=tc_data.get("no_duplicate", True),
        max_attribute_attack=tc_data.get("max_attribute_attack", 2),
    )

    # 解析首词条
    first_affix = {}
    for slot, affixes in data.get("first_affix", {}).items():
        first_affix[slot] = _ensure_list(affixes)

    # 解析神力规则
    divine_affixes = [
        _parse_divine_affix(d) for d in data.get("divine_affixes", [])
    ]

    # 解析扣分规则
    deduction_rules = [
        _parse_deduction_rule(d) for d in data.get("deduction_rules", [])
    ]

    return RuleConfig(
        name=data["name"],
        description=data.get("description", ""),
        valid_affixes=data.get("valid_affixes", []),
        tuning_pool=data.get("tuning_pool", {}),
        tuning_constraints=tuning_constraints,
        priority=data.get("priority", []),
        first_affix=first_affix,
        quality=data.get("quality", {}),
        divine_affixes=divine_affixes,
        deduction_rules=deduction_rules,
        rating=data.get("rating", {}),
        tuning=data.get("tuning", {}),
    )
