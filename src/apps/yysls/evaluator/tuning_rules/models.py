"""调律规则领域模型与固定词汇

规则中的全部词条引用一律使用规则可引用词表（rule_affix_candidates：
标准词条全集 + 四个动态词条）。具体属攻词条（如 最大裂石攻击）
为字面精确引用，动态词条为跨属性泛化引用，两者均合法；最大/最小
无相攻击为字面语义（仅匹配真实无相词条，游戏事实：仅武器
掉落）。

schema 要点：
- playstyles: 名字 → {main/sub: {weapon, damage}, attr}，damage 为
  具体增伤词条名或 null（不需要增伤），attr 为玩法属性（属性攻击
  词组组名，通用/鸣金/牵丝/裂石/破竹）；判定武器部位时按用户勾选
  的名字展开尝试，装备武器名匹配主/副武器即产生一次判定；非武器
  部位判定时装备具体属攻额外获得动态词条身份（字面名与归类名
  双重匹配，本属 = 该属性、外属 = 其余属性，见 dynamic_affix_map）；
  attr=通用（混搭流）不做任何归类，且规则禁止引用动态词条；
- patterns.<部位>: first + 四档条件 junk/normal/excellent/top_conditions，
  每档为「条件组」列表：组间 OR（任一组命中即触发该档）、组内
  AND；单个条件 dict 视作单条件组，{when, all} 形态可绑定开关前提
  （when 全部匹配时条件组才参与判定）。判定顺序
  junk → normal → excellent → top，全不命中取 default_rating；
- common_conditions: 通用判定（规则级四档条件，键同上四档），无
  首词条/默认判定，判定时逐档并入所有部位的条件组（通用在前）；
- default_rating: 四档 key（junk/normal/excellent/top）之一，缺省
  excellent；patterns.<部位> 可选同名字段按部位覆盖；
- affix_pool: 可用词条库（全局），声明序即价值序（越靠前越优先
  保留与填充），潜力判定据此填充空槽；transmute_priority 独立
  （转律只能转出库内词条，转入取库中最高优先级）；字面无相与
  动态词条可在库内并存（经部位过滤互不干扰）；
- 开关注册表在 tuning_base.yaml 的 switches 段（key → {name}），
  条件组 when 只能引用已注册开关。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ─── 固定词汇（写死在代码，不进 YAML） ─────────────────────

# 属性攻击词组类别（玩法属性候选 + 属攻→无相等价的数据源）
ATTR_ATTACK_CATEGORY = "属性攻击"
# 通用属性（其属攻即无相攻击，混搭流玩法不做动态归类）
GENERIC_ATTR = "通用"

# 动态词条（规则层词汇，非 attributes.yaml 真实词条）：判定时按
# 玩法属性把装备上的具体属攻归类为 本属（=该属性）/外属（=其余
# 属性，多对一），大/小对齐词组内声明序（最大↔大、最小↔小）
DYNAMIC_AFFIXES = ("最大本属攻击", "最小本属攻击",
                   "最大外属攻击", "最小外属攻击")
# 动态词条在规则编辑器中的归属分类名（与属攻类并列，不入
# game_config 归属体系）
DYNAMIC_CATEGORY = "动态类"

# 条件原语类型
COND_KINDS = {"contains_all", "not_together", "count_max", "count_min"}

# 评级档位 key（判定顺序 junk → normal → excellent → top）
RATING_KEYS = ("junk", "normal", "excellent", "top")
# 四档条件字段 key（patterns.<部位> 与 common_conditions 共用）
TIER_KEYS = ("junk_conditions", "normal_conditions",
             "excellent_conditions", "top_conditions")
# 评级档位显示名
RATING_LABELS = {"junk": "垃圾", "normal": "一般",
                 "excellent": "优秀", "top": "顶级"}

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


def specific_attr_names() -> list[str]:
    """具体属攻词条全集（属性攻击词组非通用组并集，按声明序）

    即动态词条归类映射（dynamic_affix_map）的源词条全集。
    """
    from ...game_config import get_game_config
    groups = get_game_config().get_alias_groups(ATTR_ATTACK_CATEGORY)
    return [s for name, names in groups.items()
            if name != GENERIC_ATTR for s in names]


def rule_affix_candidates() -> list[str]:
    """规则可引用词条全集（校验词表与编辑器候选的唯一来源）

    标准词条全集（含具体属攻，字面精确引用合法）+ 动态词条，
    动态词条插在无相词条之后（保持价值语境相邻；无相不在全集时
    追加末尾）。
    """
    from ...game_config import get_game_config
    names = list(standard_affix_names())
    groups = get_game_config().get_alias_groups(ATTR_ATTACK_CATEGORY)
    generic = groups.get(GENERIC_ATTR) or []
    positions = [names.index(g) for g in generic if g in names]
    insert_at = (max(positions) + 1) if positions else len(names)
    names[insert_at:insert_at] = list(DYNAMIC_AFFIXES)
    return names


def dynamic_affix_map(attr: str) -> dict[str, str]:
    """属攻→动态词条归类映射：玩法属性 attr 视角下的额外身份

    attr 的最大/最小属攻 → 最大/最小本属攻击，其余属性的最大/
    最小属攻 → 最大/最小外属攻击（多对一）；判定时装备词条同时
    以字面名与归类名参与匹配（双重身份，非破坏性改写）。attr 为
    通用/空/未知时返回空 dict（不归类，规则中的动态词条永不匹配）。
    大/小按属性攻击词组内声明序位置对齐（第 1 个=最大、第 2 个=
    最小）；无相词条不参与归类（字面语义，仅武器掉落）。
    """
    if not attr or attr == GENERIC_ATTR:
        return {}
    from ...game_config import get_game_config
    groups = get_game_config().get_alias_groups(ATTR_ATTACK_CATEGORY)
    if attr not in groups:
        return {}
    mapping: dict[str, str] = {}
    for name, names in groups.items():
        if name == GENERIC_ATTR:
            continue
        big, small = (DYNAMIC_AFFIXES[0:2] if name == attr
                      else DYNAMIC_AFFIXES[2:4])
        for i, s in enumerate(names[:2]):
            mapping[s] = big if i == 0 else small
    return mapping


# ─── 规则数据结构 ──────────────────────────────────────────

@dataclass
class Condition:
    """条件原语（条件组内 AND）

    symbols 为规则可引用词条名列表；include_first=True 时首词条
    参与判断。alias 为词条别名映射（字面名→动态归类名，双重
    身份）：任一身份命中 symbols 即计入，每条词条至多计 1 次。
    - contains_all: 必须同时出现（全部 symbols 各自出现，集合语义）
    - not_together: 不得同时出现（symbols（≥2 个）全部同现即违反）
    - count_max:    计数不得超过（symbols 计数 ≤ max，max=0 即
                    「未出现任一」）
    - count_min:    计数不得低于（symbols 计数 ≥ min 即触发）
    """
    kind: str
    symbols: list[str]
    max: int = 0
    min: int = 0
    include_first: bool = False

    def _present(self, first_token: str, tokens: list[str],
                 alias: dict[str, str]) -> set[str]:
        s = set(tokens)
        if self.include_first:
            s.add(first_token)
        s |= {alias[t] for t in tuple(s) if t in alias}
        return s

    def _count(self, first_token: str, tokens: list[str],
               alias: dict[str, str]) -> int:
        def hit(t: str) -> bool:
            return t in self.symbols or alias.get(t) in self.symbols
        count = sum(1 for t in tokens if hit(t))
        if self.include_first and hit(first_token):
            count += 1
        return count

    def check(self, first_token: str, tokens: list[str],
              alias: dict[str, str] | None = None) -> bool:
        """条件是否成立（tokens 为非首词条名列表）"""
        alias = alias or {}
        if self.kind == "contains_all":
            return set(self.symbols) <= self._present(
                first_token, tokens, alias)
        if self.kind == "not_together":
            return not (set(self.symbols)
                        <= self._present(first_token, tokens, alias))
        count = self._count(first_token, tokens, alias)
        if self.kind == "count_max":
            return count <= self.max
        if self.kind == "count_min":
            return count >= self.min
        return False


@dataclass
class ConditionGroup:
    """条件组（组内 AND）：可选开关前提 when（开关 key → 期望值）

    when 全部匹配（未配置的开关视作 False）时条件组才参与判定。
    """
    conditions: list[Condition] = field(default_factory=list)
    when: dict[str, bool] = field(default_factory=dict)

    def active(self, switches: dict[str, bool]) -> bool:
        """按当前开关状态判断条件组是否参与判定"""
        return all(bool(switches.get(k, False)) == v
                   for k, v in self.when.items())


@dataclass
class WeaponSide:
    """武器规则单侧（主或副）：武器名 + 增伤词条名（None = 不需要）"""
    weapon: str
    damage: str | None = None


@dataclass
class Playstyle:
    """玩法设定（如 纯唐/双切）：规定主/副武器、各自增伤要求与玩法属性

    attr 为属性攻击词组组名（通用/鸣金/牵丝/裂石/破竹），判定非武器
    部位时装备具体属攻按该属性额外获得动态词条身份（见 dynamic_affix_map）。
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
    """单部位模式：首词条 + 四档条件

    四档均为条件组列表（组间 OR、组内 AND，条件组可带开关前提），
    判定顺序 junk → normal → excellent → top，全不命中取默认判定
    （部位级 default_rating 优先，None = 跟随规则级）。
    """
    first: list[str]
    junk_conditions: list[ConditionGroup] = field(default_factory=list)
    normal_conditions: list[ConditionGroup] = field(default_factory=list)
    excellent_conditions: list[ConditionGroup] = field(default_factory=list)
    top_conditions: list[ConditionGroup] = field(default_factory=list)
    # 部位级默认判定覆盖（RATING_KEYS 之一；None = 跟随规则级）
    default_rating: str | None = None


@dataclass
class CommonConditions:
    """通用判定：规则级四档条件，对所有部位生效

    无首词条/默认判定，判定时逐档并入各部位模式的条件组
    （通用条件组在前，组间仍为 OR）。
    """
    junk_conditions: list[ConditionGroup] = field(default_factory=list)
    normal_conditions: list[ConditionGroup] = field(default_factory=list)
    excellent_conditions: list[ConditionGroup] = field(default_factory=list)
    top_conditions: list[ConditionGroup] = field(default_factory=list)


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
    # 通用判定（规则级四档条件，对所有部位生效）
    common: CommonConditions = field(default_factory=CommonConditions)
    # 四档条件全不命中时的默认判定（RATING_KEYS 之一）
    default_rating: str = "excellent"
    # 品阶门槛覆盖（部位 → 允许品阶；未列部位沿用全局 tuning_base）
    quality_thresholds: dict[str, list[str]] = field(default_factory=dict)

    @property
    def pool_set(self) -> set[str]:
        return set(self.affix_pool)

    def referenced_switches(self) -> set[str]:
        """全部条件组 when 引用的开关 key 集合（含通用判定）"""
        keys: set[str] = set()
        for holder in (self.common, *self.patterns.values()):
            for tier_key in TIER_KEYS:
                for group in getattr(holder, tier_key):
                    keys.update(group.when)
        return keys

    def referenced_affixes(self) -> set[str]:
        """规则引用的全部词条名（库/转律/first/四档条件/增伤）"""
        names: set[str] = set(self.affix_pool) | set(self.transmute_priority)
        for ps in self.playstyles.values():
            for side in (ps.main, ps.sub):
                if side.damage:
                    names.add(side.damage)
        for pattern in self.patterns.values():
            names.update(pattern.first)
        for holder in (self.common, *self.patterns.values()):
            for tier_key in TIER_KEYS:
                for group in getattr(holder, tier_key):
                    for cond in group.conditions:
                        names.update(cond.symbols)
        return names

    # ── UI 元数据接口 ──

    @property
    def implemented(self) -> bool:
        return True

    @property
    def playstyle_options(self) -> dict[str, str]:
        """玩法名字 → 摘要（UI 勾选项）"""
        return {name: ps.summary() for name, ps in self.playstyles.items()}


# ─── 基础配置（品阶门槛 + 开关注册表，全局） ──────────────

@dataclass
class TuningBase:
    """全局基础配置（品阶门槛 + 开关注册表）

    switches: 开关 key → 显示名；规则条件组 when 只能引用已注册
    开关，主窗口按注册表渲染全局开关复选框。
    """
    quality_thresholds: dict[str, list[str]] = field(default_factory=dict)
    switches: dict[str, str] = field(default_factory=dict)

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
