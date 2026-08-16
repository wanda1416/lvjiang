"""毕业率计算模块

基于 Excel 公式逆向实现的毕业率计算器。
从 calc_graduation.py 迁移而来，支持按流派注册计算器。

计算链路：
    CombatAttributes（最终面板）→ 桥接函数 → 计算器 INPUT → DPS → 毕业率
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from ..combat_attrs import (
    CombatAttributes,
    apply_bonus_resistance,
    apply_three_rate_resistance,
)

# ─── 项目数据目录 ──────────────────────────────────────────────
_DATA_DIR = Path(__file__).parents[5] / "data" / "graduation"


# ─── 数据结构 ──────────────────────────────────────────────────

@dataclass
class GraduationResult:
    """毕业率计算结果"""
    total_damage: float       # 总期望伤害
    dps: float                # DPS
    graduation_rate: float    # 毕业率 (0~1)
    baseline_dps: float       # 基准 DPS
    combat_time: float        # 战斗时间 (秒)


# ─── 各流派基准 DPS（从 Excel 提取） ─────────────────────────
BASELINE_DPS: dict[str, float] = {
    "鸣金·虹": 120570.64,
    "鸣金·影": 136862.71,
    "牵丝·玉": 137523.51,
    "牵丝·翊": 100459.97,
    "破竹·尘": 120448.31,
    "破竹·风": 131059.36,
    "破竹·鸢": 127284.15,
    "破竹·樽": 148520.28,
    "裂石·威": 119183.52,
    "裂石·钧": 125264.41,
    "牵丝·霖": 101313.33,
}

# ─── 流派 → 主属性映射 ────────────────────────────────────────
SCHOOL_ELEMENT: dict[str, str] = {
    "鸣金·虹": "鸣金", "鸣金·影": "鸣金",
    "裂石·威": "裂石", "裂石·钧": "裂石",
    "牵丝·玉": "牵丝", "牵丝·霖": "牵丝", "牵丝·翊": "牵丝",
    "破竹·尘": "破竹", "破竹·风": "破竹", "破竹·鸢": "破竹", "破竹·樽": "破竹",
}

# ─── 流派 → 主武器映射 ────────────────────────────────────────
SCHOOL_WEAPON: dict[str, str] = {
    "鸣金·虹": "剑", "鸣金·影": "剑",
    "裂石·威": "陌刀", "裂石·钧": "横刀",
    "牵丝·玉": "伞", "牵丝·霖": "扇", "牵丝·翊": "舞绫鼓",
    "破竹·尘": "伞", "破竹·风": "双刀", "破竹·鸢": "手甲", "破竹·樽": "手甲",
}


# ─── 抽象基类 ──────────────────────────────────────────────────

class GraduationCalculator(ABC):
    """毕业率计算器抽象基类

    每个流派实现一个子类，提供自己的：
    - 技能轴数据 (rotation / skills / buffs)
    - 基准 DPS
    - 战斗时间
    - 特定计算逻辑（如有差异）
    """

    @abstractmethod
    def calculate(self, attrs: CombatAttributes) -> GraduationResult:
        """计算毕业率

        Args:
            attrs: 最终战斗属性（基础 + 装备 + 弓玦，已含心法）

        Returns:
            GraduationResult
        """

    @abstractmethod
    def baseline_dps(self) -> float:
        """基准 DPS（理论最大 DPS）"""

    @abstractmethod
    def combat_time(self) -> float:
        """战斗时间（秒）"""


# ─── 通用计算器实现 ────────────────────────────────────────────

class GenericCalculator(GraduationCalculator):
    """通用毕业率计算器

    从 data/graduation/{school_name}.json 加载数据，
    metadata 中读取 baseline_dps 和 combat_time。
    """

    def __init__(self, school_name: str) -> None:
        self._school = school_name
        self._data = self._load_data(school_name)
        meta = self._data.get("metadata", {})
        self._baseline = meta.get("baseline_dps", BASELINE_DPS.get(school_name, 0))
        self._combat_time = meta.get("combat_time", 109.1)

    def baseline_dps(self) -> float:
        return self._baseline

    def combat_time(self) -> float:
        return self._combat_time

    @staticmethod
    def _load_data(school_name: str) -> dict[str, Any]:
        path = _DATA_DIR / f"{school_name}.json"
        if not path.exists():
            logger.error(f"毕业率数据文件不存在: {path}")
            return {"rotation": [], "skills": {}, "buffs": {}}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def calculate(self, attrs: CombatAttributes) -> GraduationResult:
        input_data = _combat_attrs_to_input(attrs, self._school)
        data = self._data
        rotation = data.get("rotation", [])
        skills = data.get("skills", {})
        buffs_table = data.get("buffs", {})

        element_bonus = _calc_element_bonus(input_data)

        total_expected = 0.0
        total_zhenqi = 0.0

        for item in rotation:
            skill_name = item["skill"]
            hits = item["hits"]
            buff_names = item["buffs"]

            if skill_name == "N/a" or skill_name not in skills:
                continue

            skill = skills[skill_name]
            buffs = [
                buffs_table[b] for b in buff_names if b in buffs_table
            ]

            result = _calc_skill_damage(skill, buffs, element_bonus, input_data)
            total_expected += result["expected"] * hits
            total_zhenqi += result["zhenqi"] * hits

        combat_time = self.combat_time()
        dps = total_expected / combat_time if combat_time > 0 else 0
        baseline = self.baseline_dps()
        rate = dps / baseline if baseline > 0 else 0

        return GraduationResult(
            total_damage=total_expected,
            dps=dps,
            graduation_rate=rate,
            baseline_dps=baseline,
            combat_time=combat_time,
        )


# ─── 桥接函数：CombatAttributes → 计算器 INPUT ────────────────

def _combat_attrs_to_input(
    attrs: CombatAttributes,
    school_name: str = "鸣金·虹",
) -> dict[str, Any]:
    """将 CombatAttributes 映射为计算器 INPUT 字典

    关键处理：
    - 三率取抗性后的生效值
    - 穿透取抗性后的生效值
    - 增伤类取抗性后的生效值
    - 武器增效从 extra_attrs 按流派主武器提取
    - 主属性根据流派动态设置
    """
    main_element = SCHOOL_ELEMENT.get(school_name, "鸣金")
    main_weapon = SCHOOL_WEAPON.get(school_name, "剑")

    # 有效三率（抗性后）
    eff_precision = apply_three_rate_resistance("precision", attrs.precision)
    eff_crit_rate = apply_three_rate_resistance("crit_rate", attrs.crit_rate)
    eff_intent_rate = apply_three_rate_resistance("intent_rate", attrs.intent_rate)

    # 有效穿透（抗性后）
    eff_outer_pen = apply_bonus_resistance(attrs.outer_pen)
    eff_mingjin_pen = apply_bonus_resistance(attrs.mingjin_pen)

    # 有效增伤（抗性后）
    eff_all_skill = apply_bonus_resistance(attrs.all_skill_bonus)
    eff_boss = apply_bonus_resistance(attrs.boss_bonus)

    # 武器增效（从 extra_attrs 按主武器提取）
    weapon_bonus = _extract_weapon_bonus(attrs.extra_attrs, main_weapon)
    charge_dingyin = 0.0
    for key, value in attrs.extra_attrs.items():
        if "蓄力技增伤" in key or "蓄力技增效" in key:
            charge_dingyin = apply_bonus_resistance(value)

    combat_time = 109.1  # 默认值，会被 GenericCalculator 的 metadata 覆盖
    return {
        # 基础攻击
        "min_outer": attrs.min_outer,
        "max_outer": attrs.max_outer,
        "outer_pen": eff_outer_pen,
        "mingjin_bonus": attrs.mingjin_bonus,
        "min_mingjin": attrs.min_mingjin,
        "max_mingjin": attrs.max_mingjin,
        "mingjin_pen": eff_mingjin_pen,
        "min_wuxiang": attrs.min_wuxiang,
        "max_wuxiang": attrs.max_wuxiang,
        # 角色属性（生效值）
        "precision": eff_precision,
        "crit_rate": eff_crit_rate,
        "intent_rate": eff_intent_rate,
        "direct_crit": attrs.direct_crit,
        "direct_intent": attrs.direct_intent,
        "crit_dmg": attrs.crit_dmg,
        "intent_dmg": attrs.intent_dmg,
        # 增伤属性（生效值）
        "all_skill_bonus": eff_all_skill,
        "boss_bonus": eff_boss,
        "weapon_bonus": weapon_bonus,
        "single_bonus": apply_bonus_resistance(attrs.single_qs_bonus),
        "aoe_bonus": apply_bonus_resistance(attrs.group_qs_bonus),
        "charge_dingyin": charge_dingyin,
        "fixed_dmg_bonus": 0.4,  # 固伤加成（默认值）
        "food_min": 0,
        "food_max": 0,
        # 全局配置
        "main_element": main_element,
        "main_weapon": main_weapon,
        "set_name": "玉斗",
        "combat_time": combat_time,
        "boss_defense": 558,
        "outer_resist": 5,
        "elem_resist": 30,
        "dungeon_talent": 0.06,
    }


def _extract_weapon_bonus(extra_attrs: dict[str, float], main_weapon: str) -> float:
    """从 extra_attrs 提取主武器的武学增伤

    匹配规则：key 包含 "{武器名}武学增伤" 或 "{武器名}武学增效"
    例如：剑→"剑武学增伤", 伞→"伞武学增伤", 陌刀→"陌刀武学增伤"
    """
    for key, value in extra_attrs.items():
        if (key.endswith("武学增伤") or key.endswith("武学增效")) and main_weapon in key:
            return apply_bonus_resistance(value)
    return 0.0


# ─── 计算器内部计算逻辑（从 calc_graduation.py 迁移）──────────

def _calc_pen_bonus(total_pen: float) -> float:
    """穿透加成公式: >0 除以200, <=0 除以100"""
    return total_pen / 200 if total_pen > 0 else total_pen / 100


def _calc_element_bonus(input_data: dict) -> dict[str, float]:
    """计算各武学属性的总增伤

    G3 = boss_bonus + 1.5% + 8%(秋瞑帖)
    主武器增伤 = all_skill + weapon_bonus
    """
    g3 = input_data["boss_bonus"] + 0.015 + 0.08
    weapon_b = input_data.get("weapon_bonus", 0)
    return {
        "通用增伤": g3,
        "剑": input_data["all_skill_bonus"] + weapon_b,
        "枪": input_data["all_skill_bonus"] + weapon_b,
        "伞": input_data["all_skill_bonus"] + weapon_b,
        "扇": input_data["all_skill_bonus"] + weapon_b,
        "陌刀": input_data["all_skill_bonus"] + weapon_b,
        "横刀": input_data["all_skill_bonus"] + weapon_b,
        "双刀": input_data["all_skill_bonus"] + weapon_b,
        "绳镖": input_data["all_skill_bonus"] + weapon_b,
        "手甲": input_data["all_skill_bonus"] + weapon_b,
        "舞绫鼓": input_data["all_skill_bonus"] + weapon_b,
        "单体奇术": input_data["single_bonus"],
        "群体奇术": input_data["aoe_bonus"],
    }


def _calc_skill_damage(
    skill: dict,
    buffs: list[dict],
    element_bonus: dict[str, float],
    input_data: dict,
) -> dict[str, float]:
    """计算单个技能的期望伤害"""

    # 从增益表汇总属性
    def _buff_sum(key: str) -> float:
        return sum(b.get(key, 0) for b in buffs)

    buff_general = _buff_sum("通用增伤")
    buff_min_outer = _buff_sum("最小外功")
    buff_max_outer = _buff_sum("最大外功")
    buff_outer_bonus = _buff_sum("外功加成")
    buff_outer_pen = _buff_sum("外功穿透")
    buff_mingjin_pen = _buff_sum("鸣金穿透")
    buff_mingjin_bonus = _buff_sum("鸣金加成")
    buff_crit_rate = _buff_sum("会心率")
    buff_crit_dmg = _buff_sum("会心伤害")
    buff_intent_rate = _buff_sum("会意率")
    buff_intent_dmg = _buff_sum("会意伤害")
    buff_direct_crit = _buff_sum("直接会心率")
    buff_direct_intent = _buff_sum("直接会意率")
    buff_special = _buff_sum("特殊增伤")

    # ── 即时外功攻击 ──
    an = skill.get("外功加成", 0) + buff_outer_bonus
    is_feisun = input_data["set_name"] == "飞隼"
    feisun_mult = 1.1 if is_feisun else 1.0
    min_outer = (input_data["min_outer"] + input_data["food_min"]) * feisun_mult
    max_outer = max(
        input_data["min_outer"] + input_data["food_min"],
        input_data["max_outer"] + input_data["food_max"],
    ) * feisun_mult
    ao = (min_outer * (1 + an) + skill.get("最小外功", 0)
          + buff_min_outer - input_data["boss_defense"])
    ap = (max_outer * (1 + an) + skill.get("最大外功", 0)
          + buff_max_outer - input_data["boss_defense"])

    # ── 即时鸣金攻击 ──
    as_bonus = 0
    is_mingjin_elem = input_data["main_element"] == "鸣金"
    min_mingjin = input_data["min_mingjin"] + (
        input_data["min_wuxiang"] if is_mingjin_elem else 0
    )
    max_mingjin = max(input_data["min_mingjin"], input_data["max_mingjin"]) + (
        input_data["max_wuxiang"] if is_mingjin_elem else 0
    )
    at = min_mingjin * (1 + as_bonus)
    au = max_mingjin * (1 + as_bonus)

    # ── 穿透加成 ──
    total_outer_pen = (
        input_data["outer_pen"] - input_data["outer_resist"]
        + skill.get("外功穿透", 0) + buff_outer_pen
    )
    aq = _calc_pen_bonus(total_outer_pen)

    total_mingjin_pen = (
        input_data["mingjin_pen"] - input_data["elem_resist"]
        + skill.get("鸣金穿透", 0) + buff_mingjin_pen
    )
    av = _calc_pen_bonus(total_mingjin_pen)

    # ── 鸣金加成 ──
    aw = input_data["mingjin_bonus"] + skill.get("鸣金加成", 0) + buff_mingjin_bonus

    # ── 通用增伤 ──
    elem_type = skill.get("类型", "")
    am = (element_bonus["通用增伤"] + element_bonus.get(elem_type, 0)
          + skill.get("通用增伤", 0) + buff_general)

    # ── 特殊增伤 ──
    br = buff_special + input_data["dungeon_talent"]

    # ── 生效精准 ──
    bj = min(1.0, input_data["precision"])

    # ── 生效会心率 ──
    if skill.get("强制会心", 0) == 1:
        bk = 1.0
    else:
        bk = (min(0.8, input_data["crit_rate"] + skill.get("会心率", 0)
                   + buff_crit_rate)
              + input_data["direct_crit"] + buff_direct_crit)
        if skill.get("强制精准", 0) == 1:
            bk = 0

    # ── 生效会心伤害 ──
    bl = 1 + input_data["crit_dmg"] + skill.get("会心伤害", 0) + buff_crit_dmg

    # ── 生效会意率 ──
    if skill.get("强制会意", 0) == 1:
        bm = 1.0
    else:
        bm = (min(0.4, input_data["intent_rate"] + skill.get("会意率", 0)
                   + buff_intent_rate)
              + input_data["direct_intent"] + buff_direct_intent)
        if skill.get("强制会心", 0) == 1:
            bm = 0

    # ── 生效会意伤害 ──
    bn = 1 + input_data["intent_dmg"] + skill.get("会意伤害", 0) + buff_intent_dmg

    # ── 占比计算 ──
    bo = min((1 - bm) * bj, bj * bk)
    bq = 0 if skill.get("强制精准", 0) == 1 else (1 - bj) * (1 - bm)
    bp = 1 - bm - bo - bq

    # ── 蓄力加成 ──
    charge_bonus = (
        input_data["charge_dingyin"]
        if skill.get("定音加成", "") == "蓄力技"
        else 0
    )

    # ── 固伤加成 ──
    skill_type = skill.get("类型", "")
    fixed_bonus_mult = (
        1 + input_data["fixed_dmg_bonus"] if skill_type in ("剑", "枪") else 1.0
    )
    effective_outer_fixed = skill.get("外攻固伤", 0) * fixed_bonus_mult
    effective_attr_fixed = skill.get("属性固伤", 0) * fixed_bonus_mult

    # ── 外功部分 ──
    outer_avg = ((ao + ap) / 2 * skill.get("外功倍率", 0)
                 + effective_outer_fixed) * (1 + aq)
    outer_max = (ap * skill.get("外功倍率", 0)
                 + effective_outer_fixed) * (1 + aq)
    outer_min = (ao * skill.get("外功倍率", 0)
                 + effective_outer_fixed) * (1 + aq)

    # ── 鸣金部分 ──
    is_mingjin = input_data["main_element"] == "鸣金"
    mingjin_mult = (
        skill.get("属性倍率", 0) if is_mingjin else skill.get("外功倍率", 0)
    )
    mingjin_fixed = effective_attr_fixed * 1.5 if is_mingjin else 0

    mingjin_avg = ((at + au) / 2 * mingjin_mult + mingjin_fixed) * (1 + av) * (1 + aw)
    mingjin_max = (au * mingjin_mult + mingjin_fixed) * (1 + av) * (1 + aw)
    mingjin_min = (at * mingjin_mult + mingjin_fixed) * (1 + av) * (1 + aw)

    # ── 期望伤害 ──
    m = ((outer_avg + mingjin_avg) * (1 + am) * bl
         * (1 + charge_bonus) * (1 + br))
    n = ((outer_max + mingjin_max) * (1 + am) * bn
         * (1 + charge_bonus) * (1 + br))
    o = ((outer_avg + mingjin_avg) * (1 + am)
         * (1 + charge_bonus) * (1 + br))
    p = ((outer_min + mingjin_min) * (1 + am)
         * (1 + charge_bonus) * (1 + br))

    expected = m * bo + n * bm + o * bp + p * bq
    zhenqi = expected * skill.get("真气比例", 1.1)

    return {"expected": expected, "zhenqi": zhenqi}


# ─── 工厂函数 ──────────────────────────────────────────────────

_ALL_SCHOOLS = [
    "鸣金·虹", "鸣金·影",
    "裂石·威", "裂石·钧",
    "牵丝·玉", "牵丝·霖", "牵丝·翊",
    "破竹·尘", "破竹·风", "破竹·鸢", "破竹·樽",
]


def get_graduation_calculator(
    school_name: str,
) -> GraduationCalculator | None:
    """根据流派名获取毕业率计算器

    所有流派统一使用 GenericCalculator，从 JSON 数据文件加载。
    未实现或缺少数据文件时返回 None。
    """
    if school_name not in _ALL_SCHOOLS:
        return None
    try:
        return GenericCalculator(school_name)
    except Exception as e:
        logger.error(f"创建毕业率计算器失败 ({school_name}): {e}")
        return None
