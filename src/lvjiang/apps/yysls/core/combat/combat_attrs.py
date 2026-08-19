"""战斗属性数据模型

定义角色最终战斗属性的字段集合，用于：
- 装备属性聚合（基础属性 + 装备 + 弓玦 → 战斗属性）
- 毕业率计算器的输入
- 反推基础属性（面板 - 装备 - 弓玦 → 基础属性）

字段来源于 Excel 计算器的输入参数和 leoq7 的面板属性。

抗性机制（110级为例，除数 = 1 + 抗性/100）：
- 三率抗性 = 145 → 除数 = 2.45
  - 精准率: 生效 = 基础值 + (面板 - 基础值) / 2.45，基础值 ≈ 65
  - 会心率: 生效 = 面板 / 2.45，上限 80%
  - 会意率: 生效 = 面板 / 2.45，上限 40%
- 增效抗性 = 15 → 除数 = 1.15
  - 穿透类（外功穿透/属攻穿透）: 生效 = 基础值 + 装备值 / 1.15
  - 增伤类（全武学增效/对首领增伤等）: 生效 = 面板 / 1.15
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from loguru import logger

# 战斗属性字段定义（顺序即展示顺序）
# 每组: (字段名, 显示名, 单位, 是否区间)
COMBAT_ATTR_FIELDS: list[tuple[str, str, str, bool]] = [
    # 攻击区间
    ("min_outer", "最小外功攻击", "", False),
    ("max_outer", "最大外功攻击", "", False),
    # 属攻区间（四种，一行展示）
    ("min_mingjin", "最小鸣金攻击", "", False),
    ("max_mingjin", "最大鸣金攻击", "", False),
    ("min_lieshi", "最小裂石攻击", "", False),
    ("max_lieshi", "最大裂石攻击", "", False),
    ("min_pozhu", "最小破竹攻击", "", False),
    ("max_pozhu", "最大破竹攻击", "", False),
    ("min_qiansi", "最小牵丝攻击", "", False),
    ("max_qiansi", "最大牵丝攻击", "", False),
    ("min_wuxiang", "最小无相攻击", "", False),
    ("max_wuxiang", "最大无相攻击", "", False),
    # 三率（有抗性）
    ("precision", "精准率", "%", False),
    ("crit_rate", "会心率", "%", False),
    ("intent_rate", "会意率", "%", False),
    # 直接率（无抗性）
    ("direct_crit", "直接会心率", "%", False),
    ("direct_intent", "直接会意率", "%", False),
    # 伤害加成
    ("crit_dmg", "会心伤害加成", "%", False),
    ("intent_dmg", "会意伤害加成", "%", False),
    ("outer_bonus", "外功伤害加成", "%", False),
    ("mingjin_bonus", "鸣金伤害加成", "%", False),
    ("lieshi_bonus", "裂石伤害加成", "%", False),
    ("pozhu_bonus", "破竹伤害加成", "%", False),
    ("qiansi_bonus", "牵丝伤害加成", "%", False),
    # 穿透（增效类，基础值不受抗性影响）
    ("outer_pen", "外功穿透", "", False),
    ("mingjin_pen", "鸣金穿透", "", False),
    ("lieshi_pen", "裂石穿透", "", False),
    ("pozhu_pen", "破竹穿透", "", False),
    ("qiansi_pen", "牵丝穿透", "", False),
    # 增伤（增效类，整个值除以除数）
    ("all_skill_bonus", "全武学增效", "%", False),
    ("boss_bonus", "对首领单位增伤", "%", False),
    ("player_bonus", "对玩家单位增效", "%", False),
    # 奇术增伤（增效类，装备提供，不参与反推）
    ("single_qs_bonus", "单体类奇术增伤", "%", False),
    ("group_qs_bonus", "群体类奇术增伤", "%", False),
    # 武器/技能增效（流派相关，动态字段）
    # 这些字段通过 extra_attrs 存储
]

# 流派属性 → 字段名映射
# 用于将属攻相关字段动态映射到当前流派对应的属性
SCHOOL_ATTR_FIELD_MAP: dict[str, dict[str, str]] = {
    "鸣金": {
        "min_attr": "min_mingjin", "max_attr": "max_mingjin",
        "attr_pen": "mingjin_pen", "attr_bonus": "mingjin_bonus",
    },
    "裂石": {
        "min_attr": "min_lieshi", "max_attr": "max_lieshi",
        "attr_pen": "lieshi_pen", "attr_bonus": "lieshi_bonus",
    },
    "破竹": {
        "min_attr": "min_pozhu", "max_attr": "max_pozhu",
        "attr_pen": "pozhu_pen", "attr_bonus": "pozhu_bonus",
    },
    "牵丝": {
        "min_attr": "min_qiansi", "max_attr": "max_qiansi",
        "attr_pen": "qiansi_pen", "attr_bonus": "qiansi_bonus",
    },
}

# 玩法输入字段分组（用于创建/编辑玩法对话框）
# 排除只能由装备提供的字段（全武学增效、对首领增伤、指定武学增效）
# 每组: (label, [(field_name, display_label, unit), ...])
# 同组字段显示在同一行
# 属攻相关字段使用占位符，运行时根据流派动态替换
PLAY_STYLE_FIELD_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("外功攻击", [("min_outer", "最小", ""), ("max_outer", "最大", "")]),
    ("属攻攻击", [("__min_attr__", "最小", ""), ("__max_attr__", "最大", "")]),
    ("无相攻击", [("min_wuxiang", "最小", ""), ("max_wuxiang", "最大", "")]),
    ("判定属性", [("precision", "精准率", "%")]),
    ("", [("crit_rate", "会心率(白字)", "%"), ("direct_crit", "直接会心率", "%")]),
    ("", [("intent_rate", "会意率(白字)", "%"), ("direct_intent", "直接会意率", "%")]),
    ("增益效果", [("outer_pen", "外功穿透", ""), ("__attr_pen__", "属攻穿透", "")]),
    ("增伤效果", [("crit_dmg", "会心伤害加成", "%"), ("intent_dmg", "会意伤害加成", "%")]),
    ("", [("outer_bonus", "外功伤害加成", "%"), ("__attr_bonus__", "属攻伤害加成", "%")]),
]

# 抗性配置（110级默认值）
# 三率抗性 = 145 → 除数 = 1 + 145/100 = 2.45
# 增效抗性 = 15 → 除数 = 1 + 15/100 = 1.15
THREE_RATE_RESISTANCE = 145  # 三率抗性值
BONUS_RESISTANCE = 15        # 增效抗性值

# 计算除数
THREE_RATE_DIVISOR = 1 + THREE_RATE_RESISTANCE / 100  # 2.45
BONUS_DIVISOR = 1 + BONUS_RESISTANCE / 100            # 1.15

# 精准率基础值（面板超出此值的部分才受抗性影响）
PRECISION_BASE = 65.0

# 三率上限（会心/会意除以除数后的上限）
CRIT_RATE_CAP = 0.80   # 80%
INTENT_RATE_CAP = 0.40  # 40%

# 三率字段
THREE_RATE_FIELDS = {"precision", "crit_rate", "intent_rate"}

# 穿透字段（基础值不受抗性，装备定音值除以除数）
PENETRATION_FIELDS = {"outer_pen", "mingjin_pen", "lieshi_pen", "pozhu_pen", "qiansi_pen"}

# 无相穿透 → 属攻穿透字段映射（定音只有无相穿透，需要根据流派属性转换）
WUXIANG_TO_ATTR_PEN = {
    "鸣金": "mingjin_pen",
    "裂石": "lieshi_pen",
    "破竹": "pozhu_pen",
    "牵丝": "qiansi_pen",
}

# 增伤字段（整个值除以除数）
BONUS_PERCENT_FIELDS = {
    "all_skill_bonus", "boss_bonus", "player_bonus",
    "single_qs_bonus", "group_qs_bonus",
}

# 动态增效字段后缀（整个值除以除数）
BONUS_SUFFIXES = ("武学增伤", "武学增效")

# ─── 五维属性转换系数 ─────────────────────────────────────
# 劲/势/敏/体/御 是装备词条，需要转换为战斗属性
# 转换公式来源：leoq7 WASM 反推
#
# 劲: 1点劲 ≈ 0.246小外攻 + 1.315大外攻
# 势: 1点势 = 0.9大外攻 + 0.04%会意率
# 敏: 1点敏 = 1小外攻 + 0.075%会心率
# 体: 1点体 = 60生命值（当前系统不追踪）
# 御: 1点御 = 17生命值 + 0.5防御（当前系统不追踪）
JIN_TO_MIN_OUTER = 0.246    # 1劲 → 最小外功攻击
JIN_TO_MAX_OUTER = 1.315    # 1劲 → 最大外功攻击
SHI_TO_MAX_OUTER = 0.9      # 1势 → 最大外功攻击
SHI_TO_INTENT_RATE = 0.0004  # 1势 → 0.04%会意率（小数形式）
MIN_TO_MIN_OUTER = 1.0       # 1敏 → 最小外功攻击
MIN_TO_CRIT_RATE = 0.00075   # 1敏 → 0.075%会心率（小数形式）

# 五维词条名集合（用于装备聚合时识别和延迟转换）
FIVE_DIM_NAMES = {"劲", "势", "敏", "体", "御"}


@dataclass
class CombatAttributes:
    """战斗属性集合

    包含固定字段和动态字段（extra_attrs）。
    固定字段是大多数流派共有的属性；
    动态字段是流派/玩法特有的属性（如特定武器增效、技能增伤）。

    抗性机制：
    - 精准率: 生效 = 基础值 + (面板 - 基础值) / 2.45
    - 会心/会意率: 生效 = 面板 / 2.45（有上限）
    - 穿透类: 生效 = 基础值 + 装备值 / 1.15
    - 增伤类: 生效 = 面板 / 1.15
    """
    # 攻击区间
    min_outer: float = 0.0
    max_outer: float = 0.0
    # 属攻区间（四种）
    min_mingjin: float = 0.0
    max_mingjin: float = 0.0
    min_lieshi: float = 0.0
    max_lieshi: float = 0.0
    min_pozhu: float = 0.0
    max_pozhu: float = 0.0
    min_qiansi: float = 0.0
    max_qiansi: float = 0.0
    min_wuxiang: float = 0.0
    max_wuxiang: float = 0.0

    # 三率（存储为小数，有抗性）
    precision: float = 0.0
    crit_rate: float = 0.0
    intent_rate: float = 0.0
    # 直接率（无抗性）
    direct_crit: float = 0.0
    direct_intent: float = 0.0

    # 伤害加成（小数）
    crit_dmg: float = 0.0
    intent_dmg: float = 0.0
    outer_bonus: float = 0.0
    # 属攻伤害加成（四种，根据流派属性使用对应字段）
    mingjin_bonus: float = 0.0
    lieshi_bonus: float = 0.0
    pozhu_bonus: float = 0.0
    qiansi_bonus: float = 0.0

    # 穿透（增效类，基础值不受抗性）
    outer_pen: float = 0.0
    mingjin_pen: float = 0.0
    lieshi_pen: float = 0.0
    pozhu_pen: float = 0.0
    qiansi_pen: float = 0.0
    # 无相穿透（定音词条，需要根据流派属性转换为属攻穿透）
    wuxiang_pen: float = 0.0

    # 增伤（小数，有抗性）
    all_skill_bonus: float = 0.0
    boss_bonus: float = 0.0
    player_bonus: float = 0.0
    # 奇术增伤（小数，有抗性，装备提供）
    single_qs_bonus: float = 0.0
    group_qs_bonus: float = 0.0

    # 动态字段（流派/玩法特有）
    extra_attrs: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转为字典（用于序列化）"""
        result = {}
        for f in fields(self):
            if f.name == "extra_attrs":
                continue
            result[f.name] = getattr(self, f.name)
        if self.extra_attrs:
            result["extra_attrs"] = dict(self.extra_attrs)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CombatAttributes:
        """从字典加载"""
        obj = cls()
        for f in fields(cls):
            if f.name == "extra_attrs":
                continue
            if f.name in data:
                setattr(obj, f.name, float(data[f.name]))
        if "extra_attrs" in data and isinstance(data["extra_attrs"], dict):
            obj.extra_attrs = {k: float(v) for k, v in data["extra_attrs"].items()}
        return obj

    def __sub__(self, other: CombatAttributes) -> CombatAttributes:
        """减法：用于反推基础属性（面板 - 装备 - 弓玦）"""
        result = CombatAttributes()
        for f in fields(self):
            if f.name == "extra_attrs":
                continue
            result.extra_attrs[f.name] = getattr(self, f.name) - getattr(other, f.name)
        # 处理动态字段
        all_keys = set(self.extra_attrs.keys()) | set(other.extra_attrs.keys())
        for key in all_keys:
            v1 = self.extra_attrs.get(key, 0.0)
            v2 = other.extra_attrs.get(key, 0.0)
            result.extra_attrs[key] = v1 - v2
        # 把固定字段从 extra_attrs 移回固定字段
        for f in fields(self):
            if f.name == "extra_attrs":
                continue
            if f.name in result.extra_attrs:
                setattr(result, f.name, result.extra_attrs.pop(f.name))
        return result

    def __add__(self, other: CombatAttributes) -> CombatAttributes:
        """加法：用于叠加属性（基础 + 装备 + 弓玦）"""
        result = CombatAttributes()
        for f in fields(self):
            if f.name == "extra_attrs":
                continue
            setattr(result, f.name, getattr(self, f.name) + getattr(other, f.name))
        # 处理动态字段
        all_keys = set(self.extra_attrs.keys()) | set(other.extra_attrs.keys())
        for key in all_keys:
            v1 = self.extra_attrs.get(key, 0.0)
            v2 = other.extra_attrs.get(key, 0.0)
            result.extra_attrs[key] = v1 + v2
        return result

    def display_items(self) -> list[tuple[str, float, str]]:
        """返回用于展示的列表：[(显示名, 数值, 单位), ...]"""
        items = []
        for field_name, display_name, unit, _ in COMBAT_ATTR_FIELDS:
            value = getattr(self, field_name, 0.0)
            items.append((display_name, value, unit))
        # 动态字段
        for key, value in sorted(self.extra_attrs.items()):
            items.append((key, value, "%"))  # 动态字段默认百分比
        return items


def parse_percent(value: str) -> float:
    """解析百分比字符串为小数（如 '83.4%' → 0.834）"""
    value = value.strip().rstrip("%")
    try:
        return float(value) / 100.0
    except ValueError:
        return 0.0


def format_value(value: float, unit: str) -> str:
    """格式化数值（加单位）"""
    if unit == "%":
        return f"{value * 100:.2f}%"
    elif value == int(value):
        return str(int(value))
    else:
        return f"{value:.2f}"


# 词条名 → 战斗属性字段映射
# 用于将装备词条聚合到战斗属性
AFFIX_TO_ATTR: dict[str, str] = {
    # 外功攻击
    "最小外功攻击": "min_outer",
    "最大外功攻击": "max_outer",
    # 属攻（四种）
    "最小鸣金攻击": "min_mingjin",
    "最大鸣金攻击": "max_mingjin",
    "最小裂石攻击": "min_lieshi",
    "最大裂石攻击": "max_lieshi",
    "最小破竹攻击": "min_pozhu",
    "最大破竹攻击": "max_pozhu",
    "最小牵丝攻击": "min_qiansi",
    "最大牵丝攻击": "max_qiansi",
    "最小无相攻击": "min_wuxiang",
    "最大无相攻击": "max_wuxiang",
    # 三率（词条存储为百分比数值，如 8.34 表示 8.34%，需转换为 0.0834）
    "精准率": "precision",
    "会心率": "crit_rate",
    "会意率": "intent_rate",
    # 直接率
    "直接会心率": "direct_crit",
    "直接会意率": "direct_intent",
    # 伤害加成
    "会心伤害加成": "crit_dmg",
    "会意伤害加成": "intent_dmg",
    # 穿透
    "外功穿透": "outer_pen",
    "鸣金穿透": "mingjin_pen",
    "裂石穿透": "lieshi_pen",
    "破竹穿透": "pozhu_pen",
    "牵丝穿透": "qiansi_pen",
    "无相穿透": "wuxiang_pen",  # 定音词条，需要根据流派属性转换为属攻穿透
    # 增伤
    "全武学增效": "all_skill_bonus",
    "对首领单位增伤": "boss_bonus",
    "对玩家单位增效": "player_bonus",
    # 奇术增伤
    "单体类奇术增伤": "single_qs_bonus",
    "群体类奇术增伤": "group_qs_bonus",
    # 伤害加成
    "外功伤害加成": "outer_bonus",
    "鸣金伤害加成": "mingjin_bonus",
    "裂石伤害加成": "lieshi_bonus",
    "破竹伤害加成": "pozhu_bonus",
    "牵丝伤害加成": "qiansi_bonus",
}

# 动态词条（流派相关，映射到 extra_attrs）
# 这些词条名不是固定的，需要根据流派动态判断
DYNAMIC_AFFIX_PATTERNS = [
    # (词条名后缀, extra_attrs key)
    ("武学增伤", None),  # 如 "剑武学增伤" → extra_attrs["剑武学增伤"]
    ("武学增效", None),  # 如 "扇武学增效"
]


def convert_five_dims(jin: float = 0, shi: float = 0, min_val: float = 0,
                      ti: float = 0, yu: float = 0) -> CombatAttributes:
    """将五维属性转换为战斗属性

    转换公式：
    - 劲: 1点 → 0.246小外攻 + 1.315大外攻
    - 势: 1点 → 0.9大外攻 + 0.04%会意率
    - 敏: 1点 → 1小外攻 + 0.075%会心率
    - 体/御: 转换为生命值/防御（当前 CombatAttributes 不追踪）

    Returns:
        转换后的 CombatAttributes（仅包含 min_outer/max_outer/crit_rate/intent_rate）
    """
    result = CombatAttributes()
    result.min_outer = jin * JIN_TO_MIN_OUTER + min_val * MIN_TO_MIN_OUTER
    result.max_outer = jin * JIN_TO_MAX_OUTER + shi * SHI_TO_MAX_OUTER
    result.crit_rate = min_val * MIN_TO_CRIT_RATE
    result.intent_rate = shi * SHI_TO_INTENT_RATE
    return result


def map_affix_to_attr(affix_name: str) -> tuple[str | None, bool]:
    """将词条名映射到战斗属性字段

    Args:
        affix_name: 词条名（如 "最大外功攻击"、"剑武学增伤"）

    Returns:
        (field_name, is_percent):
        - field_name: 对应的战斗属性字段名，无法映射返回 None
        - is_percent: 该字段是否为百分比类型
    """
    # 1. 固定字段映射
    if affix_name in AFFIX_TO_ATTR:
        field_name = AFFIX_TO_ATTR[affix_name]
        # 判断是否为百分比字段
        for fn, _, unit, _ in COMBAT_ATTR_FIELDS:
            if fn == field_name:
                return field_name, (unit == "%")
        return field_name, False

    # 2. 指定技能定音：以游戏配置的词组归属为唯一权威来源。
    # 这类名称包含武学技、蓄力技、特殊技、重击、增疗等多种形式，
    # 不能依靠字符串后缀穷举。
    from ...config import get_game_config
    if get_game_config().resolve_affix_category(affix_name) == "指定技能增效":
        return affix_name, True

    # 3. 武器自身的武学增伤/增效动态字段。
    for suffix, _ in DYNAMIC_AFFIX_PATTERNS:
        if affix_name.endswith(suffix):
            # 动态字段存入 extra_attrs，key 为词条名本身
            return affix_name, True  # 动态字段默认为百分比

    return None, False


def apply_hypothetical_caps(
    equipped: dict,
    full_chengyin: bool = False,
    full_dingyin: bool = False,
    full_level: int = 0,
) -> dict:
    """假设装备升至理想状态，返回变换后的装备副本。

    Args:
        equipped: {slot_key: equip_dict}
        full_chengyin: 承音装备的普通词条 → 承音上限 (cap×0.94)
        full_dingyin: 所有装备的定音词条 → 上限 (cap, 100%)
        full_level: 目标等级（>0 时，低于该等级的装备升至该等级）
            仅升基础属性；词条/定音数值是否升级取决于 full_chengyin/full_dingyin。
            词条上限按升级后的等级查询。

    Returns:
        变换后的装备 dict；无需变换时返回原 dict。
    """
    if not full_chengyin and not full_dingyin and full_level <= 0:
        return equipped

    import copy

    from ...config import get_game_config

    gc = get_game_config()
    result: dict = {}

    for slot_key, equip in equipped.items():
        if not isinstance(equip, dict):
            result[slot_key] = equip
            continue

        equip = copy.deepcopy(equip)

        # 满等级：提升装备等级（基础属性随之变化）
        # 低于赛季最高等级的装备均可升级为承音装备
        if full_level > 0:
            try:
                cur_level = int(equip.get("level") or 0)
            except (TypeError, ValueError):
                cur_level = 0
            if 0 < cur_level < full_level:
                equip["level"] = full_level
                equip["is_chengyin"] = True

        effective_level = equip.get("level")
        is_cy = equip.get("is_chengyin", False)

        # 普通词条 affix_1~5
        for i in range(1, 6):
            affix = equip.get(f"affix_{i}")
            if not affix or not isinstance(affix, dict) or not affix.get("name"):
                continue
            if full_chengyin and is_cy and effective_level:
                caps = gc.get_affix_caps(effective_level, affix["name"])
                if caps:
                    affix["value"] = caps["chengyin"]

        # 定音词条
        dingyin = equip.get("dingyin")
        if full_dingyin and dingyin and isinstance(dingyin, dict) and dingyin.get("name") and effective_level:
            caps = gc.get_affix_caps(effective_level, dingyin["name"])
            if caps:
                dingyin["value"] = caps["cap"]

        result[slot_key] = equip

    return result


def aggregate_equipment_attrs(equipped: dict) -> CombatAttributes:
    """聚合装备属性到战斗属性

    Args:
        equipped: 用户装备数据，格式为 {slot_key: equip_dict}
                  equip_dict 包含 affix_1~5 和 dingyin

    Returns:
        聚合后的装备属性（不含基础属性和弓玦）

    五维处理：劲/势/敏/体/御 词条先累计总值，再统一转换为战斗属性
    （转换后的值已包含在返回结果中，反推基础属性时自然扣除）
    """
    result = CombatAttributes()
    # 五维累计（最后统一转换）
    five_dims = {"劲": 0.0, "势": 0.0, "敏": 0.0, "体": 0.0, "御": 0.0}

    for _slot_key, equip in equipped.items():
        if not isinstance(equip, dict):
            continue

        # 处理 affix_1 ~ affix_5
        for i in range(1, 6):
            affix = equip.get(f"affix_{i}")
            if not affix or not isinstance(affix, dict):
                continue

            name = affix.get("name", "")
            value = affix.get("value", 0)
            if not name or value == 0:
                continue

            # 五维词条：累计，稍后统一转换
            if name in FIVE_DIM_NAMES:
                five_dims[name] += value
                continue

            field_name, is_percent = map_affix_to_attr(name)
            if field_name is None:
                continue

            # 百分比词条需要转换为小数
            if is_percent:
                value = value / 100.0

            # 累加到对应字段
            if hasattr(result, field_name):
                current = getattr(result, field_name, 0.0)
                setattr(result, field_name, current + value)
            else:
                # 动态字段
                current = result.extra_attrs.get(field_name, 0.0)
                result.extra_attrs[field_name] = current + value

        # 处理定音词条（dingyin）
        dingyin = equip.get("dingyin")
        if dingyin and isinstance(dingyin, dict):
            name = dingyin.get("name", "")
            value = dingyin.get("value", 0)
            if name and value != 0:
                # 定音一般不会有五维，但为完整性处理
                if name in FIVE_DIM_NAMES:
                    five_dims[name] += value
                    continue
                field_name, is_percent = map_affix_to_attr(name)
                if field_name:
                    if is_percent:
                        value = value / 100.0
                    if hasattr(result, field_name):
                        current = getattr(result, field_name, 0.0)
                        setattr(result, field_name, current + value)
                    else:
                        current = result.extra_attrs.get(field_name, 0.0)
                        result.extra_attrs[field_name] = current + value

    # 五维转换：劲/势/敏 → 外功攻击/会心/会意
    if any(v > 0 for v in five_dims.values()):
        five_dim_attrs = convert_five_dims(
            jin=five_dims["劲"], shi=five_dims["势"],
            min_val=five_dims["敏"], ti=five_dims["体"],
            yu=five_dims["御"],
        )
        result = result + five_dim_attrs

    return result


# 装备部位 → base_attrs key 映射
# 只有 weapon/ring/pendant 提供外功攻击
_SLOT_TO_BASE_KEY = {
    "main_weapon": "weapon",
    "sub_weapon": "weapon",
    "ring": "ring",
    "pendant": "pendant",
}


def compute_equip_base_attrs(equipped: dict, base_attr_lookup) -> CombatAttributes:
    """计算装备基础外功攻击值（根据部位/等级/品阶）

    武器提供外功攻击区间（min_outer, max_outer）
    环提供最小外功攻击
    佩提供最大外功攻击

    Args:
        equipped: 用户装备数据 {slot_key: equip_dict}
        base_attr_lookup: 查询函数 (part, level, quality) -> (min_val, max_val) | (None, None)

    Returns:
        CombatAttributes 包含基础外功攻击值
    """
    result = CombatAttributes()

    for slot_key, equip in equipped.items():
        if not isinstance(equip, dict):
            continue
        base_key = _SLOT_TO_BASE_KEY.get(slot_key)
        if not base_key:
            continue

        level = equip.get("level", 0)
        if isinstance(level, str):
            try:
                level = int(level)
            except (ValueError, TypeError):
                level = 0
        quality = equip.get("quality", "")
        if not level or not quality:
            continue

        min_val, max_val = base_attr_lookup(base_key, level, quality)
        if min_val is None:
            continue

        if base_key == "weapon":
            # 武器：外功攻击区间
            result.min_outer += min_val
            result.max_outer += max_val
        elif base_key == "ring":
            # 环：最小外功攻击
            result.min_outer += min_val
        elif base_key == "pendant":
            # 佩：最大外功攻击
            result.max_outer += max_val

    return result


@dataclass(frozen=True)
class GraduationAttrContext:
    """毕业率属性预处理所需的不可变配置快照。"""

    judge_resistance: float
    buff_resistance: float
    target_pen_field: str | None

    @classmethod
    def from_school(cls, school: str) -> "GraduationAttrContext":
        from ...config import get_game_config

        gc = get_game_config()
        configs = gc.get_level_configs()
        if configs:
            level_cfg = max(configs, key=lambda item: item.level)
            judge_resistance = float(level_cfg.judge_resistance or 0)
            buff_resistance = float(level_cfg.buff_resistance or 0)
        else:
            judge_resistance = buff_resistance = 0.0
        school_attr = gc.get_school_attr(school) if school else None
        return cls(
            judge_resistance=judge_resistance,
            buff_resistance=buff_resistance,
            target_pen_field=WUXIANG_TO_ATTR_PEN.get(school_attr or ""),
        )


def build_graduation_attrs(
    base_attrs: CombatAttributes,
    equipment_attrs: CombatAttributes,
    school: str,
    *,
    context: GraduationAttrContext | None = None,
) -> CombatAttributes:
    """构造毕业率计算唯一输入：原始属性合并后统一应用抗性规则。

    ``equipment_attrs`` 可同时包含装备基础攻击和装备词条；只有穿透字段
    需要区分基础值与装备值，装备基础攻击不会影响该区分。
    """
    context = context or GraduationAttrContext.from_school(school)
    judge_resistance = context.judge_resistance
    buff_resistance = context.buff_resistance
    result = base_attrs + equipment_attrs
    target_pen = context.target_pen_field

    for field_name in THREE_RATE_FIELDS:
        setattr(result, field_name, apply_three_rate_resistance(
            field_name, getattr(result, field_name), judge_resistance,
        ))
    for field_name in BONUS_PERCENT_FIELDS:
        setattr(result, field_name, apply_bonus_resistance(
            getattr(result, field_name), resistance=buff_resistance,
        ))
    for field_name in PENETRATION_FIELDS:
        equipment_value = getattr(equipment_attrs, field_name)
        if field_name == target_pen:
            equipment_value += equipment_attrs.wuxiang_pen
        setattr(result, field_name, apply_penetration_resistance(
            equipment_value,
            getattr(base_attrs, field_name),
            buff_resistance,
        ))
    result.extra_attrs = {
        key: apply_bonus_resistance(value, resistance=buff_resistance)
        if has_resistance(key) else value
        for key, value in result.extra_attrs.items()
    }
    return result


# ─── 抗性计算 ─────────────────────────────────────────────

def is_three_rate_field(field_name: str) -> bool:
    """判断是否为三率字段"""
    return field_name in THREE_RATE_FIELDS


def is_penetration_field(field_name: str) -> bool:
    """判断是否为穿透字段（基础值不受抗性，装备值除以除数）"""
    return field_name in PENETRATION_FIELDS


def is_bonus_percent_field(field_name: str) -> bool:
    """判断是否为增伤类增效字段（整个值除以除数）"""
    if field_name in BONUS_PERCENT_FIELDS:
        return True
    from ...config import get_game_config
    if get_game_config().resolve_affix_category(field_name) == "指定技能增效":
        return True
    # 动态增效字段
    for suffix in BONUS_SUFFIXES:
        if field_name.endswith(suffix):
            return True
    return False


def has_resistance(field_name: str) -> bool:
    """判断字段是否有抗性机制"""
    return (is_three_rate_field(field_name) or
            is_penetration_field(field_name) or
            is_bonus_percent_field(field_name))


def apply_three_rate_resistance(
    field_name: str,
    value: float,
    resistance: float = THREE_RATE_RESISTANCE,
) -> float:
    """应用三率抗性

    精准率: 生效 = 基础值 + (面板 - 基础值) / 2.45
    会心率: 生效 = 面板 / 2.45，上限 80%
    会意率: 生效 = 面板 / 2.45，上限 40%

    Args:
        field_name: 字段名（precision/crit_rate/intent_rate）
        value: 原始值（小数形式，如 1.463 表示 146.3%）

    Returns:
        抗性后的生效值（小数形式）
    """
    if field_name == "precision":
        # 精准率：基础值 + (面板 - 基础值) / 2.45
        base = PRECISION_BASE / 100  # 转换为小数
        if value <= base:
            return value
        return base + (value - base) / (1 + resistance / 100)

    elif field_name == "crit_rate":
        # 会心率：面板 / 2.45，上限 80%
        effective = value / (1 + resistance / 100)
        return min(effective, CRIT_RATE_CAP)

    elif field_name == "intent_rate":
        # 会意率：面板 / 2.45，上限 40%
        effective = value / (1 + resistance / 100)
        return min(effective, INTENT_RATE_CAP)

    return value


def apply_bonus_resistance(
    value: float,
    base: float = 0.0,
    resistance: float = BONUS_RESISTANCE,
) -> float:
    """应用增效抗性

    生效值 = 基础值 + 装备值 / 1.15

    Args:
        value: 装备值（小数形式）
        base: 基础值（不受抗性影响）

    Returns:
        抗性后的生效值
    """
    return base + value / (1 + resistance / 100)


def apply_penetration_resistance(
    equipment_value: float,
    base: float = 0.0,
    resistance: float = BONUS_RESISTANCE,
) -> float:
    """应用穿透抗性

    穿透公式：生效 = 基础值 + 装备定音 / 1.15

    基础值不受抗性影响，只有装备定音部分需要计算抗性。

    Args:
        equipment_value: 装备定音提供的穿透值
        base: 基础值（从玩法配置读取，不受抗性影响）

    Returns:
        抗性后的生效值
    """
    return base + equipment_value / (1 + resistance / 100)


def compute_gongjue_attrs(gongjue_type: str, equip_level: int,
                          affix_caps_lookup) -> CombatAttributes:
    """计算弓玦属性：当前赛季最大等级三率词条上限的一半

    弓玦套装本质是给玩家凑半条词条用的。

    Args:
        gongjue_type: 弓玦类型（"会意"/"精准"/"会心"/""）
        equip_level: 当前赛季装备等级
        affix_caps_lookup: 查询函数 (level, affix_name) -> {"cap": float} | None
    """
    result = CombatAttributes()
    if not gongjue_type:
        return result

    type_to_affix = {"会意": "会意率", "会心": "会心率", "精准": "精准率"}
    attr_field = {"会意": "intent_rate", "会心": "crit_rate", "精准": "precision"}

    affix_name = type_to_affix.get(gongjue_type)
    field_name = attr_field.get(gongjue_type)
    if not affix_name or not field_name:
        return result

    cap_data = affix_caps_lookup(equip_level, affix_name)
    if cap_data is None:
        logger.warning(f"弓玦属性计算：未找到 {affix_name} Lv{equip_level} 的词条上限")
        return result

    # 弓玦 = 单条词条上限的一半（百分比转小数）
    half_cap = cap_data["cap"] / 2.0 / 100.0
    setattr(result, field_name, half_cap)
    return result


def get_resistance_info(field_name: str) -> dict | None:
    """获取字段的抗性信息，无抗性返回 None

    Returns:
        {"type": "three_rate" | "penetration" | "bonus", "divisor": float, "base": float, "cap": float | None}
    """
    if field_name == "precision":
        return {
            "type": "three_rate",
            "divisor": THREE_RATE_DIVISOR,
            "base": PRECISION_BASE / 100,
            "cap": None,
            "desc": f"基础{PRECISION_BASE}% + 超出部分/{THREE_RATE_DIVISOR}"
        }
    elif field_name == "crit_rate":
        return {
            "type": "three_rate",
            "divisor": THREE_RATE_DIVISOR,
            "base": 0.0,
            "cap": CRIT_RATE_CAP,
            "desc": f"面板/{THREE_RATE_DIVISOR}，上限{CRIT_RATE_CAP*100:.0f}%"
        }
    elif field_name == "intent_rate":
        return {
            "type": "three_rate",
            "divisor": THREE_RATE_DIVISOR,
            "base": 0.0,
            "cap": INTENT_RATE_CAP,
            "desc": f"面板/{THREE_RATE_DIVISOR}，上限{INTENT_RATE_CAP*100:.0f}%"
        }
    elif field_name in PENETRATION_FIELDS:
        return {
            "type": "penetration",
            "divisor": BONUS_DIVISOR,
            "base": 0.0,  # 基础值需要从玩法数据获取
            "cap": None,
            "desc": f"基础值 + 装备值/{BONUS_DIVISOR}"
        }
    elif is_bonus_percent_field(field_name):
        return {
            "type": "bonus",
            "divisor": BONUS_DIVISOR,
            "base": 0.0,
            "cap": None,
            "desc": f"面板/{BONUS_DIVISOR}"
        }
    return None
