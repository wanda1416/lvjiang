"""调律熔断测试

模拟调律过程中不同阶段的装备状态，测试 check_tuning_worthiness。

场景：
1. 仅有首词条（正确） → 继续
2. 首词条错误 → 立即停止
3. 首词条 + 1 个有效非首词条 → 继续
4. 首词条 + 1 个无效非首词条 → 继续（仅 1 个不合格）
5. 首词条 + 2 个无效非首词条 → 熔断（不合格 ≥ 2）
6. 首词条 + 扣分=1 → 继续
7. 首词条 + 扣分=2 → 继续（仍可通过转律救回一条）
8. 完整装备（5 词条，无扣分） → 继续（已完美）
9. 完整装备（5 词条，扣分=1） → 继续（合格）
10. 完整装备（5 词条，扣分=3） → 熔断

用法: python scripts/test_tuning_worthiness.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lvjiang.equip_parser import EquipmentData, Affix
from lvjiang.evaluator.ming_hong import MingHongEvaluator


def make_equip(slot: str, type_: str | None, affixes: list[tuple]) -> EquipmentData:
    """快速构造测试用装备

    affixes: [(name, value, unit?, is_transferred?), ...]
    """
    affix_list = []
    for item in affixes:
        name, value = item[0], item[1]
        unit = item[2] if len(item) > 2 else None
        transferred = item[3] if len(item) > 3 else False
        affix_list.append(Affix(name=name, value=value, unit=unit, is_transferred=transferred))
    return EquipmentData(slot=slot, type=type_, level=110, affixes=affix_list)


def run_test(name: str, equip: EquipmentData, expect_continue: bool):
    """运行单个测试用例"""
    evaluator = MingHongEvaluator()
    advice = evaluator.check_tuning_worthiness(equip)

    status = "✓" if advice.should_continue == expect_continue else "✗ FAIL"
    cont_str = "继续" if advice.should_continue else "停止"
    expect_str = "继续" if expect_continue else "停止"

    print(f"\n  {status} {name}")
    print(f"    词条数: {len(equip.affixes)} | "
          f"结果: {cont_str} (期望: {expect_str}) | "
          f"扣分: {advice.current_deductions} | "
          f"不合格: {advice.invalid_count}")
    for r in advice.reasons:
        print(f"      {r}")

    return advice.should_continue == expect_continue


def main():
    print("=" * 60)
    print("  调律熔断测试")
    print("=" * 60)

    passed = 0
    total = 0

    # ── 1. 仅首词条（正确） → 继续 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
    ])
    if run_test("仅首词条（正确）", equip, expect_continue=True):
        passed += 1

    # ── 2. 首词条错误 → 停止 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("会意率", 6.6, "%"),  # 武器首词条应为 最大外功攻击/势
    ])
    if run_test("首词条错误（武器首词条为会意率）", equip, expect_continue=False):
        passed += 1

    # ── 3. 首词条 + 1 个有效非首词条 → 继续 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("最大外功攻击", 114.1),  # 有效
    ])
    if run_test("首词条 + 1 有效", equip, expect_continue=True):
        passed += 1

    # ── 4. 首词条 + 1 个无效非首词条 → 继续 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("气血最大值", 8750),  # 无效（不在鸣金虹有效集合中）
    ])
    if run_test("首词条 + 1 无效（气血最大值）", equip, expect_continue=True):
        passed += 1

    # ── 5. 首词条 + 2 个无效非首词条 → 熔断 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("气血最大值", 8750),   # 无效
        ("最小牵丝攻击", 45.9), # 无效
    ])
    if run_test("首词条 + 2 无效 → 熔断", equip, expect_continue=False):
        passed += 1

    # ── 6. 首词条 + 扣分=1 → 继续 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("最大鸣金攻击", 67.2),  # 扣分 -1
    ])
    if run_test("首词条 + 扣分=1（最大鸣金攻击）", equip, expect_continue=True):
        passed += 1

    # ── 7. 首词条 + 扣分=2 → 继续（仍可救回） ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("最大鸣金攻击", 67.2),  # 扣分 -1
        ("最大无相攻击", 64.7),  # 扣分 -1 → 总扣分 2
    ])
    if run_test("首词条 + 扣分=2 → 继续（可救回）", equip, expect_continue=True):
        passed += 1

    # ── 8. 完整装备（5 词条，无扣分） → 继续 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("最大外功攻击", 114.1),
        ("劲", 72.2),
        ("会意率", 6.6, "%"),
        ("剑武学增伤", 9.2, "%"),
    ])
    if run_test("完整 5 词条无扣分", equip, expect_continue=True):
        passed += 1

    # ── 9. 完整装备（扣分=1） → 继续 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("会意率", 6.6, "%"),     # 势+会意率 扣分
        ("势", 72.2),             # 势+会意率 扣分
        ("最大外功攻击", 114.1),
        ("剑武学增伤", 9.2, "%"),
    ])
    if run_test("完整 5 词条扣分=1（势+会意率）", equip, expect_continue=True):
        passed += 1

    # ── 10. 完整装备（扣分=3） → 熔断 ──
    total += 1
    equip = make_equip("main_weapon", "剑", [
        ("最大外功攻击", 114.1),
        ("会心率", 11.1, "%"),    # 扣分 -1
        ("精准率", 8.4, "%"),     # 扣分 -1
        ("最大鸣金攻击", 67.2),   # 扣分 -1 → 总 3
        ("剑武学增伤", 9.2, "%"),
    ])
    if run_test("完整 5 词条扣分=3 → 熔断", equip, expect_continue=False):
        passed += 1

    # ── 11. 防具首词条正确（劲） + 无后续 → 继续 ──
    total += 1
    equip = make_equip("leg", "胫甲", [
        ("劲", 72.2),
    ])
    if run_test("防具首词条正确（劲）", equip, expect_continue=True):
        passed += 1

    # ── 12. 首饰首词条错误 → 停止 ──
    total += 1
    equip = make_equip("ring", None, [
        ("势", 74.1),  # 首饰首词条应为 最大外功攻击
    ])
    if run_test("首饰首词条错误（势）", equip, expect_continue=False):
        passed += 1

    # ── 13. 模拟调律过程：逐步添加词条 ──
    print(f"\n{'─' * 60}")
    print(f"  模拟调律过程（逐步添加词条）")
    print(f"{'─' * 60}")

    stages = [
        [("最大外功攻击", 114.1)],                                    # 仅首词条
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%")],              # +会意率
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%"),
         ("最大鸣金攻击", 67.2)],                                      # +鸣金（扣分=1）
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%"),
         ("最大鸣金攻击", 67.2), ("最大无相攻击", 64.7)],             # +无相（扣分=2→继续）
        [("最大外功攻击", 114.1), ("会意率", 6.6, "%"),
         ("最大鸣金攻击", 67.2), ("最大无相攻击", 64.7),
         ("精准率", 8.4, "%")],                                        # +精准（扣分=3→熔断）
    ]

    for i, affixes in enumerate(stages, 1):
        total += 1
        equip = make_equip("main_weapon", "剑", affixes)
        evaluator = MingHongEvaluator()
        advice = evaluator.check_tuning_worthiness(equip)

        cont_str = "继续" if advice.should_continue else "停止"
        affix_names = [af.name for af in equip.affixes]
        print(f"\n    第{i}轮: {affix_names}")
        print(f"      → {cont_str} | 扣分={advice.current_deductions} "
              f"不合格={advice.invalid_count}")
        for r in advice.reasons:
            print(f"        {r}")

        # 第 5 轮应该熔断（扣分=3）
        expect = (i < 5)
        if advice.should_continue == expect:
            passed += 1

    # ── 汇总 ──
    print(f"\n{'=' * 60}")
    print(f"  结果: {passed}/{total} 通过")
    print(f"{'=' * 60}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
