"""装备领域常量定义

部位分类、武器类型枚举、词条名称枚举等。
"""

import re


# ─── 部位分类（旧，仅 parser 内部用于 base_attr 分派） ────

WEAPON_SLOTS = {"main_weapon", "sub_weapon"}
JEWELRY_SLOTS = {"ring", "pendant"}
ARMOR_SLOTS = {"head", "chest", "leg", "wrist"}

# ─── 装备类型分类（type-based，替代 slot-based） ────────────

WEAPON_TYPES_SET: set[str] = {
    "陌刀", "舞绫鼓", "双刀", "绳镖", "横刀", "手甲",
    "剑", "枪", "扇", "伞",
}
JEWELRY_TYPES_SET: set[str] = {"环", "佩"}
ARMOR_TYPES_SET: set[str] = {"冠胄", "胸甲", "胫甲", "腕甲"}


def infer_category(equip_type: str | None) -> str:
    """从装备 type 推断类别

    Returns: "weapon" / "jewelry" / "armor" / "unknown"
    """
    if equip_type in WEAPON_TYPES_SET:
        return "weapon"
    if equip_type in JEWELRY_TYPES_SET:
        return "jewelry"
    if equip_type in ARMOR_TYPES_SET:
        return "armor"
    return "unknown"

# ─── 武器类型枚举 ──────────────────────────────────────────

WEAPON_TYPES = [
    "陌刀", "舞绫鼓", "双刀", "绳镖", "横刀", "手甲",
    "剑", "枪", "扇", "伞",
]

# ─── 词条名称枚举（按长度降序，保证最长前缀优先匹配）────────

AFFIX_NAMES = sorted([
    # 外功攻击类
    "最大外功攻击", "最小外功攻击",
    # 属性攻击类
    "最大无相攻击", "最小无相攻击",
    "最大牵丝攻击", "最小牵丝攻击",
    "最大鸣金攻击", "最小鸣金攻击",
    "最大裂石攻击", "最小裂石攻击",
    "最大破竹攻击", "最小破竹攻击",
    # 三率类
    "会心率", "会意率", "精准率",
    # 基础属性类
    "劲", "势", "敏", "体", "御",
    # 神力词条
    "全武学增效",
    "单体类奇术增伤", "群体类奇术增伤",
    "对首领单位增伤", "对玩家单位增效",
    # 其他
    "气血最大值", "外功防御",
], key=len, reverse=True)

# 武学增伤/增效需要动态匹配（如 "剑武学增伤"、"扇武学增效"），单独处理
WUXUE_PATTERN = re.compile(r"^(.+?)武学增[伤效]")

# 带 % 的词条（三率 + 神力类）
PERCENT_AFFIXES = {
    "会心率", "会意率", "精准率",
    "全武学增效", "单体类奇术增伤", "群体类奇术增伤",
    "对首领单位增伤", "对玩家单位增效",
}
