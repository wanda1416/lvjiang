"""毕业率模型输入适配。

领域属性始终保留游戏中的真实字段；旧社区模型缺少新字段时，只在进入模型
前做临时折算。普通评分器与最优组合向量内环必须共用这里，避免同一套装备
在两个入口得到不同毕业率。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from ..combat.combat_attrs import CombatAttributes


def adapt_attrs_to_model_inputs(
    attrs: CombatAttributes,
    specs: list[dict[str, Any]],
) -> CombatAttributes:
    """按模型声明的输入能力适配属性，不修改传入对象。

    115 起游戏把单体/群体类奇术增伤合并成全奇术增伤。旧模型没有
    ``all_qs_bonus`` 输入时，按当前兼容口径临时折入单体奇术；新版模型
    一旦声明独立输入便保持三个真实字段不变。
    """
    if not attrs.all_qs_bonus:
        return attrs
    names = {spec["name"] for spec in specs if spec["kind"] == "field"}
    if "all_qs_bonus" in names:
        return attrs
    return replace(
        attrs,
        single_qs_bonus=attrs.single_qs_bonus + attrs.all_qs_bonus,
        all_qs_bonus=0.0,
    )
