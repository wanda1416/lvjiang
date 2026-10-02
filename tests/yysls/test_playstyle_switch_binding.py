"""玩法绑定开关：九剑 / 走地玉 自带 keep_danti。

这两条绑定在 0.10.0 的配置重构里丢过一次——玩法定义从规则内嵌改成公共配置
`game_config/playstyles.yaml` 时，原来写在玩法里的 `switch: keep_danti` 没有跟着
搬到规则的 `playstyle_switches`，于是机制还在、数据没了，一直到 0.13.8 才被发现。

所以这里断言的是**行为**而不是 YAML 字段：全局开关关着时，绑定了开关的玩法仍按
"保留单体奇术增"判定，没绑定的玩法照旧判垃圾。字段写法可以再改，这个结论不能变。

毕业表侧的依据：鸣金·影（九剑）与牵丝·玉（走地玉）的竞速毕业表 `single_qs_bonus`
都不为零，而 `group_qs_bonus` 全为零——它们确实要求单体奇术，且只要单体。
"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.evaluator.registry import get_tuning_judge
from lvjiang.apps.yysls.core.tuning_rules.manager import get_tuning_rule_manager


def _helm(affix_names: list[str], level: int = 110) -> EquipmentData:
    return EquipmentData(
        type="冠胄", name="测试装备", level=level, quality="purple",
        affixes=[Affix(name=name, value=1.0) for name in affix_names],
    )


#: 两个规则组的冠胄首词条和顶级条件不同，所以各用自己那套词条构造顶级形状。
_CASES = [
    pytest.param(
        "huiyi_general", "九剑", "无名",
        ["会意率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
        id="通用会意-九剑",
    ),
    pytest.param(
        "huixin_yuyu", "走地玉", "飞天玉",
        ["精准率", "单体类奇术增伤", "最大外功攻击", "敏", "会心率"],
        id="玉玉大王-走地玉",
    ),
]


@pytest.mark.parametrize("rule_key,bound,unbound,affixes", _CASES)
def test_bound_playstyle_keeps_single_target_qishu_without_the_global_switch(
    rule_key, bound, unbound, affixes,
):
    """绑定了 keep_danti 的玩法，全局开关关着也按开着判。

    这是绑定存在的全部意义：单体奇术增对这些玩法是必需词条，不该要求用户每次
    手动勾一个对所有玩法生效的全局开关。
    """
    equip = _helm(affixes)

    bound_judge = get_tuning_judge(rule_key, {"playstyles": [bound]})
    unbound_judge = get_tuning_judge(rule_key, {"playstyles": [unbound]})

    assert bound_judge.judge(equip).rating.name == "TOP"
    assert unbound_judge.judge(equip).rating.name == "JUNK", (
        "同组里没绑定的玩法必须保持原判定，否则等于把开关改成了全组默认")


@pytest.mark.parametrize("rule_key,bound,unbound,_affixes", _CASES)
def test_binding_is_declared_on_the_rule_not_the_shared_playstyle(
    rule_key, bound, unbound, _affixes,
):
    """绑定写在规则的 playstyle_switches 里，不写进公共玩法定义。

    开关控制的是判定口径，属于规则的事：同一个玩法在不同规则下可以绑不同开关、
    甚至不绑。公共玩法定义里的 `qishu_requirement` 只描述玩法本身。
    """
    rule = get_tuning_rule_manager().get_rule(rule_key)
    assert rule is not None

    assert rule.playstyles[bound].switch == "keep_danti"
    assert rule.playstyles[unbound].switch is None
    assert "keep_danti" in rule.referenced_switches(), (
        "被引用的开关不能从注册表里删掉，这条靠 referenced_switches 兜住")
