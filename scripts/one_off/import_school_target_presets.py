# -*- coding: utf-8 -*-
"""一次性生成系统预置出装搭配（来源：外部调律器的流派目标词条分布）。

外部工具按「流派 → 全套八件装备每种词条的条数」给出 20 条目标（合计均为 40 条），
这里把它们展开成八件装备，写入 `config/system/yysls/gear_sets/`。生成后这批 yaml 就是
数据源，脚本只在需要复核这批预置时手动运行：

    python scripts/one_off/import_school_target_presets.py

约定的口径（与开发者确认）：
- 全部按承音数值口径（`chengyin: true`）；
- 装备等级与弓玦等级都是运行态参数，不写进文件；
- 弓玦：会意流派（鸣金·虹／鸣金·影）用「会意」，其余用「会心」；
- 套装：按 `equipment.yaml` 里该流派的推荐攻具套装及其配对防具套装；
- 「牵丝霖小外花」原目标（小外 12 条 + 大牵丝 9 条）互斥——外功攻击要占满
  武器首词条位，属攻就只剩普通位（上限 8 条），因此按 10 条小外修正后收录。
"""
from __future__ import annotations

import sys

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.builds import BuildDefinition, BuildRepository
from lvjiang.apps.yysls.config.equipment_slots import SLOT_SPECS
from lvjiang.apps.yysls.core.combat.equipment_sets import LEFT_SET_SLOTS
from lvjiang.apps.yysls.core.loadout.affix_distribution import distribute_affixes
from lvjiang.apps.yysls.core.tuning_rules.models import dynamic_affix_map
from lvjiang.core.config.resolver import get_resolver

#: 简称 → 项目词条显示名（与 `affixes.yaml` 的 `_aliases` 对齐）。
TERM_NAMES = {
    "大外": "最大外功攻击", "小外": "最小外功攻击",
    "会心": "会心率", "会意": "会意率", "精准": "精准率",
    "势": "势", "劲": "劲", "敏": "敏",
    "首领单位": "对首领单位增伤", "全武学增效": "全武学增效", "奇术增伤": "全奇术增伤",
    "扇武学增": "扇武学增效", "伞武学增": "伞武学增效", "剑武学增": "剑武学增伤",
    "鼓武学增": "舞绫鼓武学增伤", "拳武学增": "手甲武学增伤", "刀武学增": "双刀武学增伤",
    "镖武学增": "绳镖武学增伤", "陌刀武学增": "陌刀武学增伤", "横刀武学增": "横刀武学增伤",
    "大无相": "最大无相攻击", "小无相": "最小无相攻击",
    "大鸣金": "最大鸣金攻击", "小鸣金": "最小鸣金攻击",
    "大牵丝": "最大牵丝攻击", "小牵丝": "最小牵丝攻击",
    "大裂石": "最大裂石攻击", "小裂石": "最小裂石攻击",
    "大破竹": "最大破竹攻击", "小破竹": "最小破竹攻击",
}

#: 会意流派（弓玦用「会意」，其余用「会心」）。
HUIYI_SCHOOLS = ("鸣金·虹", "鸣金·影")

#: (稳定 id, 展示名, 玩法, 词条简称 → 条数)。
PRESETS = (
    ("lieshijun1", "裂石均1", "双切",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "陌刀武学增": 1, "会心": 5, "大裂石": 7, "精准": 3}),
    ("lieshijun2", "裂石均2", "双切",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "陌刀武学增": 1, "会心": 6, "大裂石": 7, "精准": 2}),
    ("lieshijun_chuntang_xiaowai", "裂石均纯唐小外", "纯唐",
     {"小外": 12, "会心": 7, "精准": 3, "敏": 8, "大裂石": 5, "首领单位": 2, "全武学增效": 2, "横刀武学增": 1}),
    ("lieshi_wei_chunwai", "裂石威纯外", "威威",
     {"大外": 12, "会心": 6, "精准": 2, "势": 3, "会意": 1, "劲": 7, "小外": 4,
      "首领单位": 2, "全武学增效": 2, "陌刀武学增": 1}),
    ("lieshi_wei_4lie", "裂石威4裂", "威威",
     {"大外": 12, "会心": 6, "精准": 2, "势": 3, "会意": 1, "劲": 7, "大裂石": 4,
      "首领单位": 2, "全武学增效": 2, "陌刀武学增": 1}),
    ("pozhu_zun", "破竹樽", "樽樽",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "刀武学增": 1, "拳武学增": 1,
      "精准": 3, "会心": 6, "大破竹": 5}),
    ("pozhu_chen", "破竹尘", "尘尘",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "伞武学增": 1, "精准": 2,
      "会心": 6, "大破竹": 7}),
    ("pozhu_feng", "破竹风", "双刀",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "刀武学增": 1, "镖武学增": 1,
      "精准": 3, "会心": 4, "大破竹": 7}),
    ("pozhu_yuan1", "破竹鸢1", "鸢鸢",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "拳武学增": 1, "精准": 2,
      "会心": 6, "会意": 1, "大破竹": 6}),
    ("pozhu_yuan2", "破竹鸢2", "鸢鸢",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "拳武学增": 1, "精准": 2,
      "会心": 6, "大破竹": 7}),
    ("qiansi_yi", "牵丝翊", "翊翊",
     {"小外": 12, "敏": 8, "全武学增效": 2, "首领单位": 2, "鼓武学增": 1, "精准": 3,
      "会心": 6, "大牵丝": 6}),
    ("qiansi_lin_dawai", "牵丝霖大外花", "火拳",
     {"大外": 12, "劲": 10, "首领单位": 2, "奇术增伤": 2, "精准": 3, "会心": 6, "敏": 4, "小外": 1}),
    ("qiansi_lin_xiaowai", "牵丝霖小外花", "火拳",
     {"小外": 10, "敏": 8, "会心": 6, "精准": 3, "首领单位": 2, "奇术增伤": 2, "大牵丝": 9}),
    ("qiansi_yu_chunlei1", "牵丝玉春雷1", "走地玉",
     {"大外": 12, "会心": 8, "精准": 2, "敏": 3, "劲": 10, "首领单位": 2, "全武学增效": 2, "伞武学增": 1}),
    ("qiansi_yu_chunlei2", "牵丝玉春雷2", "走地玉",
     {"大外": 12, "会心": 8, "精准": 2, "敏": 3, "劲": 9, "首领单位": 2, "全武学增效": 2,
      "伞武学增": 1, "扇武学增": 1}),
    ("qiansi_yu_chunlei3", "牵丝玉春雷3", "走地玉",
     {"大外": 12, "会心": 8, "精准": 2, "敏": 3, "劲": 9, "首领单位": 2, "全武学增效": 2,
      "伞武学增": 1, "势": 1}),
    ("qiansi_yu_chunduan", "牵丝玉春断", "走地玉",
     {"大外": 12, "会心": 8, "精准": 1, "敏": 3, "劲": 10, "首领单位": 2, "全武学增效": 2,
      "伞武学增": 1, "扇武学增": 1}),
    ("qiansi_yu_duanshi", "牵丝玉断石", "走地玉",
     {"大外": 12, "会心": 8, "精准": 1, "敏": 2, "劲": 10, "首领单位": 2, "全武学增效": 2,
      "奇术增伤": 2, "伞武学增": 1}),
    ("mingjin_ying", "鸣金影", "九剑",
     {"大外": 12, "会意": 5, "势": 7, "剑武学增": 1, "奇术增伤": 1, "首领单位": 2,
      "全武学增效": 2, "劲": 10}),
    ("mingjin_hong", "鸣金虹", "无名",
     {"大外": 12, "会意": 6, "势": 7, "剑武学增": 1, "首领单位": 2, "全武学增效": 2, "劲": 10}),
)

DIVINE_CATEGORIES = ("增效类", "武器类")


def build_counts(terms: dict[str, int], attribute: str, game_config) -> dict[str, int]:
    """简称词条 → 分配引擎认识的计数（属攻按本属／外属折叠）。"""
    dynamic = dynamic_affix_map(attribute, game_config=game_config)
    counts: dict[str, int] = {}
    for term, count in terms.items():
        name = TERM_NAMES[term]
        key = dynamic.get(name, name)
        counts[key] = counts.get(key, 0) + count
    return counts


def templates_for(playstyle: str, game_config) -> dict[str, dict]:
    """槽位模板：武器取玩法的主副武器，套装取该流派推荐的左右套装。"""
    config = game_config.get_playstyle(playstyle) or {}
    school = str(config.get("school") or "")
    left_set = game_config.recommended_equipment_set(school)
    right_set = str((game_config.get_equipment_sets("left").get(left_set) or {})
                    .get("recommended_right") or "") if left_set else ""
    templates: dict[str, dict] = {}
    for spec in SLOT_SPECS:
        kind = (str(config.get("main_weapon" if spec.key == "main_weapon" else "sub_weapon") or "")
                if spec.is_weapon else spec.part)
        templates[spec.key] = {
            "type": kind,
            "equipment_set": left_set if spec.key in LEFT_SET_SLOTS else right_set,
        }
    return templates


def requirements_for(counts: dict[str, int], game_config) -> list[dict]:
    """把目标条数写成搭配自身的约束；神力词条按强制、其余按最佳。"""
    return [
        {"affix": name, "priority": "required" if game_config.get_affix_category(name) in DIVINE_CATEGORIES
         else "optimal", "minimum": count, "maximum": count}
        for name, count in counts.items()
    ]


def main() -> int:
    resolver = get_resolver()
    if not resolver.is_dev_mode():
        print("需要在开发模式下运行才能写入系统预置", file=sys.stderr)
        return 1
    game_config = get_game_config()
    repository = BuildRepository(resolver)
    level = game_config.current_equip_level()
    existing = {build.id: build for build in repository.all()}
    failures: list[tuple[str, list[str]]] = []

    for slug, name, playstyle, terms in PRESETS:
        config = game_config.get_playstyle(playstyle)
        if not config:
            failures.append((name, [f"玩法「{playstyle}」未登记"]))
            continue
        attribute = str(config.get("attr") or "")
        counts = build_counts(terms, attribute, game_config)
        result = distribute_affixes(counts, templates_for(playstyle, game_config), attribute=attribute,
                                    level=level, chengyin=True, game_config=game_config)
        if not result.feasible:
            failures.append((name, list(result.errors)))
            continue
        build = BuildDefinition(id=slug, name=name, playstyle=playstyle, chengyin=True)
        build.storage = "system"
        build.gongjue = "会意" if str(config.get("school") or "") in HUIYI_SCHOOLS else "会心"
        build.equipment = result.equipment
        build.requirements = requirements_for(counts, game_config)
        current = existing.get(slug)
        try:
            repository.save(build, expected=current.to_dict() if current else None)
        except (ValueError, OSError) as exc:
            failures.append((name, [str(exc)]))
            continue
        print(f"{slug:<28} {name:<10} {playstyle:<5} 词条 {sum(counts.values())} 条 · "
              f"弓玦 {build.gongjue}")

    if failures:
        print("\n以下条目未生成：", file=sys.stderr)
        for name, errors in failures:
            print(f"  {name}: {'；'.join(errors)}", file=sys.stderr)
        return 1
    print(f"\n完成：{len(PRESETS)} 条系统预置已写入 {resolver.system_dir}/yysls/gear_sets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
