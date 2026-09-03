"""基础属性求值引擎

两趟求值：先把全部常数加完，再算公式。这样公式读到的源字段已经是
最终值，结果与 YAML 里来源的书写顺序无关——否则「敏 → 外功攻击」
会因为武学天赋恰好排在五维之前而算少。

同一份来源清单求值两次得到双出口：scope=panel 的子集对得上游戏
角色面板，全集喂毕业率。差集即吃食一类只在战斗内生效的加成。

未填数值的条目不参与求值（贡献 0），只登记进 unmodeled；缺口由
:func:`solve_residual` 反解成一份 residual，等价于用户手填的那部分。
于是模型建到一半也是可用状态，不必等全部来源填完。
"""

from __future__ import annotations

from dataclasses import fields as dataclass_fields
from typing import Callable

from loguru import logger

from .....i18n import tr
from ..combat.combat_attrs import CombatAttributes
from .models import (
    AFFIX_RANGE_FIELDS,
    AFFIX_SCALAR_FIELDS,
    ATTR_ATTACK_CATEGORY,
    DIMENSION_CATEGORY,
    SCOPE_PANEL,
    AppliedModifier,
    AttrModelError,
    Formula,
    ResolveResult,
    StatEffect,
    UnmodeledSource,
    attr_attack_fields,
    split_affix_cap,
    working_fields,
)

#: 词条满值查询：(等级, 词条类别) → 满值；查不到返回 None
CapsLookup = Callable[[int, str], float | None]

#: 反解迭代上限。加性管线通常一轮即收敛，公式互相依赖时多几轮。
_MAX_SOLVE_ITERATIONS = 8
_SOLVE_TOLERANCE = 1e-9

#: 浮点累加的规整精度，与 graduation 的做法一致，避免 0.1+0.2 噪声
_NORMALIZE_DIGITS = 9

#: 反解产生的合成来源标识
RESIDUAL_SOURCE_ID = "__residual__"


def _combat_numeric_fields() -> tuple[str, ...]:
    return tuple(
        f.name for f in dataclass_fields(CombatAttributes) if f.name != "extra_attrs"
    )


def _normalize(value: float) -> float:
    return round(value, _NORMALIZE_DIGITS)


def expand_full_affix(
    effect: StatEffect,
    *,
    level: int,
    school_attr: str,
    caps_lookup: CapsLookup,
) -> dict[str, float]:
    """把条目级的「一整条词条」展开成具体字段的数值

    区间词条拆成最小 + 最大两个字段（和为满值），单值词条整条落在
    一个字段上。数值全部来自 affix_caps，配置里不写死。
    """
    declaration = effect.full_affix
    if declaration is None:
        return {}

    category = declaration.category
    cap = caps_lookup(level, category)
    if cap is None:
        raise AttrModelError(
            tr("{label}: 等级 {level} 没有词条 {category} 的上限配置").format(
                label=effect.label, level=level, category=category
            )
        )

    if category == ATTR_ATTACK_CATEGORY:
        low_field, high_field = attr_attack_fields(school_attr)
    elif category == DIMENSION_CATEGORY:
        # 五维整条词条同时加在五个维度上是游戏事实之外的臆测，
        # 这里要求配置显式指定落在哪一维，避免猜测。
        raise AttrModelError(
            tr("{label}: 五维词条需在 stats 中显式指定维度，不支持 full_affix").format(
                label=effect.label
            )
        )
    elif category in AFFIX_RANGE_FIELDS:
        low_field, high_field = AFFIX_RANGE_FIELDS[category]
    elif category in AFFIX_SCALAR_FIELDS:
        return {AFFIX_SCALAR_FIELDS[category]: cap}
    else:
        raise AttrModelError(
            tr("{label}: 词条 {category} 未登记字段映射").format(
                label=effect.label, category=category
            )
        )

    low, high = split_affix_cap(cap, declaration.split)
    return {low_field: low, high_field: high}


def _resolve_scope(
    effects: list[StatEffect],
    *,
    level: int,
    school_attr: str,
    caps_lookup: CapsLookup,
    include_combat_only: bool,
    residual: dict[str, float] | None,
) -> tuple[CombatAttributes, list[AppliedModifier]]:
    """单个作用域的两趟求值"""
    numeric_fields = _combat_numeric_fields()
    allowed = set(working_fields(numeric_fields))
    working: dict[str, float] = {name: 0.0 for name in allowed}
    extra: dict[str, float] = {}
    modifiers: list[AppliedModifier] = []

    selected = [
        effect
        for effect in effects
        if effect.modeled
        and (include_combat_only or effect.scope == SCOPE_PANEL)
    ]

    def record(effect: StatEffect, name: str, delta: float) -> None:
        before = working[name]
        after = _normalize(before + delta)
        working[name] = after
        modifiers.append(
            AppliedModifier(
                source_id=effect.source_id,
                label=effect.label,
                kind=effect.kind,
                scope=effect.scope,
                field_name=name,
                before=before,
                after=after,
            )
        )

    # 第一趟：常数与整条词条
    for effect in selected:
        expanded = expand_full_affix(
            effect, level=level, school_attr=school_attr, caps_lookup=caps_lookup
        )
        for name, affix_value in expanded.items():
            record(effect, name, affix_value)
        for name, stat_value in effect.stats.items():
            if isinstance(stat_value, Formula):
                continue
            if name not in allowed:
                raise AttrModelError(
                    tr("{label}: 未知属性字段 {field}").format(
                        label=effect.label, field=name
                    )
                )
            record(effect, name, float(stat_value))
        for name, extra_value in effect.extra.items():
            extra[name] = _normalize(extra.get(name, 0.0) + float(extra_value))

    # 第二趟：公式。源字段此时已是最终值，与书写顺序无关。
    for effect in selected:
        for name, stat_value in effect.stats.items():
            if not isinstance(stat_value, Formula):
                continue
            if name not in allowed:
                raise AttrModelError(
                    tr("{label}: 未知属性字段 {field}").format(
                        label=effect.label, field=name
                    )
                )
            if stat_value.source not in allowed:
                raise AttrModelError(
                    tr("{label}: 公式引用了未知源字段 {source}").format(
                        label=effect.label, source=stat_value.source
                    )
                )
            resolved = stat_value.apply(working)
            if resolved is None:
                continue
            record(effect, name, resolved)

    # 残差：未建模来源的兜底，等价于用户手填的那部分
    if residual:
        for name, value in residual.items():
            if name not in allowed or not value:
                continue
            before = working[name]
            after = _normalize(before + float(value))
            working[name] = after
            modifiers.append(
                AppliedModifier(
                    source_id=RESIDUAL_SOURCE_ID,
                    label=tr("手填补足"),
                    kind="base",
                    scope=SCOPE_PANEL,
                    field_name=name,
                    before=before,
                    after=after,
                )
            )

    attrs = CombatAttributes(
        **{name: working[name] for name in numeric_fields},
        extra_attrs=extra,
    )
    return attrs, modifiers


def resolve(
    effects: list[StatEffect],
    *,
    level: int,
    school_attr: str,
    caps_lookup: CapsLookup,
    residual: dict[str, float] | None = None,
) -> ResolveResult:
    """求值全部来源，产出面板属性与战斗属性双出口

    Args:
        effects: 已解析的来源条目
        level: 当前赛季装备等级，决定 full_affix 取哪一档满值
        school_attr: 流派属性（通用/鸣金/牵丝/裂石/破竹），
            决定属性攻击词条落到哪对字段
        caps_lookup: 词条满值查询
        residual: 反解得到的手填补足，见 :func:`solve_residual`
    """
    panel_attrs, panel_modifiers = _resolve_scope(
        effects,
        level=level,
        school_attr=school_attr,
        caps_lookup=caps_lookup,
        include_combat_only=False,
        residual=residual,
    )
    combat_attrs, combat_modifiers = _resolve_scope(
        effects,
        level=level,
        school_attr=school_attr,
        caps_lookup=caps_lookup,
        include_combat_only=True,
        residual=residual,
    )
    unmodeled = [
        UnmodeledSource(effect.source_id, effect.label, effect.kind)
        for effect in effects
        if not effect.modeled
    ]
    if unmodeled:
        logger.debug(
            "属性来源未建模条目 {count} 个，缺口由 residual 兜底", count=len(unmodeled)
        )
    return ResolveResult(
        panel_attrs=panel_attrs,
        combat_attrs=combat_attrs,
        modifiers=combat_modifiers if combat_modifiers else panel_modifiers,
        unmodeled=unmodeled,
    )


def solve_residual(
    effects: list[StatEffect],
    targets: dict[str, float],
    *,
    level: int,
    school_attr: str,
    caps_lookup: CapsLookup,
) -> dict[str, float]:
    """反解：求出让面板属性等于 targets 所需的手填补足

    模型已覆盖的来源走推导，没覆盖的由本函数补齐，两者相加恰好等于
    用户实际面板。于是来源只建了一半也能得到正确的总量，不必等全部
    填完才可用。

    公式来源会让「改一个字段」影响另一个字段（例如敏影响外功攻击），
    所以逐轮修正而不是一次相减。
    """
    residual: dict[str, float] = {name: 0.0 for name in targets}
    for _ in range(_MAX_SOLVE_ITERATIONS):
        result = _resolve_scope(
            effects,
            level=level,
            school_attr=school_attr,
            caps_lookup=caps_lookup,
            include_combat_only=False,
            residual=residual,
        )[0]
        largest = 0.0
        for name, target in targets.items():
            current = getattr(result, name, None)
            if current is None:
                raise AttrModelError(
                    tr("反解目标含未知字段: {field}").format(field=name)
                )
            correction = target - current
            residual[name] = _normalize(residual[name] + correction)
            largest = max(largest, abs(correction))
        if largest < _SOLVE_TOLERANCE:
            break
    return {name: value for name, value in residual.items() if value}


def diff_against_panel(
    result: ResolveResult, panel: CombatAttributes
) -> dict[str, tuple[float, float]]:
    """模型面板属性与实测面板的逐字段差异

    返回 ``{字段: (模型值, 实测值)}``，只含不一致的字段。配合
    :meth:`ResolveResult.contribution_by_kind` 即可定位到出错的来源。
    """
    diff: dict[str, tuple[float, float]] = {}
    for name in _combat_numeric_fields():
        modeled = getattr(result.panel_attrs, name)
        actual = getattr(panel, name)
        if abs(modeled - actual) > 1e-6:
            diff[name] = (modeled, actual)
    return diff
