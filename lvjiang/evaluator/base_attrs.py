"""装备品阶推断

根据基础属性数值反推装备品阶（gold/purple/blue/green），
同时校验 OCR 解析结果是否合理。

数据来源：config/base_attrs.yaml
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from lvjiang.equip_parser.constants import WEAPON_SLOTS


# ─── 数据结构 ──────────────────────────────────────────────

@dataclass
class AttrRange:
    """单个品阶的属性值范围"""
    quality: str           # gold / purple / blue
    min_val: int | None = None
    max_val: int | None = None

    def contains(self, value: int) -> bool:
        if self.min_val is not None and value < self.min_val:
            return False
        if self.max_val is not None and value > self.max_val:
            return False
        return True


@dataclass
class LevelRule:
    """某个分类在某个等级的品阶规则"""
    ranges: list[AttrRange] = field(default_factory=list)

    def infer_quality(self, value: int) -> str | None:
        for r in self.ranges:
            if r.contains(value):
                return r.quality
        return None


# ─── slot → 配置 key 映射 ─────────────────────────────────

_SLOT_TO_KEY = {
    "main_weapon": "weapon",
    "sub_weapon": "weapon",
    "ring": "ring",
    "pendant": "pendant",
    "head": "armor_other",
    "leg": "armor_other",
    "wrist": "armor_other",
    "chest": "chest",
}


# ─── 配置加载 ──────────────────────────────────────────────

class BaseAttrConfig:
    """基础属性规则配置

    从 base_attrs.yaml 加载，提供品阶推断接口。
    配置结构为 5 种平铺分类 + defense：
        weapon / ring / pendant / armor_other / chest / defense
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path(__file__).resolve().parent.parent.parent / "config" / "base_attrs.yaml"
        self._path = Path(path)
        # key → level → LevelRule
        self._rules: dict[str, dict[int, LevelRule]] = {}
        self._load()

    def _load(self):
        with open(self._path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 五种属性分类（结构相同：level → quality → value）
        for key in ("weapon", "ring", "pendant", "armor_other", "chest"):
            section = data.get(key, {})
            self._rules[key] = {}
            for level_str, qualities in section.items():
                level = int(level_str)
                ranges = []
                for q in ("gold", "purple", "blue"):
                    if q not in qualities:
                        continue
                    v = qualities[q]
                    if isinstance(v, dict):
                        # 范围值（weapon）
                        ranges.append(AttrRange(
                            quality=q,
                            min_val=v.get("min"),
                            max_val=v.get("max"),
                        ))
                    else:
                        # 单值
                        val = int(v)
                        ranges.append(AttrRange(quality=q, min_val=val, max_val=val))
                self._rules[key][level] = LevelRule(ranges=ranges)

    # ── 品阶推断接口 ──

    def infer_quality(self, slot: str, level: int, value: int) -> str | None:
        """根据基础属性推断品阶

        Args:
            slot: 部位 key（main_weapon / ring / head 等）
            level: 装备等级
            value: 属性值（武器范围取 max）

        Returns:
            'gold' / 'purple' / 'blue' / None（无匹配）
        """
        key = _SLOT_TO_KEY.get(slot)
        if key is None:
            return None
        rule = self._rules.get(key, {}).get(level)
        if rule is None:
            return None
        return rule.infer_quality(value)
