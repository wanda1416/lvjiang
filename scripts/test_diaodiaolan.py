# -*- coding: utf-8 -*-
"""刁刁蓝装备数据解析 + 通用评估引擎测试 + 品阶推断"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lvjiang.evaluator.rule_config import load_rule_config
from lvjiang.evaluator.generic_evaluator import GenericEvaluator
from lvjiang.evaluator.base import Rating
from lvjiang.evaluator.base_attrs import BaseAttrConfig
from lvjiang.equip_parser import EquipmentParser
from lvjiang.equip_parser.constants import WEAPON_SLOTS


def _extract_base_attr_value(equip, slot_key: str):
    """从 EquipmentData 提取用于品阶推断的属性值。

    Returns:
        value (int) 或 None
    """
    if slot_key in WEAPON_SLOTS:
        ba = equip.base_attr_1
        if ba and ba.name == "外功攻击" and isinstance(ba.value, list):
            return ba.value[1]  # 取 max 值
    else:
        ba = equip.base_attr_1
        if ba:
            return ba.value
    return None


def main():
    rule_path = ROOT / "config" / "rules" / "鸣金虹.yaml"
    raw_path = ROOT / "config" / "local" / "users" / "刁刁蓝" / "equipments.json"

    # 加载规则
    config = load_rule_config(rule_path)
    evaluator = GenericEvaluator(config)
    base_attrs = BaseAttrConfig()

    # 解析原始装备数据
    with open(raw_path, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    parser = EquipmentParser()
    equip_dict = parser.parse_all(raw_data)
    print(f"共解析 {len(equip_dict)} 件装备\n")

    # ── Part 1: 品阶推断 ──
    print("=" * 70)
    print("  品阶推断（基础属性 → 品阶）")
    print("=" * 70)

    for slot, equip in equip_dict.items():
        value = _extract_base_attr_value(equip, slot)
        if value is None:
            print(f"\n  [{slot}] {equip.name} | 无法提取基础属性")
            continue
        quality = base_attrs.infer_quality(slot, equip.level, value)
        q_label = {"gold": "金装", "purple": "紫装", "blue": "蓝装"}.get(quality, "未知")
        print(f"\n  [{slot}] {equip.name} | Lv.{equip.level}")
        print(f"    基础属性值: {value}")
        print(f"    推断品阶: {q_label} ({quality})")

    # ── Part 2: 装备评级 ──
    print("\n" + "=" * 70)
    print("  装备评级结果")
    print("=" * 70)

    for slot, equip in equip_dict.items():
        # 先设置推断出的品阶
        value = _extract_base_attr_value(equip, slot)
        if value is not None:
            q = base_attrs.infer_quality(slot, equip.level, value)
            if q:
                equip.quality = q

        result = evaluator.evaluate(equip)
        affix_str = ", ".join(a.name for a in equip.affixes)
        dq_str = " [不合格]" if result.disqualified else ""
        q_str = f"({equip.quality})" if equip.quality else "(品阶未知)"
        print(f"\n  [{slot}] {equip.name} | {equip.type or '?'} | Lv.{equip.level} {q_str}")
        print(f"    词条({len(equip.affixes)}): {affix_str}")
        print(f"    评级: {result.rating.value}{dq_str} (扣分={result.deductions})")
        for d in result.details:
            print(f"      {d}")
        for r in result.disqualify_reasons:
            print(f"      ✗ {r}")

    # ── Part 3: 调律熔断检测 ──
    print("\n" + "=" * 70)
    print("  调律熔断检测")
    print("=" * 70)

    for slot, equip in equip_dict.items():
        advice = evaluator.check_tuning_worthiness(equip)
        status = "继续" if advice.should_continue else "熔断"
        print(f"\n  [{slot}] {equip.name} | {equip.type or '?'}")
        print(f"    词条: {', '.join(a.name for a in equip.affixes)}")
        print(f"    扣分={advice.current_deductions}  不合格={advice.invalid_count}  → {status}")
        for r in advice.reasons:
            print(f"      {r}")

    print("\n完成。")


if __name__ == "__main__":
    main()
