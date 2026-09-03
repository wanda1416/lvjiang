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
from typing import Any

from .....i18n import tr

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
    stats: dict[str, float | Formula] = field(default_factory=dict)
    full_affix: FullAffix | None = None
    extra: dict[str, float] = field(default_factory=dict)
    #: 该条目是否已填数值。未填的进 breakdown 的「未建模」清单，
    #: 贡献 0，由反解兜底成用户手填值。
    modeled: bool = True
    #: 已确认此条目不提供基础属性（心法六重里大量是触发类效果，只改
    #: 战斗行为不进面板）。与「未填」区分开，进度才能真的走到 100%，
    #: 否则永远有一堆查过、确认没有、却仍显示待填的条目。
    no_effect: bool = False

    @property
    def pending(self) -> bool:
        """仍需人工确认：既没填数值，也没确认过无贡献"""
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


# ─── 求值产物 ────────────────────────────────────────────

@dataclass(frozen=True)
class AppliedModifier:
    """单次贡献的明细，用于 breakdown 与差分定位

    面板对不上时，逐条比对本清单即可定位到具体来源，而不是只知道
    总数不对。
    """

    source_id: str
    label: str
    kind: str
    scope: str
    field_name: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class UnmodeledSource:
    """已声明但未填数值的条目"""

    source_id: str
    label: str
    kind: str


@dataclass
class ResolveResult:
    """一次求值的完整产物

    ``panel_attrs`` 只含 scope=panel 的贡献，用来和游戏角色面板对账；
    ``combat_attrs`` 含全部贡献，作为毕业率的 base_attrs。两者的差集
    就是吃食这类只在战斗内生效的部分。
    """

    panel_attrs: Any  # CombatAttributes，避免本模块反向依赖 combat 包
    combat_attrs: Any
    modifiers: list[AppliedModifier] = field(default_factory=list)
    unmodeled: list[UnmodeledSource] = field(default_factory=list)

    def modifiers_for(self, field_name: str) -> list[AppliedModifier]:
        """某个字段收到的全部贡献，按求值顺序"""
        return [m for m in self.modifiers if m.field_name == field_name]

    def contribution_by_kind(self, field_name: str) -> dict[str, float]:
        """某个字段按来源类别汇总的贡献"""
        totals: dict[str, float] = {}
        for modifier in self.modifiers_for(field_name):
            totals[modifier.kind] = totals.get(modifier.kind, 0.0) + modifier.delta
        return totals


# ─── 工作字段空间 ────────────────────────────────────────

def working_fields(combat_fields: tuple[str, ...]) -> tuple[str, ...]:
    """CombatAttributes 数值字段 + 五维，构成求值的工作字段空间"""
    return tuple(combat_fields) + DIMENSION_FIELDS


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
