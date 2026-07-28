"""调律规则领域模型与固定词汇

规则中的全部词条引用一律使用标准词条名（attributes.yaml 普通词组
_aliases 全集，经 GameConfigManager.get_normal_affix_names() 提供）。

schema 要点：
- playstyles: 名字 → {main/sub: {weapon, damage}, attr}，damage 为
  具体增伤词条名或 null（不需要增伤），attr 为玩法属性（属性攻击
  词组组名，通用/鸣金/牵丝/裂石/破竹）；判定武器部位时按用户勾选
  的名字展开尝试，装备武器名匹配主/副武器即产生一次判定；非武器
  部位判定时把玩法属性对应的属攻视作无相词条（属攻→无相等价）；
- patterns.<部位>: first + 三档条件 junk/usable/top_conditions，
  每档为「条件组」列表：组间 OR（任一组命中即触发该档）、组内
  AND；单个条件 dict 视作单条件组。判定顺序 junk → usable → top，
  全不命中默认「优秀」。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ─── 固定词汇（写死在代码，不进 YAML） ─────────────────────

# PVP 词条（全局 keep_pvp 开启时按部位做等价处理）；
# 实际取值由 tuning_base.yaml 提供，此处仅为兜底默认
PVP_NAMES = {"单体类奇术增伤", "对玩家单位增效"}

# 属性攻击词组类别（玩法属性候选 + 属攻→无相等价的数据源）
ATTR_ATTACK_CATEGORY = "属性攻击"
# 通用属性（其属攻即无相攻击，作为等价转换的目标）
GENERIC_ATTR = "通用"

# 条件原语类型
COND_KINDS = {"not_contains", "contains_all", "not_together",
              "count_max", "count_min"}

# 部位归并：佩→环、胸甲→冠胄、腕甲→胫甲
PART_ALIAS = {"佩": "环", "胸甲": "冠胄", "腕甲": "胫甲"}

# 模式部位 key 全集
PART_KEYS = ("主武器", "副武器", "环", "冠胄", "胫甲")

# 品阶门槛部位全集（固定 7 项，与 equip_parser.infer_part 输出对齐）
QUALITY_PARTS = ("武器", "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲")


def standard_affix_names() -> list[str]:
    """标准词条全集（普通词组 _aliases 并集，按 YAML 声明序）"""
    from ...game_config import get_game_config
    return get_game_config().get_normal_affix_names()


def standard_playstyle_attrs() -> list[str]:
    """玩法属性候选（属性攻击词组的组名，通用置首）"""
    from ...game_config import get_game_config
    groups = get_game_config().get_alias_groups(ATTR_ATTACK_CATEGORY)
    names = list(groups.keys())
    if GENERIC_ATTR in names:
        names.remove(GENERIC_ATTR)
        names.insert(0, GENERIC_ATTR)
    return names


def attr_equivalence(attr: str) -> dict[str, str]:
    """属攻→无相等价映射：玩法属性 attr 的最大/最小属攻 → 通用无相攻击

    attr 为通用/空/未知时返回空 dict（无需转换）。映射按属性攻击
    词组内的声明序位置对齐（最大↔最大、最小↔最小）。
    """
    if not attr or attr == GENERIC_ATTR:
        return {}
    from ...game_config import get_game_config
    groups = get_game_config().get_alias_groups(ATTR_ATTACK_CATEGORY)
    generic = groups.get(GENERIC_ATTR) or []
    specific = groups.get(attr) or []
    return {s: generic[i] for i, s in enumerate(specific) if i < len(generic)}


# ─── 规则数据结构 ──────────────────────────────────────────

@dataclass
class Condition:
    """条件原语（三档条件组内 AND）

    symbols 为标准词条名列表。
    - not_contains: 非首词条未出现任一 symbols
    - contains_all: 非首词条必须同时出现全部 symbols
    - not_together: symbols（恰 2 个）不同时出现
    - count_max:    symbols 计数 ≤ max（include_first 时含首词条）
    - count_min:    symbols 计数 ≥ min 即触发
    """
    kind: str
    symbols: list[str]
    max: int = 0
    min: int = 0
    include_first: bool = False

    def _count(self, first_token: str, tokens: list[str]) -> int:
        count = sum(1 for t in tokens if t in self.symbols)
        if self.include_first and first_token in self.symbols:
            count += 1
        return count

    def check(self, first_token: str, tokens: list[str]) -> bool:
        """条件是否成立（tokens 为非首词条名列表）"""
        s = set(tokens)
        if self.kind == "not_contains":
            return not (s & set(self.symbols))
        if self.kind == "contains_all":
            return set(self.symbols) <= s
        if self.kind == "not_together":
            return not (set(self.symbols) <= s)
        count = self._count(first_token, tokens)
        if self.kind == "count_max":
            return count <= self.max
        if self.kind == "count_min":
            return count >= self.min
        return False

    def potential(self, first_token: str, tokens: list[str],
                  n_avail: int) -> bool:
        """潜力求值：剩余 n_avail 张牌能否使条件仍有机会成立

        排除类条件（not_contains/not_together/count_max）当前已满足
        即可（空槽按最优填法不会引入排除词条）；contains_all 缺失数
        不超过可补牌数即可。
        """
        if self.kind == "contains_all":
            missing = set(self.symbols) - set(tokens)
            return len(missing) <= n_avail
        return self.check(first_token, tokens)

    def still_hits(self, first_token: str, tokens: list[str],
                   n_avail: int) -> bool:
        """潜力求值：n_avail 张万能牌按最优填法能否解除命中

        junk/usable 条件专用——补牌只增不减：
        - contains_all/count_min: 命中后加词条不会反转 → 维持命中；
        - not_contains: 补 1 个 symbols 内词条即可解除；
        - not_together: 补齐 2 词条同现即可解除；
        - count_max: 补 symbols 内词条至超出上限即可解除。
        """
        if not self.check(first_token, tokens):
            return False
        if self.kind in ("contains_all", "count_min"):
            return True
        if self.kind == "not_contains":
            return n_avail < 1
        if self.kind == "not_together":
            missing = len(set(self.symbols) - set(tokens))
            return missing > n_avail
        # count_max：需补 max+1-count 个才能突破上限
        count = self._count(first_token, tokens)
        return (self.max + 1 - count) > n_avail


@dataclass
class WeaponSide:
    """武器规则单侧（主或副）：武器名 + 增伤词条名（None = 不需要）"""
    weapon: str
    damage: str | None = None


@dataclass
class Playstyle:
    """玩法设定（如 纯唐/双切）：规定主/副武器、各自增伤要求与玩法属性

    attr 为属性攻击词组组名（通用/鸣金/牵丝/裂石/破竹），判定非武器
    部位时把该属性的属攻视作无相词条。
    """
    name: str
    main: WeaponSide
    sub: WeaponSide
    attr: str = GENERIC_ATTR

    def summary(self) -> str:
        """UI 摘要文案"""
        return f"主 {self.main.weapon} / 副 {self.sub.weapon}"


@dataclass
class PartPattern:
    """单部位模式：首词条 + 三档条件

    三档均为条件组列表（组间 OR、组内 AND），判定顺序
    junk → usable → top，全不命中默认「优秀」。
    """
    first: list[str]
    junk_conditions: list[list[Condition]] = field(default_factory=list)
    usable_conditions: list[list[Condition]] = field(default_factory=list)
    top_conditions: list[list[Condition]] = field(default_factory=list)


@dataclass
class TuningRule:
    """单条调律规则（一个 YAML 文件，对应 UI 一个 Tab）"""
    key: str
    name: str
    order: int = 100
    playstyles: dict[str, Playstyle] = field(default_factory=dict)
    transmute_priority: list[str] = field(default_factory=list)
    affix_pool: list[str] = field(default_factory=list)
    patterns: dict[str, PartPattern] = field(default_factory=dict)
    # 品阶门槛覆盖（部位 → 允许品阶；未列部位沿用全局 tuning_base）
    quality_thresholds: dict[str, list[str]] = field(default_factory=dict)

    @property
    def pool_set(self) -> set[str]:
        return set(self.affix_pool)

    # ── UI 元数据接口 ──

    @property
    def implemented(self) -> bool:
        return True

    @property
    def playstyle_options(self) -> dict[str, str]:
        """玩法名字 → 摘要（UI 勾选项）"""
        return {name: ps.summary() for name, ps in self.playstyles.items()}


# ─── 基础配置（品阶门槛 + PVP 等价，全局） ──────────────

@dataclass
class PvpPartRule:
    """单部位 PVP 等价处理

    - substitutions: 词条替换（源词条 → 目标词条），仅当源词条
      不在当前词条库时生效（否则词条已合法无需等价）；
    - add_to_pool: 临时并入词条库的词条。
    """
    substitutions: dict[str, str] = field(default_factory=dict)
    add_to_pool: list[str] = field(default_factory=list)


@dataclass
class TuningBase:
    """全局基础配置（品阶门槛 + PVP 词条集合与部位等价）"""
    quality_thresholds: dict[str, list[str]] = field(default_factory=dict)
    pvp_names: set[str] = field(default_factory=set)
    pvp_parts: dict[str, PvpPartRule] = field(default_factory=dict)

    def quality_ok(self, part: str, quality: str | None,
                   overrides: dict[str, list[str]] | None = None) -> bool:
        """品阶筛选：按标准部位名（QUALITY_PARTS）取允许品阶；
        规则级 overrides 中列出的部位优先于全局配置"""
        allowed = (overrides or {}).get(part)
        if allowed is None:
            allowed = self.quality_thresholds.get(part, [])
        return quality in allowed


class RuleValidationError(ValueError):
    """规则 schema 校验失败"""
