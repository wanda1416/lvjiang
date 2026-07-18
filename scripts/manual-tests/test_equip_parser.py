"""装备转换器验证脚本

用 config/local 下的 4 份原始 OCR 数据测试 EquipmentParser。

用法: python scripts/test_equip_parser.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lvjiang.equip_parser import EquipmentParser, EquipmentData


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def print_equip(slot: str, equip):
    """格式化输出单件装备"""
    print(f"\n  [{slot}] {equip.name or '?'} | {equip.type or '?'} | "
          f"Lv{equip.level or '?'} | {'承音' if equip.is_chengyin else '普通'}")

    if equip.base_attr_1:
        print(f"    基础1: {equip.base_attr_1.name} {equip.base_attr_1.value}")
    if equip.base_attr_2:
        print(f"    基础2: {equip.base_attr_2.name} {equip.base_attr_2.value}")

    for i, af in enumerate(equip.affixes, 1):
        t = "[转]" if af.is_transferred else ""
        u = af.unit or ""
        print(f"    词条{i}: {t}{af.name} {af.value}{u}")

    for w in equip.warnings:
        print(f"    !! {w}")


def main():
    parser = EquipmentParser()

    # 查找所有 equipments.json
    data_files = sorted(ROOT.glob("config/local/**/equipments.json"), key=str)
    if not data_files:
        # 也检查 origin
        data_files = sorted(ROOT.glob("config/local/origin/*/equipments.json"), key=str)
        data_files += sorted(ROOT.glob("config/local/users/*/equipments.json"), key=str)

    if not data_files:
        print("未找到 equipments.json 文件")
        return 1

    total_slots = 0
    total_warnings = 0
    total_affixes = 0

    for path in data_files:
        # 相对路径作为标签
        rel = path.relative_to(ROOT / "config" / "local")
        print(f"\n{'=' * 60}")
        print(f"  {rel}")
        print(f"{'=' * 60}")

        raw = load_json(path)
        results = parser.parse_all(raw)

        for slot, equip in results.items():
            print_equip(slot, equip)
            total_slots += 1
            total_warnings += len(equip.warnings)
            total_affixes += len(equip.affixes)

    print(f"\n{'=' * 60}")
    print(f"  汇总: {total_slots} 件装备, {total_affixes} 条词条, {total_warnings} 个警告")
    print(f"{'=' * 60}")

    # ── JSON 输出验证 ──
    print("\n\n── JSON 输出示例（宛元芷 main_weapon）──\n")
    for path in data_files:
        if "宛元芷" in str(path):
            raw = load_json(path)
            results = parser.parse_all(raw)
            mw = results.get("main_weapon")
            if mw:
                print(json.dumps(mw.to_dict(), ensure_ascii=False, indent=2))

                # 往返验证
                restored = EquipmentData.from_dict(mw.to_dict())
                assert restored.to_dict() == mw.to_dict(), "from_dict 往返不一致!"
                print("\n✓ from_dict 往返验证通过")
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
