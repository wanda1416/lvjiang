#!/usr/bin/env python3
"""导出网页端毕业率计算所需的游戏资产到 ``server/web/public/assets/yysls/``。

单一真源仍是 `config/system/yysls/` ——这个脚本不重新实现任何派生逻辑
（别名归并、承音上限、词条等级范围、品阶区间……），只是把 ``GameConfigManager``
已经算好的结果落一份地，给不方便依赖整个 Python 包的网页端读。网页端因此
不需要 YAML 解析器，也不需要重写一遍配置装载规则。

产物三件套：

- ``config.json``：规则表（词条上限/等级范围/部位/套装/流派/玩法/抗性……）；
- ``manifest.json``：模型索引（流派/方案/等级/版本 → 分片路径与摘要）；
- ``models/<等级>-<序号>.json``：毕业率模型实体，按需懒加载。

模型合计约 6 MB，绝不能塞进 JS bundle：网页只先取 manifest，算哪个方案才拉
哪个分片。

用法：

    python scripts/export_yysls_web_assets.py           # 写盘
    python scripts/export_yysls_web_assets.py --check   # 只比对，有漂移则返回 1

游戏配置或毕业率模型改动后重新跑一遍；``--check`` 供 CI/提交前做漂移门禁。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from lvjiang.apps.yysls.config import (  # noqa: E402
    BASE_ATTR_PARTS,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    WUXUE_CATEGORY,
    get_base_attribute_presets,
    get_game_config,
)
from lvjiang.apps.yysls.core.graduation.model_registry import (  # noqa: E402
    list_graduation_models,
    load_model,
)

_DEFAULT_DEST = _REPO_ROOT / "server/web/public/assets/yysls"
_CONFIG_NAME = "config.json"
_MANIFEST_NAME = "manifest.json"
_MODELS_DIR = "models"
#: 防具定音所在的词条类别（与 config/arts_queries.get_playstyle_dingyin 同源：
#: 攻具/防具的划分直接用这个类别自己的 _parts 声明，不另建分组）。
_SKILL_DINGYIN_CATEGORY = "指定技能增效"


def _dump(data: object) -> bytes:
    """统一落盘格式：UTF-8、中文不转义、两空格缩进、末尾换行。"""
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


# ─── 规则表 ────────────────────────────────────────────────


def _affix_tables(gc) -> dict:
    """词条相关表：上限、单位、叠加方式、词库、别名、部位、等级范围。"""
    caps: dict[str, dict[str, dict]] = {}
    units: dict[str, str] = {}
    for category in gc.get_all_affix_categories():
        caps[category] = {}
        for level in gc.get_affix_cap_levels(category):
            entry = gc.get_affix_caps(level, category)
            if entry is None:
                continue
            caps[category][str(level)] = {
                "cap": entry["cap"], "chengyin": entry["chengyin"]}
            units[category] = entry["unit"]

    aliases = {
        affix: gc.resolve_affix_category(affix)
        for affix in gc.get_normal_affix_names() + gc.get_dingyin_affix_names()
    }
    # 词组级属性（叠加方式/词库）按别名查询——get_affix_stack 只认别名。
    representative: dict[str, str] = {}
    for affix, category in aliases.items():
        representative.setdefault(category, affix)
    # 覆盖式部位声明单独导出：绝大多数词条走「分类级 _parts → 全部七部位」，
    # 只把真正与分类不同的少数几个记下来。
    parts_override: dict[str, list[str]] = {}
    category_parts: dict[str, list[str]] = {}
    for affix, category in aliases.items():
        category_parts.setdefault(category, gc.get_affix_category_parts(category))
        declared = gc.get_affix_parts(affix)
        fallback = category_parts[category] or list(EQUIP_PART_NAMES)
        if declared != fallback:
            parts_override[affix] = declared

    return {
        "affix_caps": caps,
        "affix_units": units,
        "affix_stacks": {
            category: "max"
            for category, affix in representative.items()
            if gc.get_affix_stack(affix) == "max"
        },
        "affix_pools": {
            category: POOL_DINGYIN
            for category in caps
            if gc.get_affix_pool(category) == POOL_DINGYIN
        },
        "alias_to_category": aliases,
        "category_parts": category_parts,
        "parts_override": parts_override,
        "part_names": list(EQUIP_PART_NAMES),
        "normal_affixes": list(gc.get_normal_affix_names()),
        "dingyin_affixes": list(gc.get_dingyin_affix_names()),
        "wuxue_affixes": list(gc.get_aliases_for_category(WUXUE_CATEGORY)),
        "affix_levels": {
            affix: [level_range.from_level, level_range.through_level]
            for affix in aliases
            if (level_range := gc.get_affix_level_range(affix)).configured
        },
        "affix_upgrades": [
            {
                "from_level": rule.from_level, "to_level": rule.to_level,
                "from_name": rule.from_name, "to_name": rule.to_name,
                "operation": rule.operation,
            }
            for rule in gc.get_affix_upgrades()
        ],
    }


def _candidate_levels(gc) -> list[int]:
    """所有配置了数值的装备等级（等级配置 ∪ 赛季 ∪ 词条上限表）。"""
    levels = {cfg.level for cfg in gc.get_level_configs()}
    levels.update(int(cfg.equip_level or 0) for cfg in gc.get_season_configs())
    for category in gc.get_all_affix_categories():
        levels.update(gc.get_affix_cap_levels(category))
    levels.discard(0)
    return sorted(levels)


def _base_attr_table(gc) -> dict:
    """装备基础属性区间：部位 → 等级 → 品阶 → [min, max]。"""
    levels = _candidate_levels(gc)
    result: dict[str, dict] = {}
    for part in BASE_ATTR_PARTS:
        by_level: dict[str, dict[str, list]] = {}
        for level in levels:
            by_quality: dict[str, list] = {}
            for quality in ("gold", "purple", "blue"):
                low, high = gc.get_base_attr_values(part, level, quality)
                if low is not None:
                    by_quality[quality] = [low, high]
            if by_quality:
                by_level[str(level)] = by_quality
        if by_level:
            result[part] = by_level
    return result


def _season_table(gc) -> dict:
    levels = {
        str(cfg.level): {
            "judge_resistance": float(cfg.judge_resistance or 0),
            "buff_resistance": float(cfg.buff_resistance or 0),
        }
        for cfg in gc.get_level_configs()
    }
    seasons = [
        {
            "season_number": cfg.season_number,
            "name": cfg.name,
            "start_date": cfg.start_date.isoformat() if cfg.start_date else "",
            "end_date": cfg.end_date.isoformat() if cfg.end_date else "",
            "equip_level": int(cfg.equip_level or 0),
            "gongjue_level": int(cfg.gongjue_level or cfg.equip_level or 0),
            "min_chengyin_level": int(cfg.min_chengyin_level or 0),
        }
        for cfg in gc.get_season_configs()
    ]
    return {
        "level_configs": levels,
        "seasons": seasons,
        "current_equip_level": int(gc.current_equip_level()),
    }


def _playstyle_affix_table(gc) -> dict[str, list[str]]:
    """每个玩法在模拟计算器里值得列出的普通词条（PC ``build_calculator`` 口径）。

    PC ``_populate_counts``：对包含当前玩法的每条调律规则，保留 `pool_set`
    或动态别名归类（本属/外属视角）命中的词条，再并上玩法的主/副伤害词条。
    这里按全量普通词条预计算**玩法侧集合**——部位/等级合法性由网页端拿
    ``normal_candidates`` 再交一次，两边相乘才是 PC 的最终列表。
    """
    from lvjiang.apps.yysls.core.tuning_rules.manager import get_tuning_rule_manager
    from lvjiang.apps.yysls.core.tuning_rules.models import dynamic_affix_map

    normal = list(gc.get_normal_affix_names())
    rules = list(get_tuning_rule_manager().get_rules().values())
    table: dict[str, list[str]] = {}
    for name, cfg in gc.get_playstyles().items():
        aliases = dynamic_affix_map(str(cfg.get("attr") or ""), game_config=gc)
        keep: set[str] = set()
        for rule in rules:
            if name not in rule.playstyles:
                continue
            pool = rule.pool_set
            keep.update(affix for affix in normal
                        if affix in pool or aliases.get(affix) in pool)
        for key in ("main_damage", "sub_damage"):
            value = str(cfg.get(key) or "")
            if value:
                keep.add(value)
        if keep:
            table[name] = sorted(keep)
    return table


def _talent_scaling_table(gc) -> dict:
    """武学天赋的缩放口径：base_dim 与每流派的系数（与 PC schools.yaml 同源）。"""
    return dict(gc.get_talent_scaling_raw())


def _base_attributes_table(gc) -> dict[str, list[dict]]:
    """导出 system 层预置基础属性（直接读取 system 层，避免被本机 local 覆盖遮蔽）。"""
    table: dict[str, list[dict]] = {}
    for school in gc.get_schools():
        presets = [
            {
                "id": preset.id,
                "level": preset.level,
                "stage": preset.stage,
                "playstyle": preset.playstyle,
                "label": preset.label,
                "stats": dict(preset.stats),
            }
            for preset in get_base_attribute_presets(school, layer="system")
        ]
        if presets:
            table[school] = presets
    return table


def build_config() -> dict:
    """算出完整规则表（不落盘）——测试用它重新算一份与提交的产物比对。"""
    gc = get_game_config()
    defense_dingyin_parts = gc.get_affix_category_parts(_SKILL_DINGYIN_CATEGORY)
    playstyles = {}
    for name, cfg in gc.get_playstyles().items():
        playstyles[name] = {
            "school": cfg.get("school", ""),
            "attr": cfg.get("attr", ""),
            "arts": list(cfg.get("arts") or []),
            "output_dingyin": cfg.get("output_dingyin", ""),
            "defense_dingyin": cfg.get("defense_dingyin", ""),
        }
    # 模拟计算器的词条分配器要用的表：部位规格、首词条候选、普通词条候选、
    # 词条分类上限，以及按**具体词条名** × 等级预计算的上限值（affix_caps 只到
    # 词组级，网页端直接查具体名会全空）。
    from lvjiang.apps.yysls.config.equipment_slots import SLOT_SPECS
    from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates
    from lvjiang.apps.yysls.core.equip_validator import (
        MAX_ATTRIBUTE_AFFIXES,
        MAX_DIVINE_AFFIXES,
        _categories,
    )

    levels = _candidate_levels(gc)
    groups = sorted(set(gc.get_type_to_group().values()))
    equip_types = sorted(gc.get_type_to_group())
    attack_category, divine_categories = _categories()
    first_affix_names = sorted({
        name
        for group in groups
        for level in levels
        for name in gc.get_first_affixes(group, level)
    })
    distribution_tables = {
        "slot_specs": [
            {"key": spec.key, "label": str(spec.label), "part": spec.part,
             "filter_type": spec.filter_type, "is_weapon": bool(spec.is_weapon)}
            for spec in SLOT_SPECS
        ],
        "first_affixes": {
            group: {str(level): list(gc.get_first_affixes(group, level)) for level in levels}
            for group in groups
        },
        "normal_candidates": {
            equip_type: {
                str(level): list(normal_affix_candidates(
                    {"type": equip_type, "level": level}, gc))
                for level in levels
            }
            for equip_type in equip_types
        },
        "attr_affix_max": MAX_ATTRIBUTE_AFFIXES,
        "divine_affix_max": MAX_DIVINE_AFFIXES,
        "affix_categories": {"attack": attack_category, "divine": sorted(divine_categories)},
        "affix_values": {
            name: {
                str(level): dict(entry)
                for level in levels
                if (entry := gc.get_affix_caps(level, name)) is not None
            }
            for name in sorted(set(gc.get_normal_affix_names()) | set(first_affix_names))
        },
        "playstyle_affixes": _playstyle_affix_table(gc),
    }

    config = {
        "_generated_by": "scripts/export_yysls_web_assets.py",
        "_source": "config/system/yysls/",
        "schema_version": 1,
        **_affix_tables(gc),
        **distribution_tables,
        "base_attrs": _base_attr_table(gc),
        "base_attributes": _base_attributes_table(gc),
        **_season_table(gc),
        "equipment_sets": gc.get_equipment_sets(),
        # 武学天赋按五维缩放的系数：网页端要和 PC 一样按「基础五维 + 装备五维」补差。
        "talent_scaling": _talent_scaling_table(gc),
        "gongjue_bonuses": gc.get_gongjue_bonuses(),
        "schools": gc.get_schools(),
        "playstyles": playstyles,
        "defense_dingyin_parts": list(defense_dingyin_parts),
        "type_to_group": gc.get_type_to_group(),
        "group_to_part": gc.get_group_to_part(),
        "weapon_wuxue_affixes": gc.get_all_weapon_wuxue_affixes(),
        "weapons_by_group": {
            group: sorted({
                equip_type for equip_type, key in gc.get_type_to_group().items()
                if key == group and equip_type != "武器"
            })
            for group in ("weapon", "ring", "pendant", "head", "chest", "leg", "wrist")
        },
    }
    return config


# ─── 资产版本与模型分片 ──────────────────────────────────────


def _asset_version(payloads: dict[str, bytes]) -> str:
    """整包摘要：内容一变，网页缓存键就变（不用改文件名）。"""
    digest = hashlib.sha256()
    for name in sorted(payloads):
        digest.update(name.encode("utf-8"))
        digest.update(payloads[name])
    return digest.hexdigest()[:16]


def build_assets() -> dict[str, bytes]:
    """全部产物：相对路径 → 字节内容。"""
    config = build_config()
    models: dict[str, bytes] = {}
    index: list[dict] = []
    per_level: dict[int, int] = {}
    for ref in sorted(
        list_graduation_models(),
        key=lambda item: (item.level, item.school, item.scheme, item.version),
    ):
        sequence = per_level.get(ref.level, 0) + 1
        per_level[ref.level] = sequence
        shard = f"{_MODELS_DIR}/{ref.level}-{sequence:02d}.json"
        payload = _dump(load_model(ref.rel_path))
        models[shard] = payload
        entry_meta: dict[str, object] = {
            "school": ref.school, "scheme": ref.scheme,
            "level": ref.level, "version": ref.version,
            "path": shard, "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()[:16],
        }
        if ref.default_base_attribute:
            entry_meta["default_base_attribute"] = ref.default_base_attribute
        index.append(entry_meta)
    payloads: dict[str, bytes] = {_CONFIG_NAME: _dump(config), **models}
    manifest = {
        "_generated_by": "scripts/export_yysls_web_assets.py",
        "schema_version": 1,
        "assets_version": _asset_version(payloads),
        "levels": sorted(per_level),
        "models": index,
    }
    payloads[_MANIFEST_NAME] = _dump(manifest)
    return payloads


# ─── 写盘 / 比对 ───────────────────────────────────────────


def _write(dest: Path, payloads: dict[str, bytes]) -> list[str]:
    written: list[str] = []
    for name, payload in sorted(payloads.items()):
        path = dest / name
        if path.is_file() and path.read_bytes() == payload:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        written.append(name)
    # 删掉上一轮留下、本轮已经不存在的分片（方案被删除/改版本号）
    for stale in sorted(dest.glob(f"{_MODELS_DIR}/*.json")):
        relative = f"{_MODELS_DIR}/{stale.name}"
        if relative not in payloads:
            stale.unlink()
            written.append(f"-{relative}")
    return written


def _drift(dest: Path, payloads: dict[str, bytes]) -> list[str]:
    problems: list[str] = []
    for name, payload in sorted(payloads.items()):
        path = dest / name
        if not path.is_file():
            problems.append(f"缺失 {name}")
        elif path.read_bytes() != payload:
            problems.append(f"内容不同 {name}")
    for stale in sorted(dest.glob(f"{_MODELS_DIR}/*.json")):
        if f"{_MODELS_DIR}/{stale.name}" not in payloads:
            problems.append(f"多余 {_MODELS_DIR}/{stale.name}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出网页端毕业率资产")
    parser.add_argument("--dest", type=Path, default=_DEFAULT_DEST,
                        help=f"输出目录（默认 {_DEFAULT_DEST}）")
    parser.add_argument("--check", action="store_true",
                        help="只比对现有产物，有漂移则返回非零")
    args = parser.parse_args(argv)

    payloads = build_assets()
    total = sum(len(payload) for payload in payloads.values())
    if args.check:
        problems = _drift(args.dest, payloads)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            print(f"网页资产已过期（{len(problems)} 处），请重新运行 "
                  "python scripts/export_yysls_web_assets.py", file=sys.stderr)
            return 1
        print(f"网页资产与配置一致：{len(payloads)} 个文件，{total} 字节",
              file=sys.stderr)
        return 0

    written = _write(args.dest, payloads)
    print(f"写入 {args.dest}：{len(payloads)} 个文件，{total} 字节，"
          f"本次更新 {len(written)} 个", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
