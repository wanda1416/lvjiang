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
from typing import Callable

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


# ─── 基础配置（品阶门槛 + 开关注册表 + 材料设置，全局） ──────

# 大律准石（数量检查目标）与狗粮材料 label（须与 references.yaml
# 调律材料组的参考图 label 严格一致，材料识别按 label 精确匹配）
STONE_LABEL = "大律准石"
FOOD_LABELS = ("金狗粮", "紫狗粮", "彩狗粮")
# 评级档位序（狗粮规则「期望 ≥」比较）
RATING_RANK = {"junk": 0, "normal": 1, "excellent": 2, "top": 3}
# 狗粮规则可选的期望档位（能进调律的装备至少优秀，
# 一般≈不限；垃圾档无意义不开放）
FOOD_EXPECT_KEYS = ("top", "excellent", "normal")
# 品阶序与展示名（狗粮规则「品阶 ≥」比较；蓝=不限）
# 行为规则品阶条件：gold=不限（≤金色即全部），gold_only=仅金装，
# purple_only=仅紫装（精确），purple=紫装及以下（≤紫色），
# blue=蓝装及以下（≤蓝色）
QUALITY_RANK = {"blue": 0, "purple": 1, "gold": 2,
                "gold_only": 3, "purple_only": 4}
QUALITY_LABELS = {"gold": "金色", "purple": "紫色", "blue": "蓝色",
                  "gold_only": "仅金色", "purple_only": "仅紫色"}
# 狗粮规则品阶标签（仅含阈值语义的品阶，不含精确匹配品阶）
_FOOD_QUALITY_LABELS = {"gold": "金色", "purple": "紫色", "blue": "蓝色"}
# 材料不足时的行为：continue=继续调律（不添加狗粮），skip=跳过该装备
INSUFFICIENT_ACTIONS = ("continue", "skip")
INSUFFICIENT_LABELS = {"continue": "继续调律", "skip": "跳过该装备"}
# 大律准石不足时的处理：skip=跳过该装备（继续遍历），abort=结束
# 全部调律，ask=confirm 弹窗询问（确认继续后本次运行不再检查）
STONE_ACTIONS = ("skip", "abort", "ask")
STONE_ACTION_LABELS = {"skip": "跳过该装备", "abort": "结束全部调律",
                       "ask": "询问是否继续"}


@dataclass
class FoodRule:
    """狗粮添加规则（有序规则表的一条）

    三个条件全部满足时命中：首词条 cap_pct >= pct（pct=0 不限，
    cap_pct 识别失败视为不达标）、装备期望评级 >= min_expect、
    装备品阶 >= min_quality（blue=不限）。
    food 空串 = 命中即明确不添加（终止规则，可表达「金品阶不喂」）。
    on_insufficient：命中但持有量不足（读不到即没有）时，
    continue=继续调律（不添加狗粮），skip=跳过该装备。
    """
    pct: int = 0
    min_expect: str = "normal"
    min_quality: str = "blue"
    food: str = ""
    on_insufficient: str = "continue"

    def summary(self) -> str:
        """条件摘要文本（日志与说明文档）"""
        return (f"首词条≥{self.pct}% 且 期望≥"
                f"{RATING_LABELS.get(self.min_expect, self.min_expect)} 且 "
                f"品阶≥{_FOOD_QUALITY_LABELS.get(self.min_quality, self.min_quality)}")


@dataclass
class FoodDecision:
    """狗粮规则表的单轮决策结果

    action：feed=添加 food，none=不添加，skip=放弃该装备。
    """
    action: str
    food: str = ""
    reason: str = ""


@dataclass
class MaterialSettings:
    """材料设置（大律准石数量检查 + 狗粮规则表）

    stone_check_enabled: 调律时检查大律准石持有量，低于基准判
    材料不足，按 stone_insufficient_action 执行不足处理；默认
    关闭（用户自行保证材料充足）。
    stone_insufficient_action: 不足处理（STONE_ACTIONS：跳过该
    装备 / 结束全部调律 / 询问是否继续），默认结束全部调律。
    food_rules: 有序狗粮规则表，逐轮顺序判定首条完全满足（条件
    命中 + 材料充足）的规则；全部走完无命中 → 不添加。
    """
    stone_check_enabled: bool = False
    stone_min_count: int = 100
    stone_insufficient_action: str = "abort"
    food_rules: list[FoodRule] = field(default_factory=list)

    def decide_food(self, cap_pct: int | None, expect: str | None,
                    quality: str | None,
                    stocks: dict[str, int | None]) -> FoodDecision:
        """逐轮狗粮决策：顺序扫规则表，首条完全满足即生效

        Args:
            cap_pct: 首词条数值百分比（None=识别失败，仅 pct=0 可命中）
            expect: 装备期望评级 key（RATING_RANK；None 保守不命中）
            quality: 装备品阶 key（QUALITY_RANK；未知保守不命中）
            stocks: 材料 label → 持有量（缺 key/None/<1 均视为不足）
        """
        expect_rank = RATING_RANK.get(expect or "", -1)
        quality_rank = QUALITY_RANK.get(quality or "", -1)
        for idx, rule in enumerate(self.food_rules, start=1):
            if rule.pct > 0 and (cap_pct is None or cap_pct < rule.pct):
                continue
            if expect_rank < RATING_RANK.get(rule.min_expect, 99):
                continue
            if quality_rank < QUALITY_RANK.get(rule.min_quality, 99):
                continue
            desc = f"规则{idx}（{rule.summary()}）命中"
            if not rule.food:
                return FoodDecision("none", "", f"{desc} → 不添加狗粮")
            stock = stocks.get(rule.food)
            if stock is None or stock < 1:
                if rule.on_insufficient == "skip":
                    return FoodDecision(
                        "skip", rule.food,
                        f"{desc}但 {rule.food} 持有量不足 → 跳过该装备")
                continue  # 继续走后续规则
            return FoodDecision(
                "feed", rule.food, f"{desc} → 本轮添加 {rule.food}")
        return FoodDecision("none", "", "无狗粮规则命中 → 不添加")


# ─── 行为配置（状态机三行为点：扫描处理 / 材料处理 / 结束处理）──

# 行为动作（结束处理 tune 的四个动作）：
# - continue: 继续调律（词条满时自动结束，无需单独配置）
# - reset: 重置装备（清空首词条以外全部词条后继续，冷却期限制每件限一次）
# - recycle: 回收装备
# - skip: 跳过该装备（结束保留在背包）
# - tune_full_recycle: 调满后回收（金装专用，仅扫描处理可用）
# 扫描处理 scan 有 recycle / skip / tune_full_recycle 三个动作
# 行为动作（结束处理 tune 的四个动作 + 扫描处理新增的调满后回收）
BEHAVIOR_ACTIONS = ("continue", "reset", "recycle", "skip", "tune_full_recycle")
BEHAVIOR_ACTION_LABELS = {
    "continue": "继续调律",   # 词条满时自动结束
    "reset": "重置装备",
    "recycle": "回收装备",
    "skip": "跳过该装备",
    "tune_full_recycle": "调满后回收",  # 金装专用：调满5词条后回收
    "tune_this": "强制调律",  # 扫描处理专用：无视门槛强制进入调律页
}
# 各行为点允许的动作（材料处理由 MaterialSettings 承担，不入表）
BEHAVIOR_STAGE_ACTIONS = {
    "scan": ("recycle", "skip", "tune_full_recycle", "tune_this"),
    "tune": ("continue", "reset", "recycle", "skip"),
}
BEHAVIOR_STAGE_LABELS = {"scan": "扫描处理", "tune": "结束处理"}
# 动作说明（供 UI tooltip 显示）
BEHAVIOR_ACTION_TOOLTIPS = {
    "continue": "继续调律：词条满时自动结束",
    "reset": "重置装备：清空首词条以外的全部词条后继续（冷却期限制，每件限一次）",
    "recycle": "回收装备：分解为材料",
    "skip": "跳过该装备：结束保留在背包",
    "tune_full_recycle": "调满后回收：金装专用，跳过狗粮与规则判定，调满5词条后回收",
    "tune_this": "强制调律：无视进入门槛，强制进入调律页（配合结束处理「启用初始判定」实现调废装备重置复用）",
}
# 判定语义：预期评级识别用哪个流派规则集；affix=自选词条
#（不跑潜力判定，判定结果列存词条名，按装备词条名匹配）
JUDGE_SCOPES = ("incoming", "all", "custom", "affix")
JUDGE_SCOPE_LABELS = {"incoming": "传入规则", "all": "全部规则",
                      "custom": "自选规则", "affix": "自选词条"}
# 首词条初始数值比较方向（le=≤，ge=≥）
PCT_OPS = ("le", "ge")
PCT_OP_LABELS = {"le": "≤", "ge": "≥"}
# 游戏内单件装备重置调律次数上限（按钮文本实读剩余次数兼作硬门）
MAX_TUNE_RESETS = 3


@dataclass
class BehaviorRule:
    """行为决策规则（有序规则表的一条，自上而下首条命中即生效）

    四条件全部满足时命中：
    - parts: 部位集合（QUALITY_PARTS 子集，空 = 不限）；
    - max_quality: 品阶条件（gold = 不限，gold_only = 仅金装，
      purple_only = 仅紫装（精确），purple/blue = ≤ 该档）；
    - pct_op/pct: 首词条初始数值 cap_pct 比较条件（方向可选：
      le=≤、ge=≥；le 且 pct=100 / ge 且 pct=0 均为不限；
      非不限时识别失败视为不达标）；
    - ratings: 结果条件集合，语义随 judge_scope 分流：
      评级语义（incoming/all/custom）下为预期评级档位集合
      （RATING_KEYS 子集，自由多选；空 = 不限，不取评级；非空时
      预期评级属于集合内才命中，按本规则声明的判定语义
      judge_scope/judge_rules 取各适用规则最高档；无任何适用
      规则 = 无调律价值，由评级提供者兜底为垃圾档）；
      自选词条语义（affix）下为词条名集合（解析层保证非空），
      不跑潜力判定，装备任一条题名属于集合即命中。
    判定语义逐规则声明：incoming=传入规则 / all=全部规则 /
    custom=自选 judge_rules（仅 custom 可声明）/ affix=自选词条。
    first_affix_only（仅扫描处置表可声明）：评级语义下取评级时
    只注入首词条（忽略已有其他词条，其余槽视作空槽由潜力
    判定自由填充），避免回收掉非首词条已成垃圾但可重置
    调律的装备；自选词条语义下只判定装备的首词条。
    """
    parts: list[str] = field(default_factory=list)
    max_quality: str = "gold"
    pct_op: str = "le"
    pct: int = 100
    ratings: list[str] = field(default_factory=list)
    judge_scope: str = "incoming"
    judge_rules: list[str] = field(default_factory=list)
    first_affix_only: bool = False
    action: str = "skip"

    @property
    def pct_unlimited(self) -> bool:
        """首词条条件是否为不限（le 且 100 / ge 且 0）"""
        return (self.pct >= 100 if self.pct_op == "le"
                else self.pct <= 0)

    def matches(self, part: str | None, quality: str | None,
                cap_pct: float | None, rating: str | None,
                affix_names: list[str] | None = None) -> bool:
        """四条件 AND 判定（未知部位/品阶/评级仅命中不限条件）

        品阶条件：gold=不限（全部匹配），gold_only=仅金装，
        purple_only=仅紫装（精确），purple=紫装及以下（≤紫色），
        blue=蓝装及以下（≤蓝色）。
        首词条条件：pct_op 方向比较（非不限时识别失败不命中）。
        结果条件按 judge_scope 分流：affix=装备任一条题名属于
        ratings（first_affix_only 时只判定首词条，ratings 空不命中）；
        评级语义 ratings 空=不限，非空时评级属于集合才命中
        （未知评级不命中）。
        """
        if self.parts and (part or "") not in self.parts:
            return False
        # 品阶匹配：gold=不限，gold_only/purple_only=精确，其余 ≤ 语义
        if self.max_quality == "gold_only":
            if (quality or "") != "gold":
                return False
        elif self.max_quality == "purple_only":
            if (quality or "") != "purple":
                return False
        elif self.max_quality != "gold":
            rank = QUALITY_RANK.get(quality or "", -1)
            if rank < 0 or rank > QUALITY_RANK[self.max_quality]:
                return False
        if not self.pct_unlimited:
            if cap_pct is None:
                return False
            if self.pct_op == "ge":
                if cap_pct < self.pct:
                    return False
            elif cap_pct > self.pct:
                return False
        if self.judge_scope == "affix":
            # 自选词条：ratings 存词条名（解析层保证非空），
            # 装备任一条名属于集合即命中；仅首词条时只看首条
            if not self.ratings:
                return False
            names = affix_names or []
            if self.first_affix_only:
                names = names[:1]
            return any(n in self.ratings for n in names)
        if self.ratings and (rating or "") not in self.ratings:
            return False
        return True

    def summary(self) -> str:
        """条件摘要文本（日志与说明文档）"""
        parts = "/".join(self.parts) if self.parts else "不限部位"
        if self.max_quality == "gold":
            quals = "不限品阶"
        elif self.max_quality == "gold_only":
            quals = "仅金装"
        elif self.max_quality == "purple_only":
            quals = "仅紫色"
        else:
            quals = f"品阶≤{QUALITY_LABELS.get(self.max_quality, self.max_quality)}"
        pct = ("首词条不限" if self.pct_unlimited
               else f"首词条{PCT_OP_LABELS.get(self.pct_op, self.pct_op)}"
                    f"{self.pct}%")
        if self.judge_scope == "affix":
            # 自选词条：ratings 存词条名，无词条时不命中
            names = "/".join(self.ratings) if self.ratings else "未选词条"
            extra = "，仅首词条" if self.first_affix_only else ""
            result = f"词条∈{{{names}}}（自选词条{extra}）"
        elif self.ratings:
            scope = JUDGE_SCOPE_LABELS.get(self.judge_scope, self.judge_scope)
            names = "/".join(RATING_LABELS.get(r, r)
                             for r in RATING_KEYS if r in self.ratings)
            extra = "，仅首词条" if self.first_affix_only else ""
            result = f"评级∈{{{names}}}（按{scope}{extra}）"
        else:
            result = "不限评级"
        return f"{parts} 且 {quals} 且 {pct} 且 {result}"


# 评级提供者：(判定语义, 自选规则 key 集, 仅注入首词条) →
# 预期评级；由调用方实现（工作流内带同一装备的语义级缓存，
# 无任何适用规则时兜底返回垃圾档；返回 None 保守视为未知，
# 仅命中不限评级规则）
RatingProvider = Callable[[str, list[str], bool], str | None]


def _first_hit(rules: list[BehaviorRule], part: str | None,
               quality: str | None, cap_pct: float | None,
               rating_of: RatingProvider,
               affix_names: list[str] | None = None,
               skip: Callable[[BehaviorRule], bool] | None = None,
               ) -> tuple[int, BehaviorRule] | None:
    """有序规则表首条命中（评级按各规则自身判定语义懒取；
    自选词条语义不跑潜力判定，按 affix_names 词条名匹配）"""
    for idx, rule in enumerate(rules, start=1):
        if skip and skip(rule):
            continue
        rating = (rating_of(rule.judge_scope, rule.judge_rules,
                            rule.first_affix_only)
                  if rule.ratings and rule.judge_scope != "affix" else None)
        if rule.matches(part, quality, cap_pct, rating, affix_names):
            return idx, rule
    return None


@dataclass
class ScanBehavior:
    """扫描处理（进调律前的行为点）

    min_level: 等级门槛 —— 低于该等级的装备直接跳过
    entry_min_rating: 调律门槛 —— 传入规则预期评级 ≥ 该档即进入
    调律（固定用传入规则判定，调律目标就是运行期所选流派）；
    rules: 不进调律装备的处置表（首条命中；无命中=跳过该装备），
    评级判定语义与仅注入首词条均逐规则声明（BehaviorRule）。
    """
    enabled: bool = True
    min_level: int = 100
    entry_min_rating: str = "excellent"
    rules: list[BehaviorRule] = field(default_factory=list)

    def decide(self, part: str | None, quality: str | None,
               cap_pct: float | None,
               rating_of: RatingProvider,
               affix_names: list[str] | None = None,
               ) -> tuple[str, str]:
        """返回 (动作, 决策说明)；未启用/无命中时为 ("skip", 说明)"""
        if not self.enabled:
            return "skip", "扫描处置未启用 → 跳过该装备"
        hit = _first_hit(self.rules, part, quality, cap_pct, rating_of,
                         affix_names)
        if hit:
            idx, rule = hit
            label = BEHAVIOR_ACTION_LABELS.get(rule.action, rule.action)
            return rule.action, (
                f"规则{idx}（{rule.summary()}）命中 → {label}")
        return "skip", "无处置规则命中 → 跳过该装备"


@dataclass
class TuneBehavior:
    """结束处理（每轮调律结束后的行为点）

    每轮 decide 时评级按各规则自身判定语义懒取；词条满为边界
    条件：full=True 时 continue 动作自动转为 skip（不可再调）。
    无命中默认：未满=继续调律、满=跳过该装备；未启用同默认。
    max_resets: 单件装备重置次数上限（按钮文本携带剩余次数另作
    硬门，不超过游戏硬限 MAX_TUNE_RESETS）；
    reset_exhausted_action: 规则命中重置但次数已用尽（含 OCR
    读不到次数）时的转处置动作：recycle / skip。
    """
    enabled: bool = False
    rules: list[BehaviorRule] = field(default_factory=list)
    max_resets: int = MAX_TUNE_RESETS
    reset_exhausted_action: str = "skip"
    initial_check: bool = False

    def decide(self, part: str | None, quality: str | None,
               cap_pct: float | None, rating_of: RatingProvider,
               full: bool,
               affix_names: list[str] | None = None) -> tuple[str, str]:
        """返回 (动作, 决策说明)；无命中默认未满=continue、满=skip"""
        default = (("skip", "词条已满，无行为规则命中 → 跳过该装备")
                   if full else ("continue", "无行为规则命中 → 继续调律"))
        if not self.enabled:
            return default
        # 词条已满不可再调，continue 动作自动转为 skip
        hit = _first_hit(self.rules, part, quality, cap_pct, rating_of,
                         affix_names,
                         skip=(lambda r: full and r.action == "continue"))
        if hit:
            idx, rule = hit
            # continue 动作在词条满时自动转为 skip
            action = ("skip" if full and rule.action == "continue"
                      else rule.action)
            label = BEHAVIOR_ACTION_LABELS.get(action, action)
            return action, (
                f"规则{idx}（{rule.summary()}）命中 → {label}")
        return default


@dataclass
class TuningGroup:
    """基础规则组（tuning_groups/ 下一个 YAML 文件，可多套切换）

    承载单次调律运行的策略基线：材料设置 + 行为配置
    （扫描/结束处理）。激进/保守等账号策略差异体现在不同规则组，
    启动时经 TuningRunContext 注入工作流。
    """
    key: str = "default"
    name: str = "基础规则"
    materials: MaterialSettings = field(default_factory=MaterialSettings)
    scan: ScanBehavior = field(default_factory=ScanBehavior)
    tune: TuneBehavior = field(default_factory=TuneBehavior)


@dataclass
class TuneConfig:
    """全局调律配置（品阶门槛 + 开关注册表 + 基础规则组声明，tune_config.yaml）

    base_rules: 基础规则组 key 列表，顺序即 UI 展示顺序。
    quality_thresholds: 品阶门槛，部位 → 允许品阶列表。
    switches: 开关注册表，key → 显示名。
    """
    base_rules: list[str] = field(default_factory=list)
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
