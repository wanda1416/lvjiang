"""115 起单体/群体类奇术增伤合并成全奇术增伤后的各条消费链路。

游戏在 115 把两条旧奇术词条并成一条新的。配置层已经能表达「哪个等级能新
产出哪条词条」和「跨等级怎么升级」，这里锁住下游：调律规则、潜力评级和
毕业率三条链路都要认新词条，否则 115 装备会悄悄判不出顶级、算不进增伤。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
)
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.evaluator.registry import get_tuning_judge
from lvjiang.apps.yysls.core.graduation import (
    _fold_all_qs_bonus,
    get_graduation_calculator,
)
from lvjiang.apps.yysls.core.graduation.optimal_combo import search_optimal_combo
from lvjiang.apps.yysls.core.graduation.scoring import LoadoutScorer
from lvjiang.apps.yysls.core.graduation.smart_tuning import SmartTuningEvaluator

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


# ─── 毕业率：新词条要进得了公式 ────────────────────────────

def test_merged_affix_becomes_its_own_attribute():
    attrs = aggregate_equipment_attrs({
        "head": {"affix_2": {"name": "全奇术增伤", "value": 10.0, "unit": "%"}},
    })

    assert attrs.all_qs_bonus == pytest.approx(0.1)
    assert attrs.single_qs_bonus == 0.0


def test_old_scheme_folds_the_merged_affix_into_its_single_qishu_input():
    """110 的毕业率表没有全奇术这一路输入，不折算的话这条词条白配。"""
    attrs = CombatAttributes(single_qs_bonus=0.02, all_qs_bonus=0.1)

    folded = _fold_all_qs_bonus(
        attrs, [{"kind": "field", "name": "single_qs_bonus"}])

    assert folded.single_qs_bonus == pytest.approx(0.12)
    assert folded.all_qs_bonus == 0.0
    assert attrs.single_qs_bonus == pytest.approx(0.02)  # 不改入参


def test_scheme_with_its_own_input_is_left_alone():
    """表格补上专门的一列后自动停止折算——判断依据是方案声明了什么输入。"""
    attrs = CombatAttributes(single_qs_bonus=0.02, all_qs_bonus=0.1)
    specs = [{"kind": "field", "name": "single_qs_bonus"},
             {"kind": "field", "name": "all_qs_bonus"}]

    assert _fold_all_qs_bonus(attrs, specs) is attrs


def test_nothing_to_fold_is_a_no_op():
    attrs = CombatAttributes(single_qs_bonus=0.02)

    assert _fold_all_qs_bonus(
        attrs, [{"kind": "field", "name": "single_qs_bonus"}]) is attrs


def test_optimal_combo_uses_the_same_old_model_fallback_as_shared_scorer(gc):
    """最优组合的向量内环不能绕过普通评分器的全奇术兼容层。"""
    calculator = get_graduation_calculator("鸣金·虹", "基础方案")
    assert calculator is not None
    equip = {
        "type": "冠胄", "name": "测试装备", "level": 115,
        "quality": "gold",
        "affix_2": {"name": "全奇术增伤", "value": 10.0, "unit": "%"},
    }
    base = CombatAttributes()
    expected = LoadoutScorer(
        calculator, base, "鸣金·虹", gc).rate({"head": equip})

    results = search_optimal_combo(
        {"head": [equip]}, calculator, base, use_dominance_pruning=False)

    assert results[0]["rate"] == pytest.approx(expected)


def test_smart_tuning_requires_the_level_appropriate_qishu_affix(gc):
    evaluator = SmartTuningEvaluator.__new__(SmartTuningEvaluator)
    evaluator._game_config = gc
    context = SimpleNamespace(playstyle="火拳")

    assert evaluator._required_affix(
        context, "head", {"type": "冠胄", "level": 110},
    ) == "单体类奇术增伤"
    assert evaluator._required_affix(
        context, "head", {"type": "冠胄", "level": 115},
    ) == "全奇术增伤"


def test_judge_dialog_uses_level_appropriate_qishu_candidates(qtbot):
    from lvjiang.apps.yysls.ui.loadout.equip.judge_dialog import EquipAffixEditor

    editor = EquipAffixEditor()
    qtbot.addWidget(editor)
    editor.part_combo.setCurrentIndex(editor.part_combo.findData("冠胄"))

    editor._level_combo.set_level(115)
    at_115 = editor._tuning_candidates()
    assert "全奇术增伤" in at_115
    assert "单体类奇术增伤" not in at_115
    assert "群体类奇术增伤" not in at_115

    editor._level_combo.set_level(110)
    at_110 = editor._tuning_candidates()
    assert "单体类奇术增伤" in at_110
    assert "群体类奇术增伤" in at_110
    assert "全奇术增伤" not in at_110


@pytest.mark.parametrize("part", [
    "武器", "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲",
])
def test_judge_dialog_offers_first_affixes_for_every_part(qtbot, part):
    """每个部位都要有首词条候选。

    首词条池按 group_key（weapon/ring/head/…）配置，拿展示部位名
    （武器/环/冠胄/…）去查会一条都查不到，下拉整个变空、宫位选不了词条，
    而且七个部位一起哑掉——没有用例覆盖的话这种全面失效反而最容易漏。
    """
    from lvjiang.apps.yysls.ui.loadout.equip.judge_dialog import (
        EquipAffixEditor,
    )

    editor = EquipAffixEditor()
    qtbot.addWidget(editor)
    editor.part_combo.setCurrentIndex(editor.part_combo.findData(part))

    assert editor._initial_candidates()


def test_judge_dialog_first_affixes_follow_the_level(qtbot):
    """115 不再首出的词条要从宫位候选里消失，低等阶仍然留着。"""
    from lvjiang.apps.yysls.ui.loadout.equip.judge_dialog import (
        EquipAffixEditor,
    )

    editor = EquipAffixEditor()
    qtbot.addWidget(editor)
    editor.part_combo.setCurrentIndex(editor.part_combo.findData("武器"))

    editor._level_combo.set_level(110)
    assert "最小无相攻击" in editor._initial_candidates()

    editor._level_combo.set_level(115)
    at_115 = editor._initial_candidates()
    assert "最小无相攻击" not in at_115
    assert "最大无相攻击" in at_115
