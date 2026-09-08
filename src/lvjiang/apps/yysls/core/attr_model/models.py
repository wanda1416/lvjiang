"""基础属性来源建模的领域模型与固定词汇

回答的问题是「装备之外的战斗属性从哪来」：等级底子、突破、五维转换、
武学天赋、心法、套装、武备、弓玦、神工、奇物、秘籍、吃食。每个来源
产出若干 :class:`StatEffect`，求值后汇总成 :class:`CombatAttributes`，
作为 ``build_graduation_attrs(base_attrs=...)`` 的入参；装备词条依旧
走原有的 ``equipment_attrs`` 通道，本包不碰。

工作字段空间（WORKING_FIELDS）是 CombatAttributes 的超集，额外含五维
（dim_*）。五维本身不是战斗属性，但武学天赋的转换公式要读它（例如
「外功攻击 = 敏 × 系数，上限 73.9」），所以必须在同一空间里参与求值，
最后投影回 CombatAttributes 时丢弃。

取值三形态：
- 常数：``{ crit_dmg: 0.046 }``
- 整条词条：``{ full_affix: 外功攻击 }`` —— 数值由 game_config 的
  affix_caps[等级] 按 split 拆成 min/max 两个字段，换赛季只改 affix_caps；
- 公式：``{ formula: {source: dim_min, multiplier: 0.2639, max: 73.9} }``

作用域区分面板与战斗：吃食一类只在战斗内生效、不进角色面板，
标 ``scope: combat``，于是同一份配置能同时产出对得上面板的
panel_attrs 和喂毕业率的 combat_attrs。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any

from .....i18n import tr
from ..combat.combat_attrs import COMBAT_ATTR_FIELDS, CombatAttributes

# ─── 固定词汇（写死在代码，不进 YAML） ─────────────────────

#: 来源类别。声明序即 breakdown 展示序，也是求值时的稳定顺序。
SOURCE_KINDS: tuple[str, ...] = (
    "base",          # 角色等级底子
    "breakthrough",  # 突破
    "dimension",     # 五维 → 战斗属性转换
    "martial_art",   # 武学天赋
    "inner_way",     # 心法
    "gear_set",      # 套装（含弓玦）
    "arsenal",       # 武备
    "divinecraft",   # 神工
    "oddity",        # 奇物
    "script",        # 秘籍
    "food",          # 吃食
)

SOURCE_KIND_LABELS: dict[str, str] = {
    "base": "等级底子",
    "breakthrough": "突破",
    "dimension": "五维转换",
    "martial_art": "武学天赋",
    "inner_way": "心法",
    "gear_set": "套装",
    "arsenal": "武备",
    "divinecraft": "神工",
    "oddity": "奇物",
    "script": "秘籍",
    "food": "吃食",
}

#: 五维工作字段（不属于 CombatAttributes，求值结束后丢弃）
DIMENSION_FIELDS: tuple[str, ...] = (
    "dim_jin",  # 劲
    "dim_shi",  # 势
    "dim_min",  # 敏
    "dim_ti",   # 体
    "dim_yu",   # 御
)

DIMENSION_LABELS: dict[str, str] = {
    "dim_jin": "劲",
    "dim_shi": "势",
    "dim_min": "敏",
    "dim_ti": "体",
    "dim_yu": "御",
}

#: 作用域：panel 进角色面板；combat 只在战斗内生效（不进面板）
SCOPE_PANEL = "panel"
SCOPE_COMBAT = "combat"
SCOPES: tuple[str, ...] = (SCOPE_PANEL, SCOPE_COMBAT)

#: 整条词条的 min/max 默认拆分比。游戏事实：心法给出的一整条词条
#: 按 1:2 拆成最小/最大，两者之和等于该等级该词条的满值。
#: 已在 96 级（25.9 / 51.9，和 77.8）与 110 级（40.5 / 80.9，和 121.4）
#: 两个独立数据点上验证。不符合的条目在 YAML 里显式写 split 覆盖。
DEFAULT_AFFIX_SPLIT: tuple[int, int] = (1, 2)

#: 词条类别 → (最小字段, 最大字段)。属性攻击是词组，按流派解析，
#: 见 :func:`attr_attack_fields`。
AFFIX_RANGE_FIELDS: dict[str, tuple[str, str]] = {
    "外功攻击": ("min_outer", "max_outer"),
}

#: 流派 → 属攻字段对。键对齐 game_config 的属性攻击词组别名分组。
ATTR_ATTACK_FIELDS: dict[str, tuple[str, str]] = {
    "通用": ("min_wuxiang", "max_wuxiang"),
    "鸣金": ("min_mingjin", "max_mingjin"),
    "牵丝": ("min_qiansi", "max_qiansi"),
    "裂石": ("min_lieshi", "max_lieshi"),
    "破竹": ("min_pozhu", "max_pozhu"),
}

#: 词条类别 → 单值字段（非区间）。五维走 dim_*，其余对齐 CombatAttributes。
AFFIX_SCALAR_FIELDS: dict[str, str] = {
    "会心率": "crit_rate",
    "会意率": "intent_rate",
    "精准率": "precision",
    "全部武学增效": "all_skill_bonus",
}

#: 属性攻击词组名（与 tuning_rules.ATTR_ATTACK_CATEGORY 同义，此处独立
#: 声明避免跨包耦合）
ATTR_ATTACK_CATEGORY = "属性攻击"

#: 五维词组名
DIMENSION_CATEGORY = "五维属性"

#: 条目 id 的分组分隔符。「易水歌·二重」「无名剑法·天赋」都按它分组。
ENTRY_SEPARATOR = "·"

#: 武学条目 id 的后缀。一门武学的整棵天赋树算一个条目——天赋点怎么加
#: 由流派与配装决定，本模型只关心这门武学最终给了多少静态属性。
MARTIAL_ART_ENTRY_SUFFIX = "天赋"

#: 心法重数。游戏里固定六重，选第 N 重时一重至 N 重同时生效。
INNER_WAY_TIERS: tuple[str, ...] = (
    "一重", "二重", "三重", "四重", "五重", "六重",
)
MAX_INNER_WAY_TIER = len(INNER_WAY_TIERS)

#: 同时可装备的心法门数
INNER_WAY_SLOTS = 4


def martial_art_source_id(name: str) -> str:
    """武学名 → 属性来源条目 id"""
    return f"{name}{ENTRY_SEPARATOR}{MARTIAL_ART_ENTRY_SUFFIX}"


def inner_way_source_ids(name: str) -> list[str]:
    """心法名 → 六重的条目 id，重数顺序即 :data:`INNER_WAY_TIERS`"""
    return [f"{name}{ENTRY_SEPARATOR}{tier}" for tier in INNER_WAY_TIERS]

#: 来源的选择方式。游戏规则决定，不进 YAML。
#:
#: - ``slots``  槽位制：心法四个槽，每槽一门 + 一个重数
#: - ``derived`` 不由用户选：武学由流派的主/副武学决定，五维转换恒生效
#: - ``single`` 至多选一项
#: - ``all``    该类别全部条目恒生效
SELECT_SLOTS = "slots"
SELECT_DERIVED = "derived"
SELECT_SINGLE = "single"
SELECT_ALL = "all"

#: 各来源类别的选择方式。空文件的那几类先按最保守的猜测填，
#: 填数据时发现不符再改——它是模型结构，不是游戏数值。
SELECTION_POLICIES: dict[str, str] = {
    "base": SELECT_ALL,            # 等级底子恒生效
    "breakthrough": SELECT_SINGLE,  # 当前突破等级只有一个
    "dimension": SELECT_DERIVED,    # 内建，恒生效
    "martial_art": SELECT_DERIVED,  # 由流派的主/副武学决定
    "inner_way": SELECT_SLOTS,      # 四个槽，每槽带重数
    "gear_set": SELECT_SINGLE,
    "arsenal": SELECT_SINGLE,
    "divinecraft": SELECT_SINGLE,
    "oddity": SELECT_ALL,           # 奇物是已收集的，全部生效
    "script": SELECT_SINGLE,
    "food": SELECT_SINGLE,
}

#: CombatAttributes 的数值字段（不含 extra_attrs）
COMBAT_NUMERIC_FIELDS: tuple[str, ...] = tuple(
    f.name for f in dataclass_fields(CombatAttributes) if f.name != "extra_attrs"
)

#: 求值的工作字段空间：战斗属性 + 五维。五维不是战斗属性，但武学天赋的
#: 转换公式要读它，所以必须在同一空间里参与求值，投影时丢弃。
WORKING_FIELDS: tuple[str, ...] = COMBAT_NUMERIC_FIELDS + DIMENSION_FIELDS

#: full_affix 真正支持的词条类别。
#:
#: 词组配置里有 15 类词条，但能落到具体属性字段的只有这几类。界面必须
#: 只列出这些——否则会出现「可选择、可保存、推导时才报错」，而一个条目
#: 报错会让整次求值失败。
SUPPORTED_FULL_AFFIX_CATEGORIES: tuple[str, ...] = (
    (ATTR_ATTACK_CATEGORY,)
    + tuple(AFFIX_RANGE_FIELDS)
    + tuple(AFFIX_SCALAR_FIELDS)
)

#: 百分比字段：内部存小数（0.046 = 4.6%），界面按百分数显示与输入。
#: 从 COMBAT_ATTR_FIELDS 的单位派生，不另抄一份——抄一份就会漂移。
PERCENT_FIELDS: frozenset[str] = frozenset(
    name for name, _display, unit, _range in COMBAT_ATTR_FIELDS if unit == "%"
)


class AttrModelError(Exception):
    """属性来源配置的解析或求值错误"""


# ─── 取值形态 ────────────────────────────────────────────

@dataclass(frozen=True)
class Formula:
    """线性转换：``source × multiplier + offset``，可选钳制与取整

    source 取自同一轮求值的工作字段（含五维），所以「敏 → 外功攻击」
    这类武学天赋能直接表达。
    """

    source: str
    multiplier: float = 1.0
    offset: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    ndigits: int | None = None

    def apply(self, sources: dict[str, float]) -> float | None:
        raw = sources.get(self.source)
        if raw is None:
            return None
        value = raw * self.multiplier + self.offset
        if self.minimum is not None:
            value = max(self.minimum, value)
        if self.maximum is not None:
            value = min(self.maximum, value)
        if self.ndigits is not None:
            value = round(value, self.ndigits)
        return value


@dataclass(frozen=True)
class FullAffix:
    """一整条词条。数值由 affix_caps[等级] 生成，不写死在配置里

    - 区间词条（外功攻击 / 属性攻击）按 split 拆成最小 + 最大两个字段，
      两者之和为满值；
    - 单值词条（会心率等）整条落在一个字段上。
    """

    category: str
    split: tuple[int, int] = DEFAULT_AFFIX_SPLIT


@dataclass(frozen=True)
class StatEffect:
    """一个来源在一个条目上的全部贡献

    ``stats`` 的值可以是 ``float``（常数）或 :class:`Formula`；
    ``full_affix`` 是条目级的整条词条声明，展开后并入 stats。
    ``extra`` 走 CombatAttributes.extra_attrs（流派专属的动态属性）。
    """

    source_id: str
    label: str
    kind: str
    scope: str = SCOPE_PANEL
    #: 分组名与档位。心法用它表达「易水歌 的 第 2 重」，选第 N 重时
    #: 一重至 N 重同时生效。显式存元数据而不是切分中文 id——id 里
    #: 出现「·」或重数写法不一致时，字符串切分会静默算错。
    group: str = ""
    tier: int = 0
    stats: dict[str, float | Formula] = field(default_factory=dict)
    full_affix: FullAffix | None = None
    extra: dict[str, float] = field(default_factory=dict)
    #: 该条目是否已填数值。未填的进 breakdown 的「未建模」清单，
    #: 贡献 0，由反解兜底成用户手填值。
    modeled: bool = True
    #: 已确认此条目不产生**本模型处理的任何静态属性**。
    #:
    #: 心法六重里大量是触发类效果——条件触发、叠层、持续时间、改变
    #: 技能循环，这些本模型一概不建模。标记的含义不是「这一重没用」，
    #: 也不只是「不进面板」：scope=combat 的静态属性同样由本模型处理，
    #: 标了 no_effect 会连它一起丢掉。
    #:
    #: 与「未填」区分开，进度才能真的走到 100%，否则永远有一堆查过、
    #: 确认没有、却仍显示待填的条目。
    no_effect: bool = False

    @property
    def pending(self) -> bool:
        """仍需人工确认：既没填数值，也没确认过「无静态属性」"""
        return not self.modeled and not self.no_effect

    def __post_init__(self) -> None:
        if self.kind not in SOURCE_KINDS:
            raise AttrModelError(
                tr("未知来源类别: {kind}").format(kind=self.kind)
            )
        if self.scope not in SCOPES:
            raise AttrModelError(
                tr("未知作用域: {scope}").format(scope=self.scope)
            )


# ─── 装配状态 ────────────────────────────────────────────

@dataclass(frozen=True)
class InnerWaySlot:
    """一个心法槽：装了哪门、修到第几重"""

    name: str
    tier: int

    def __post_init__(self) -> None:
        if not 1 <= self.tier <= MAX_INNER_WAY_TIER:
            raise AttrModelError(
                tr("心法重数只能是 1-{max}：{name} 填了 {tier}").format(
                    max=MAX_INNER_WAY_TIER, name=self.name, tier=self.tier)
            )


@dataclass(frozen=True)
class AttrLoadout:
    """当前角色的装配状态——推导的唯一输入

    对应游戏里实际能装的东西：四个心法槽（每槽一门 + 重数），套装/
    武备/神工/吃食等各选一项。两门武学由流派的主副武学决定，不在这里
    选；五维转换恒生效。

    没有「全选」这个状态：互斥来源全部相加必然是错的，而空装配得到
    的零值一眼就能看出没配，比一个似是而非的数安全。
    """

    level: int
    school: str
    inner_ways: tuple[InnerWaySlot, ...] = ()
    #: 单选类来源：类别 → 条目 id
    selections: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.inner_ways) > INNER_WAY_SLOTS:
            raise AttrModelError(
                tr("最多同时装备 {n} 门心法，给了 {got} 门").format(
                    n=INNER_WAY_SLOTS, got=len(self.inner_ways))
            )
        names = [slot.name for slot in self.inner_ways]
        if len(set(names)) != len(names):
            raise AttrModelError(tr("同一门心法不能装在多个槽位"))
        for kind in self.selections:
            if SELECTION_POLICIES.get(kind) != SELECT_SINGLE:
                raise AttrModelError(
                    tr("{kind} 不是单选来源，不能用 selections 指定").format(
                        kind=kind)
                )

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "school": self.school,
            "inner_ways": [
                {"name": slot.name, "tier": slot.tier} for slot in self.inner_ways
            ],
            "selections": dict(self.selections),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "AttrLoadout":
        payload = data if isinstance(data, dict) else {}
        slots: list[InnerWaySlot] = []
        for raw in payload.get("inner_ways") or []:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            slots.append(InnerWaySlot(
                name=str(raw["name"]), tier=int(raw.get("tier") or 1)))
        selections = {
            str(k): str(v)
            for k, v in (payload.get("selections") or {}).items() if v
        }
        return cls(
            level=int(payload.get("level") or 0),
            school=str(payload.get("school") or ""),
            inner_ways=tuple(slots),
            selections=selections,
        )


# ─── 求值产物 ────────────────────────────────────────────

@dataclass(frozen=True)
class AppliedModifier:
    """单次贡献的明细，用于 breakdown 与差分定位

    面板对不上时，逐条比对本清单即可定位到具体来源，而不是只知道
    总数不对。

    ``is_extra`` 为真时 ``field_name`` 是 extra_attrs 的动态属性名
    （如「剑武学增伤」），否则是 CombatAttributes 的字段名。两者命名
    空间天然不重叠，但显式标出来，取用方不必靠猜。
    """

    source_id: str
    label: str
    kind: str
    scope: str
    field_name: str
    before: float
    after: float
    is_extra: bool = False

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class UnmodeledSource:
    """已声明但未填数值的条目"""

    source_id: str
    label: str
    kind: str


@dataclass(frozen=True)
class ScopeResult:
    """一个作用域的求值产物：属性值与产生它的明细

    值和明细绑在同一个对象上，是为了让「显示 A 的值、却按 B 的明细
    拆分」在结构上就写不出来——此前两者分开放，界面显示面板值、
    breakdown 却汇总了含吃食的战斗贡献，两栏加不到一起。
    """

    attrs: Any  # CombatAttributes，避免本模块反向依赖 combat 包
    modifiers: list[AppliedModifier] = field(default_factory=list)

    def modifiers_for(self, field_name: str) -> list[AppliedModifier]:
        """某个字段收到的全部贡献，按求值顺序"""
        return [m for m in self.modifiers if m.field_name == field_name]

    def contribution_by_kind(self, field_name: str) -> dict[str, float]:
        """某个字段按来源类别汇总的贡献"""
        totals: dict[str, float] = {}
        for modifier in self.modifiers_for(field_name):
            totals[modifier.kind] = totals.get(modifier.kind, 0.0) + modifier.delta
        return totals

    def touched_fields(self) -> list[str]:
        """收到过贡献的字段，按首次出现序；含 extra_attrs 的动态属性"""
        seen: list[str] = []
        for modifier in self.modifiers:
            if modifier.field_name not in seen:
                seen.append(modifier.field_name)
        return seen


@dataclass
class ResolveResult:
    """一次求值的完整产物

    ``panel`` 只含 scope=panel 的贡献，用来和游戏角色面板对账；
    ``combat`` 含全部贡献，作为毕业率的 base_attrs。两者的差集就是
    吃食这类只在战斗内生效的部分。
    """

    panel: ScopeResult
    combat: ScopeResult
    unmodeled: list[UnmodeledSource] = field(default_factory=list)

    @property
    def panel_attrs(self) -> Any:
        return self.panel.attrs

    @property
    def combat_attrs(self) -> Any:
        return self.combat.attrs


# ─── 工作字段空间 ────────────────────────────────────────

def attr_attack_fields(school_attr: str) -> tuple[str, str]:
    """流派属性 → (最小属攻字段, 最大属攻字段)"""
    fields_pair = ATTR_ATTACK_FIELDS.get(school_attr)
    if fields_pair is None:
        raise AttrModelError(
            tr("未知流派属性: {attr}").format(attr=school_attr)
        )
    return fields_pair


def split_affix_cap(cap: float, split: tuple[int, int]) -> tuple[float, float]:
    """把一整条词条的满值按比例拆成 (最小, 最大)

    保留一位小数与游戏内显示一致：110 级外功攻击满值 121.4 按 1:2
    拆出 40.5 / 80.9。
    """
    low, high = split
    total = low + high
    if total <= 0:
        raise AttrModelError(tr("词条拆分比无效: {split}").format(split=split))
    return round(cap * low / total, 1), round(cap * high / total, 1)
