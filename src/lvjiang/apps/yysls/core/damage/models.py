"""伤害建模的数据结构

回答「这个技能的伤害由哪几个系数决定」。毕业率方案 JSON 里那份
``program`` 能精确复现整张 Excel，但它是两千多个节点的表达式图——
从里面读不出「第一道剑气的外功倍率是 1.3066」，更改不了。本模块把
这部分显式建模：技能系数表与增益表各自成条目，可读、可改、可比对。

**求值不在这里**。毕业率仍由编译程序算，本模型是它的可读参考层，
两边同源于同一份 Excel（``source.sha256`` 对得上才是配套的）。所以
这里只有数据结构与校验，没有伤害公式——写第二份公式就等于有了两份
真相，改一处漏一处。

环境参数（怪物防御/抗性、食物、固伤加成、团队增益）也不在这里：
方案 JSON 的 ``environment`` 已经存了一份，再存一份必然漏同步。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .....i18n import tr


class DamageModelError(ValueError):
    """伤害模型配置有误"""


#: 修正字段。技能自带的加成与增益条目共用同一套词汇——它们在 Excel
#: 里就是同一组列（`武学奇术` 的 B..X 与 `增益` 的 B..W），求值时也走
#: 同一条加法。分成两套名字只会让人对着表来回翻译。
MODIFIER_FIELDS: dict[str, str] = {
    "generic": "通用增伤",
    "special": "特殊增伤",
    "min_outer": "最小外功",
    "max_outer": "最大外功",
    "outer_bonus": "外功加成",
    "outer_pen": "外功穿透",
    "outer_dmg": "外攻伤害加成",
    "attr_bonus": "属性攻击加成",
    "mingjin_pen": "鸣金穿透",
    "mingjin_dmg": "鸣金加成",
    "lieshi_pen": "裂石穿透",
    "lieshi_dmg": "裂石加成",
    "qiansi_pen": "牵丝穿透",
    "qiansi_dmg": "牵丝加成",
    "pozhu_pen": "破竹穿透",
    "pozhu_dmg": "破竹加成",
    "crit_rate": "会心率",
    "crit_dmg": "会心伤害",
    "intent_rate": "会意率",
    "intent_dmg": "会意伤害",
    "direct_crit": "直接会心率",
    "direct_intent": "直接会意率",
}

#: 技能的强制结算开关。命中必定按该档结算，绕开概率分配——「气竭」
#: 一类的机制靠它表达。
FORCE_FLAGS: dict[str, str] = {
    "force_precision": "强制精准",
    "force_crit": "强制会心",
    "force_intent": "强制会意",
}

#: 技能的四个系数。伤害对它们是线性的，所以「三剑气」那种合并条目
#: 直接等于三道之和。
RATIO_FIELDS: dict[str, str] = {
    "outer_ratio": "外功倍率",
    "outer_fixed": "外攻固伤",
    "attr_ratio": "属性倍率",
    "attr_fixed": "属性固伤",
}


def _check_modifiers(where: str, modifiers: dict[str, float]) -> None:
    unknown = set(modifiers) - set(MODIFIER_FIELDS)
    if unknown:
        raise DamageModelError(
            tr("{where} 含未知修正字段: {keys}").format(
                where=where, keys="、".join(sorted(unknown)))
        )


@dataclass(frozen=True)
class DamageSkill:
    """一次技能释放的伤害系数

    ``kind`` 是技能类型（剑 / 枪 / 单体奇术 / 群体奇术 / 心法…），决定
    吃哪一路增伤（剑增、枪增、奇术增），所以它是系数的一部分而不是
    分类标签。``charge`` 对应「定音加成 = 蓄力技」，吃蓄力技定音。
    """

    name: str
    kind: str = ""
    outer_ratio: float = 0.0
    outer_fixed: float = 0.0
    attr_ratio: float = 0.0
    attr_fixed: float = 0.0
    charge: bool = False
    #: 真气比例。技能造成的伤害里有多少折算成真气回复。
    qi_ratio: float = 0.0
    modifiers: dict[str, float] = field(default_factory=dict)
    force: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise DamageModelError(tr("技能名不能为空"))
        _check_modifiers(self.name, self.modifiers)
        unknown = set(self.force) - set(FORCE_FLAGS)
        if unknown:
            raise DamageModelError(
                tr("{where} 含未知强制结算开关: {keys}").format(
                    where=self.name, keys="、".join(sorted(unknown)))
            )

    @property
    def modeled(self) -> bool:
        """四个系数至少有一个非零才算填过"""
        return any(getattr(self, name) for name in RATIO_FIELDS)


@dataclass(frozen=True)
class DamageBuff:
    """一个可叠的增益

    轴上每一行挂若干增益，效果按字段相加。层数在 Excel 里是分开的条目
    （`秋瞑帖1层`…`秋瞑帖10层`），这里照搬——把层数做成参数看着更整齐，
    但表里每层的增量并不总是等差，硬套线性会算错。
    """

    name: str
    modifiers: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise DamageModelError(tr("增益名不能为空"))
        _check_modifiers(self.name, self.modifiers)


@dataclass(frozen=True)
class DamageModel:
    """一个流派方案的伤害模型"""

    school: str
    scheme: str = ""
    source: dict[str, str] = field(default_factory=dict)
    skills: tuple[DamageSkill, ...] = ()
    buffs: tuple[DamageBuff, ...] = ()

    def skill(self, name: str) -> DamageSkill | None:
        return next((s for s in self.skills if s.name == name), None)

    def progress(self) -> tuple[int, int]:
        """建模进度 ``(已填系数, 技能总数)``"""
        return sum(1 for s in self.skills if s.modeled), len(self.skills)
