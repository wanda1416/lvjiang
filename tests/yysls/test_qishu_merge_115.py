"""115 起奇术词条合并后，调律规则与潜力评级都要认新名字。

游戏在 115 把单体/群体类奇术增伤并成全奇术增伤。规则是跨等级复用的，只认
旧名字的话 115 装备既判不出顶级、垃圾条件也恒不命中；潜力评级若不按等级
过滤填充候选，还会按该等级已经调不出来的词条算出一个到不了的上限。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.evaluator.registry import get_tuning_judge

_RULES_DIR = Path(__file__).parents[2] / "config/system/yysls/tuning_rules"


@pytest.fixture
def gc():
    return get_game_config()


# ─── 调律规则必须同时认新旧两个名字 ────────────────────────

def _lists(node):
    """递归取出 YAML 里的全部字符串列表。"""
    if isinstance(node, list):
        if all(isinstance(item, str) for item in node):
            yield node
        for item in node:
            yield from _lists(item)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _lists(value)


def test_every_rule_accepting_the_old_name_also_accepts_the_upgraded_one(gc):
    """规则里出现旧词条的地方必须并列新词条，否则新等阶上永远不命中。

    规则是跨等级复用的：同一条规则要同时管 110 和 115 的装备。旧名字在 115
    已经调不出来、新名字在 110 还不存在，只写一个就会有一端失效——顶级条件
    判不出顶级，垃圾条件恒真。

    映射取自升级表，以后再合并别的词条，这条用例自动覆盖。
    """
    upgrades = {rule.from_name: rule.to_name for rule in gc.get_affix_upgrades()}
    assert upgrades, "升级表为空，本用例失去意义"

    missing: list[str] = []
    for path in sorted(_RULES_DIR.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for names in _lists(document):
            for old, new in upgrades.items():
                if old in names and new not in names:
                    missing.append(f"{path.name}: {names}")
    assert not missing, "这些位置只写了旧词条：\n" + "\n".join(missing)


def _helm(affix_names: list[str], level: int) -> EquipmentData:
    return EquipmentData(
        type="冠胄", name="测试装备", level=level, quality="purple",
        affixes=[Affix(name=name, value=1.0) for name in affix_names],
    )


#: keep_danti 的名字没改，语义扩成「单体（<115）与全奇术（115）都算」。
_DANTI_HELM = ["会意率", "{qishu}", "最大外功攻击", "劲", "势"]


@pytest.mark.parametrize("qishu,level", [
    ("单体类奇术增伤", 110),
    ("全奇术增伤", 115),
])
def test_keep_danti_covers_both_the_old_and_the_merged_affix(qishu, level):
    """开关开着时两个名字都进顶级条件，关着时两个都判垃圾。

    115 只会出全奇术增伤；只认旧名字的话，这个部位在新等阶上既判不出顶级、
    垃圾条件也恒不命中，开关等于失效。
    """
    equip = _helm([name.format(qishu=qishu) for name in _DANTI_HELM], level)

    off = get_tuning_judge("huiyi_general", {"playstyles": ["无名"]})
    assert off.judge(equip).rating.name == "JUNK"

    on = get_tuning_judge("huiyi_general", {"switches": {"keep_danti": True}})
    assert on.judge(equip).rating.name == "TOP"


# ─── 潜力评级按等级过滤填充候选 ────────────────────────────

def test_potential_fill_does_not_offer_an_affix_retired_at_this_level():
    """潜力判定按可用词条库填空槽，不能拿该等级已经调不出来的词条充数。

    不过滤的话，115 装备会按 110 才有的词条算出一个到不了的上限。
    """
    judge = get_tuning_judge("huiyi_general")
    equip = EquipmentData(
        type="冠胄", name="测试装备", level=115, quality="gold",
        affixes=[Affix(name="精准率", value=1.0)],
    )

    result = judge.check_tuning_worthiness(equip)

    filled = {affix.name for affix in result.equipment.affixes}
    assert "单体类奇术增伤" not in filled
    assert "群体类奇术增伤" not in filled
