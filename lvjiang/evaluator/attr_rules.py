"""属性规则管理器

统一管理：
1. 基础属性品阶推断（原 EquipAttrConfig）
2. 词条上限查询（含承音值）
3. 真实词条名 → 配置类别名 映射

数据来源：config/system/attributes.yaml
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# ─── 真实词条 → 配置类别 映射 ──────────────────────────────

_AFFIX_TO_CATEGORY: dict[str, str] = {
    # 外功攻击类
    "最大外功攻击": "外功攻击",
    "最小外功攻击": "外功攻击",
    # 属性攻击类
    "最大无相攻击": "属性攻击",
    "最小无相攻击": "属性攻击",
    "最大牵丝攻击": "属性攻击",
    "最小牵丝攻击": "属性攻击",
    "最大鸣金攻击": "属性攻击",
    "最小鸣金攻击": "属性攻击",
    "最大裂石攻击": "属性攻击",
    "最小裂石攻击": "属性攻击",
    "最大破竹攻击": "属性攻击",
    "最小破竹攻击": "属性攻击",
    # 五维属性
    "劲": "五维属性",
    "势": "五维属性",
    "敏": "五维属性",
    "体": "五维属性",
    "御": "五维属性",
    # 三率（类别名 = 词条名）
    "会意率": "会意率",
    "会心率": "会心率",
    "精准率": "精准率",
    # 神力词条
    "全武学增效": "全武学增效",
    "扇武学增效": "扇武学增效",
    "对首领单位增伤": "对单位增效",
    "对玩家单位增效": "对单位增效",
    "单体类奇术增伤": "奇术类增伤",
    "群体类奇术增伤": "奇术类增伤",
}

# 武学增伤/增效动态匹配（如 "剑武学增伤" → "单武学增伤"）
# 注意：扇武学增效是独立类别，已在上方精确映射
_WUXUE_PATTERN = re.compile(r"^.+?武学增[伤效]$")

# 承音比例
_CHENGYIN_RATIO = 0.94


# ─── 品阶推断数据结构 ──────────────────────────────────────

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


# ─── equip_type → 配置 key 映射 ─────────────────────────────

_TYPE_TO_KEY = {
    # 武器类型 → weapon
    "陌刀": "weapon", "舞绫鼓": "weapon", "双刀": "weapon",
    "绳镖": "weapon", "横刀": "weapon", "拳甲": "weapon",
    "剑": "weapon", "枪": "weapon", "扇": "weapon", "伞": "weapon",
    # 首饰
    "环": "ring",
    "佩": "pendant",
    # 防具
    "冠胄": "armor_other", "胫甲": "armor_other", "腕甲": "armor_other",
    "胸甲": "chest",
}


# ─── 属性规则管理器 ─────────────────────────────────────────

class AttrRuleManager:
    """属性规则管理器

    从 attributes.yaml 加载全部规则，提供：
    - 品阶推断（base_attrs）
    - 词条上限查询（affix_caps）
    - 词条名映射（真实词条 → 配置类别）
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            path = Path(__file__).resolve().parent.parent.parent / "config" / "system" / "attributes.yaml"
        self._path = Path(path)

        # 品阶推断：key → level → LevelRule
        self._base_rules: dict[str, dict[int, LevelRule]] = {}
        # 词条上限：category → level → { cap, unit }
        self._affix_caps: dict[str, dict[int, dict]] = {}

        self._load()

    def _load(self):
        with open(self._path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # ── base_attrs ──
        base_attrs = data.get("base_attrs", {})
        for key in ("weapon", "ring", "pendant", "armor_other", "chest"):
            section = base_attrs.get(key, {})
            self._base_rules[key] = {}
            for level_str, qualities in section.items():
                level = int(level_str)
                ranges = []
                for q in ("gold", "purple", "blue"):
                    if q not in qualities:
                        continue
                    v = qualities[q]
                    if isinstance(v, dict):
                        ranges.append(AttrRange(
                            quality=q,
                            min_val=v.get("min"),
                            max_val=v.get("max"),
                        ))
                    else:
                        val = int(v)
                        ranges.append(AttrRange(quality=q, min_val=val, max_val=val))
                self._base_rules[key][level] = LevelRule(ranges=ranges)

        # ── affix_caps ──
        raw_caps = data.get("affix_caps", {})
        for category, levels in raw_caps.items():
            self._affix_caps[category] = {}
            if not isinstance(levels, dict):
                continue
            for level_str, entry in levels.items():
                level = int(level_str)
                if isinstance(entry, dict):
                    self._affix_caps[category][level] = {
                        "cap": entry.get("cap", 0),
                        "unit": entry.get("unit", ""),
                    }
                else:
                    # 兼容旧格式（直接数值）
                    self._affix_caps[category][level] = {
                        "cap": entry,
                        "unit": "",
                    }

    # ── 词条映射 ────────────────────────────────────────────

    def resolve_affix_category(self, affix_name: str) -> str:
        """真实词条名 → 配置类别名

        无映射则原样返回。
        武学增伤类统一映射为 "单武学增伤"（扇武学增效除外，单独映射）。
        """
        # 精确匹配（优先，包括 "扇武学增效" 等独立类别）
        cat = _AFFIX_TO_CATEGORY.get(affix_name)
        if cat is not None:
            return cat
        # 武学增伤/增效动态匹配
        if _WUXUE_PATTERN.match(affix_name):
            return "单武学增伤"
        # 无映射，原样返回
        return affix_name

    # ── 词条上限查询 ────────────────────────────────────────

    def get_affix_caps(self, level: int, affix_name: str) -> dict | None:
        """查询某等级某词条的上限

        Args:
            level: 装备等级
            affix_name: 真实词条名（自动映射到类别）

        Returns:
            {"cap": float, "unit": str, "chengyin": float} 或 None
        """
        category = self.resolve_affix_category(affix_name)
        caps = self._affix_caps.get(category, {})
        entry = caps.get(level)
        if entry is None:
            return None
        cap = entry["cap"]
        return {
            "cap": cap,
            "unit": entry["unit"],
            "chengyin": round(cap * _CHENGYIN_RATIO, 2),
        }

    def get_all_affix_categories(self) -> list[str]:
        """返回所有已配置的词条类别名"""
        return list(self._affix_caps.keys())

    # ── 品阶推断 ────────────────────────────────────────────

    def infer_quality(self, equip_type: str, level: int, value: int) -> str | None:
        """根据基础属性推断品阶

        Args:
            equip_type: 装备类型（剑/枪/环/佩/冠胄/胸甲/...）
            level: 装备等级
            value: 属性值（武器范围取 max）

        Returns:
            'gold' / 'purple' / 'blue' / None（无匹配）
        """
        key = _TYPE_TO_KEY.get(equip_type)
        if key is None:
            return None
        rule = self._base_rules.get(key, {}).get(level)
        if rule is None:
            return None
        return rule.infer_quality(value)


# ─── 全局单例 ─────────────────────────────────────────────

_instance: AttrRuleManager | None = None


def get_attr_rule_manager() -> AttrRuleManager:
    """获取全局 AttrRuleManager 单例"""
    global _instance
    if _instance is None:
        _instance = AttrRuleManager()
    return _instance
