"""通用评估引擎测试（鸣金虹 YAML 配置）

1. 对宛元芷 8 件装备进行清洗 → 评估 → 验证结果
2. 测试 Mock 最差重评级熔断逻辑

用法: python scripts/test_generic_evaluator.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lvjiang.equip_parser import EquipmentParser, EquipmentData, Affix
from lvjiang.evaluator import GenericEvaluator, load_rule_config
from lvjiang.constants import SYSTEM_RULES_DIR


# ── 常量 ──

SLOT_CN = {
    "main_weapon": "主武器", "sub_weapon": "副武器",
    "ring": "环", "pendant": "佩",
    "head": "冠胄", "chest": "胸甲",
    "leg": "胫甲", "wrist": "腕甲",
}

RATING_ICON = {
    "传家宝": "★★★", "合格装备": "★★",
    "凑合装备": "★", "垃圾装备": "✗",
}


def make_equip(type_, affixes):
    """快速构造测试装备"""
    affix_list = []
    for item in affixes:
        name, value = item[0], item[1]
        unit = item[2] if len(item) > 2 else None
        transferred = item[3] if len(item) > 3 else False
        affix_list.append(Affix(name=name, value=value, unit=unit, is_transferred=transferred))
    return EquipmentData(type=type_, level=110, affixes=affix_list)


# ═══════════════════════════════════════════════════════════
#  Part 1: 宛元芷装备评估
# ═══════════════════════════════════════════════════════════

def test_wanyuanzhi():
    print("=" * 60)
    print("  通用评估引擎测试 — 宛元芷装备")
    print("=" * 60)

    # 加载规则配置
    rules_path = SYSTEM_RULES_DIR / "鸣金虹.yaml"
    config = load_rule_config(rules_path)
    evaluator = GenericEvaluator(config)
    print(f"  规则: {config.name}")

    # 加载原始数据
    data_path = ROOT / "config" / "local" / "origin" / "宛元芷" / "equipments.json"
    with open(data_path, encoding="utf-8") as f:
        raw = json.load(f)

    parser = EquipmentParser()
    equips = parser.parse_all(raw)
    print(f"  清洗完成: {len(equips)} 件装备\n")

    passed = 0
    total = 0

    # 期望结果
    expected = {
        "main_weapon": ("合格装备", 1),
        "sub_weapon":  ("合格装备", 1),
        "ring":        ("合格装备", 1),
        "pendant":     ("合格装备", 1),
        "head":        ("合格装备", 1),
        "chest":       ("合格装备", 1),
        "leg":         ("传家宝", 0),
        "wrist":       ("传家宝", 0),
    }

    for slot, equip in equips.items():
        total += 1
        result = evaluator.evaluate(equip)
        cn = SLOT_CN.get(slot, slot)
        icon = RATING_ICON.get(result.rating.value, "?")
        exp_rating, exp_ded = expected.get(slot, ("?", -1))

        ok = (result.rating.value == exp_rating and result.deductions == exp_ded)
        status = "✓" if ok else "✗ FAIL"
        if ok:
            passed += 1

        print(f"  {status} [{cn}] {equip.name or '?'} ({equip.type or '?'})")
        print(f"    评级: {icon} {result.rating.value} (扣分 {result.deductions})"
              f"  期望: {exp_rating} (扣分 {exp_ded})")
        for d in result.details:
            print(f"      {d}")
        if result.disqualified:
            for r in result.disqualify_reasons:
                print(f"      ✗ {r}")
        print()

    # 汇总
    summary = {}
    for slot, equip in equips.items():
        r = evaluator.evaluate(equip)
        v = r.rating.value
        summary[v] = summary.get(v, 0) + 1

    print("─" * 60)
    print("  汇总")
    print("─" * 60)
    for rating_name in ["传家宝", "合格装备", "凑合装备", "垃圾装备"]:
        if rating_name in summary:
            icon = RATING_ICON.get(rating_name, "?")
            print(f"    {icon} {rating_name}: {summary[rating_name]}")

    print(f"\n  结果: {passed}/{total} 通过")
    return passed, total


# ═══════════════════════════════════════════════════════════
#  Part 2: Mock 熔断测试
# ═══════════════════════════════════════════════════════════

def test_circuit_breaker():
    print(f"\n{'=' * 60}")
    print("  Mock 熔断测试")
    print(f"{'=' * 60}")

    rules_path = SYSTEM_RULES_DIR / "鸣金虹.yaml"
    config = load_rule_config(rules_path)
    evaluator = GenericEvaluator(config)

    passed = 0
    total = 0

    cases = [
        # (name, equip, expect_continue)
        ("仅首词条（正确）",
         make_equip("剑", [("最大外功攻击", 114.1)]),
         True),

        ("首词条异常（武器首词条为会意率）",
         make_equip("剑", [("会意率", 6.6, "%")]),
         False),

        ("首词条 + 1 有效",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("最大外功攻击", 114.1),
         ]),
         True),

        ("首词条 + 1 无效（气血最大值）",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("气血最大值", 8750),
         ]),
         True),  # mock: 移除无效 → 补最佳 → 传家宝

        ("首词条 + 2 无效 → 熔断",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("气血最大值", 8750),
             ("最小牵丝攻击", 45.9),
         ]),
         False),  # mock: 移除1个无效 → 还有1个无效 → 仍垃圾

        ("首词条 + 扣分=1（最大鸣金攻击）",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("最大鸣金攻击", 67.2),
         ]),
         True),

        ("首词条 + 扣分=2 → 继续",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("最大鸣金攻击", 67.2),
             ("最大无相攻击", 64.7),
         ]),
         True),  # mock: 移除鸣金 → 补最佳 → 扣1分 → 合格

        ("首词条 + 1无效 + 扣1分 → 继续（mock可修）",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("气血最大值", 8750),
             ("最大鸣金攻击", 67.2),
         ]),
         True),  # mock: 移除无效 → 补最佳 → 扣1分 → 合格

        ("完整 5 词条无扣分",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("最大外功攻击", 114.1),
             ("劲", 72.2),
             ("会意率", 6.6, "%"),
             ("剑武学增伤", 9.2, "%"),
         ]),
         True),

        ("完整 5 词条扣分=3 → 继续（mock可修）",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("会心率", 11.1, "%"),
             ("精准率", 8.4, "%"),
             ("最大鸣金攻击", 67.2),
             ("剑武学增伤", 9.2, "%"),
         ]),
         True),  # mock: 移除会心率 → 补最佳 → 扣2分 → 凑合

        ("完整 5 词条 2无效+1扣分 → 熔断",
         make_equip("剑", [
             ("最大外功攻击", 114.1),
             ("气血最大值", 8750),     # 无效
             ("最小牵丝攻击", 45.9),   # 无效
             ("最大鸣金攻击", 67.2),   # 扣1分
             ("剑武学增伤", 9.2, "%"),
         ]),
         False),  # mock: 移除1个无效 → 还有1个无效 → 仍垃圾

        ("防具首词条正确（劲）",
         make_equip("胫甲", [("劲", 72.2)]),
         True),

        ("首饰首词条异常（势）",
         make_equip(None, [("势", 74.1)]),
         False),
    ]

    for name, equip, expect_cont in cases:
        total += 1
        advice = evaluator.check_tuning_worthiness(equip)
        ok = advice.should_continue == expect_cont
        status = "✓" if ok else "✗ FAIL"
        if ok:
            passed += 1

        cont = "继续" if advice.should_continue else "停止"
        exp = "继续" if expect_cont else "停止"
        print(f"\n  {status} {name}")
        print(f"    词条数: {len(equip.affixes)} | "
              f"结果: {cont} (期望: {exp}) | "
              f"扣分: {advice.current_deductions} | "
              f"不合格: {advice.invalid_count}")
        for r in advice.reasons:
            print(f"      {r}")

    # ── 模拟调律过程 ──
    print(f"\n{'─' * 60}")
    print(f"  模拟调律过程（逐步添加词条）")
    print(f"{'─' * 60}")

    stages = [
        [("最大外功攻击", 114.1)],
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%")],
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%"),
         ("最大鸣金攻击", 67.2)],
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%"),
         ("最大鸣金攻击", 67.2), ("最大无相攻击", 64.7)],
    ]

    for i, affixes in enumerate(stages, 1):
        total += 1
        equip = make_equip("剑", affixes)
        advice = evaluator.check_tuning_worthiness(equip)

        cont = "继续" if advice.should_continue else "停止"
        affix_names = [af.name for af in equip.affixes]
        print(f"\n    第{i}轮: {affix_names}")
        print(f"      → {cont} | 扣分={advice.current_deductions} "
              f"不合格={advice.invalid_count}")
        for r in advice.reasons:
            print(f"        {r}")

        # 全部应该继续（扣分最多=2，mock 可以修）
        if advice.should_continue:
            passed += 1

    # ── 汇总 ──
    print(f"\n{'=' * 60}")
    print(f"  结果: {passed}/{total} 通过")
    print(f"{'=' * 60}")

    return passed, total


# ═══════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════

def main():
    p1, t1 = test_wanyuanzhi()
    p2, t2 = test_circuit_breaker()

    total_p = p1 + p2
    total_t = t1 + t2
    print(f"\n{'=' * 60}")
    print(f"  总计: {total_p}/{total_t} 通过")
    print(f"{'=' * 60}")

    return 0 if total_p == total_t else 1


if __name__ == "__main__":
    sys.exit(main())
