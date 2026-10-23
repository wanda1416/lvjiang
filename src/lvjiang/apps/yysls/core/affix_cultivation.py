"""承音词条喂满所需的传律石数量估算。

一次传律只提升**一条**词条的数值，增量是该词条在目标等级的普通满值
（``cap``）乘以配置的传律石提升比例（默认 4.5%）。所以「这件装备喂满
还要几颗」不是一个比值，而是五条词条各自向上取整后的合计——哪怕某条只
差 0.1，也得再喂一颗，那也算这条词条喂满了。

终点不是手算的 94%，而是该词条在目标等级的**承音上限配置原值**。游戏里
承音上限正是按 94% 派生的，但配置允许逐条校正（显式 ``chengyin`` 优先），
按 94% 手算会和实际能到的数值对不上：最大外功到了 131.2 时占比 93.98%，
按 94% 判会以为还能提升，实际上承音上限就是 131.2，已经到头了。当前值
达到或超过承音上限时同样算「已经满了」——未承音装备在承音那一刻会被
截断到上限，需要 0 颗。

目标等级是**承音之后**的等级：未承音装备必须先承音才能培养词条数值，
所以 110 未承音装备要按 115 的满值与承音上限算，否则和 115 原生承音
装备不在同一口径上、没法比较。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ....i18n import tr
from ..config.constants import DEFAULT_CHUANLV_STONE_PERCENT
from .affix_cap import affix_cap_value
from .numbers import strict_float

#: 普通词条槽位下标；定音是独立槽、不受承音限制，不参与传律石估算
CULTIVABLE_INDEXES: tuple[int, ...] = (1, 2, 3, 4, 5)

#: 浮点容差：一石增量是 cap × 4.5%，累加后 (target - value) / step 经常
#: 差 1e-16 落在整数两侧。不扣掉这点误差，本该正好整除的情形会多喂一颗。
_EPSILON = 1e-9


@dataclass(frozen=True)
class AffixStoneNeed:
    """单条词条喂到承音上限所需的传律石。"""

    index: int
    name: str
    value: float
    target: float
    step: float
    stones: int


@dataclass(frozen=True)
class StoneEstimate:
    """一件装备五条普通词条合计。"""

    target_level: int
    percent: float
    items: tuple[AffixStoneNeed, ...]
    #: 配置缺失、算不出来的词条名（多半是该等级没登记这个词条的上限）
    unresolved: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return sum(item.stones for item in self.items)

    @property
    def is_full(self) -> bool:
        """五条词条都已经到承音上限，一颗都不用喂。"""
        return bool(self.items) and self.total == 0

    def summary(self) -> str:
        """面向用户的一句话结论。"""
        if not self.items:
            return tr("没有可估算的承音词条")
        if self.total == 0:
            return tr("当前装备承音词条已满，无需传律石")
        return tr("当前装备承音喂满需要 {count} 个传律石").format(
            count=self.total)

    def breakdown(self) -> str:
        """逐条明细，供 tooltip 展示。"""
        lines: list[str] = []
        for item in self.items:
            if item.stones == 0:
                lines.append(tr("{name}：已满（{value} / {target}）").format(
                    name=item.name, value=f"{item.value:.1f}",
                    target=f"{item.target:.1f}"))
            else:
                lines.append(tr(
                    "{name}：{stones} 个（{value} → {target}，每石 +{step}）"
                ).format(
                    name=item.name, stones=item.stones,
                    value=f"{item.value:.1f}", target=f"{item.target:.1f}",
                    step=f"{item.step:.2f}"))
        if self.unresolved:
            lines.append(tr("无法估算（缺少该等级上限配置）：{names}").format(
                names="、".join(self.unresolved)))
        return "\n".join(lines)


def stones_to_full(value: float, target: float, step: float) -> int:
    """把 ``value`` 喂到 ``target`` 需要的石头数。

    ``step`` 是每颗石头的绝对增量；已经达到或超过 ``target`` 时返回 0
    （承音截断后就是上限，没有可提升的空间）。
    """
    if step <= 0 or value >= target:
        return 0
    return max(0, math.ceil((target - value) / step - _EPSILON))


def affix_stone_need(
    level: int,
    index: int,
    name: str,
    value,
    percent: float,
    *,
    game_config=None,
) -> AffixStoneNeed | None:
    """单条词条的估算；上限配置缺失、词条为空或数值不可读时返回 None。"""
    if not name:
        return None
    numeric = strict_float(value)
    if numeric is None:
        return None
    # 满值取普通上限：4.5% 说的是「满值的 4.5%」，不是承音上限的。
    cap = affix_cap_value(level, name, game_config=game_config)
    target = affix_cap_value(level, name, chengyin=True, game_config=game_config)
    if not cap or not target:
        return None
    step = cap * float(percent) / 100.0
    return AffixStoneNeed(
        index=index,
        name=name,
        value=numeric,
        target=target,
        step=step,
        stones=stones_to_full(numeric, target, step),
    )


def estimate_stones(
    affixes: Iterable[tuple[int, str, object]],
    target_level: int,
    *,
    percent: float | None = None,
    game_config=None,
) -> StoneEstimate:
    """估算一组 ``(槽位, 词条名, 当前值)`` 喂满所需的传律石。

    ``target_level`` 必须是**承音之后**的等级，满值和承音上限都按它取。
    """
    if percent is None:
        percent = _default_percent(game_config)
    items: list[AffixStoneNeed] = []
    unresolved: list[str] = []
    for index, name, value in affixes:
        need = affix_stone_need(
            target_level, index, name, value, percent,
            game_config=game_config)
        if need is None:
            if name:
                unresolved.append(name)
            continue
        items.append(need)
    return StoneEstimate(
        target_level=target_level,
        percent=float(percent),
        items=tuple(items),
        unresolved=tuple(unresolved),
    )


def estimate_equipment_stones(
    equip: Mapping,
    *,
    target_level: int | None = None,
    percent: float | None = None,
    game_config=None,
) -> StoneEstimate:
    """按装备快照估算；``target_level`` 省略时取装备当前等级。"""
    level = target_level
    if level is None:
        level = int(equip.get("level") or 0)
    affixes = [
        (index, str((equip.get(f"affix_{index}") or {}).get("name") or ""),
         (equip.get(f"affix_{index}") or {}).get("value"))
        for index in CULTIVABLE_INDEXES
    ]
    return estimate_stones(
        affixes, int(level), percent=percent, game_config=game_config)


def _default_percent(game_config) -> float:
    """配置里的传律石提升比例；配置不可用时退回代码缺省值。"""
    if game_config is None:
        from ..config import get_game_config
        game_config = get_game_config()
    getter = getattr(game_config, "get_chuanlv_stone_percent", None)
    if callable(getter):
        value = getter()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return DEFAULT_CHUANLV_STONE_PERCENT


__all__ = [
    "CULTIVABLE_INDEXES",
    "AffixStoneNeed",
    "StoneEstimate",
    "affix_stone_need",
    "estimate_equipment_stones",
    "estimate_stones",
    "stones_to_full",
]
