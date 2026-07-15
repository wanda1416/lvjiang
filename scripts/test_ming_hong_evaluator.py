"""鸣金·虹流派装备评估测试

对宛元芷的装备数据进行清洗 → 评分 → 评级。

用法: python scripts/test_ming_hong_evaluator.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lvjiang.equip_parser import EquipmentParser
from lvjiang.evaluator.ming_hong import MingHongEvaluator


# 部位中文名映射
SLOT_CN = {
    "main_weapon": "主武器",
    "sub_weapon": "副武器",
    "ring": "环",
    "pendant": "佩",
    "head": "冠胄",
    "chest": "胸甲",
    "leg": "胫甲",
    "wrist": "腕甲",
}

RATING_ICON = {
    "传家宝": "★★★",
    "合格装备": "★★",
    "凑合装备": "★",
    "垃圾装备": "✗",
}


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def print_result(slot: str, equip, result):
    """格式化输出单件评估结果"""
    cn = SLOT_CN.get(slot, slot)
    icon = RATING_ICON.get(result.rating.value, "?")
    type_str = equip.type or "?"
    chengyin = "承音" if equip.is_chengyin else "普通"

    print(f"\n{'─' * 50}")
    print(f"  [{cn}] {equip.name or '?'} ({type_str}) Lv{equip.level or '?'} {chengyin}")
    print(f"{'─' * 50}")

    # 词条
    for i, af in enumerate(equip.affixes, 1):
        t = "[转]" if af.is_transferred else ""
        u = af.unit or ""
        marker = " ①" if i == 1 else ""
        print(f"    词条{i}{marker}: {t}{af.name} {af.value}{u}")

    # 评估详情
    print()
    if result.disqualified:
        print(f"    结果: ✗ 不合格")
        for r in result.disqualify_reasons:
            print(f"      原因: {r}")
    else:
        print(f"    结果: {icon} {result.rating.value} (扣分 {result.deductions})")

    for d in result.details:
        print(f"      {d}")

    # 清洗警告
    for w in equip.warnings:
        print(f"      !! {w}")


def main():
    # ── 加载数据 ──
    data_path = ROOT / "config" / "local" / "origin" / "宛元芷" / "equipments.json"
    if not data_path.exists():
        print(f"未找到数据文件: {data_path}")
        return 1

    raw_data = load_json(data_path)

    # ── Step 1: 清洗 ──
    parser = EquipmentParser()
    equips = parser.parse_all(raw_data)

    print(f"{'=' * 50}")
    print(f"  鸣金·虹流派装备评估 — 宛元芷")
    print(f"  清洗完成: {len(equips)} 件装备")
    print(f"{'=' * 50}")

    # ── Step 2: 评估 ──
    evaluator = MingHongEvaluator()
    results = evaluator.evaluate_all(equips)

    # ── Step 3: 输出 ──
    for slot in ["main_weapon", "sub_weapon", "ring", "pendant",
                  "head", "chest", "leg", "wrist"]:
        if slot in results:
            print_result(slot, equips[slot], results[slot])

    # ── 汇总 ──
    print(f"\n{'=' * 50}")
    print(f"  汇总")
    print(f"{'=' * 50}")

    from collections import Counter
    rating_count = Counter(r.rating.value for r in results.values())
    for rating_name in ["传家宝", "合格装备", "凑合装备", "垃圾装备"]:
        count = rating_count.get(rating_name, 0)
        if count:
            print(f"    {RATING_ICON.get(rating_name, '?')} {rating_name}: {count}")

    # JSON 输出
    print(f"\n\n── JSON 输出 ──\n")
    json_output = {
        slot: r.to_dict() for slot, r in results.items()
    }
    print(json.dumps(json_output, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
