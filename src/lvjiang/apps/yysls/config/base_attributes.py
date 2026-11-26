"""流派/玩法预置基础属性（system / local 分层）与双源解析。

存储路径：
- 系统随包：``config/system/yysls/base_attributes/{流派}.yaml``
- 本地覆盖：``config/local/yysls/base_attributes/{流派}.yaml``

口径约束：
- ``stats`` 与 ``config/session/yysls/play_styles.json`` 严格同口径——均为扣除
  8 件装备（含固有基础值、40 条普通词条、定音）和弓玦后的纯角色基础属性。
- 阶段维度使用通用整数 ``stage``（1, 2, 3…），不绑定国服赛季日历，默认自动取
  最高可用 ``(level, stage)``，以兼容国际版等多服节奏。
- 备战方案通过 ``preset:<id>`` 引用预置条目，与 ``session/play_styles.json``
  的个人实测名称隔离，互不撞名且不修改 ``loadouts`` Schema。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from ....core.config import dump_yaml
from ....core.config.resolver import (
    LAYER_LOCAL,
    LAYER_SYSTEM,
    SystemContentProtected,
    get_resolver,
)
from . import play_styles as _play_styles_mod

BASE_ATTRIBUTES_DIR = "yysls/base_attributes"
PRESET_REF_PREFIX = "preset:"


@dataclass(frozen=True)
class BaseAttributePreset:
    """一条流派/玩法预置基础属性。"""

    id: str
    school: str
    level: int
    stage: int
    playstyle: str | None
    label: str
    stats: dict[str, float]
    source: str = "system"  # "system" | "local"


def make_preset_ref(preset_id: str) -> str:
    """将预置 ID 编码为备战方案 ``base_attribute`` 引用键。"""
    cleaned = preset_id.strip()
    if not cleaned:
        raise ValueError("预置基础属性 ID 不能为空")
    return f"{PRESET_REF_PREFIX}{cleaned}"


def parse_preset_ref(ref: str) -> str | None:
    """若为 ``preset:<id>`` 引用则返回预置 ID，否则返回 None。"""
    if isinstance(ref, str) and ref.startswith(PRESET_REF_PREFIX):
        preset_id = ref[len(PRESET_REF_PREFIX):].strip()
        return preset_id or None
    return None


def preset_rel_path(school: str) -> str:
    """流派预置基础属性文件的相对路径。"""
    cleaned = school.strip()
    if not cleaned or any(ch in cleaned for ch in '<>:"/\\|?*'):
        raise ValueError(f"流派名称非法: {school!r}")
    return f"{BASE_ATTRIBUTES_DIR}/{cleaned}.yaml"


def _parse_stats(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    stats: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        stats[key.strip()] = float(value)
    return stats


def _parse_preset_item(school: str, item: object, *, source: str) -> BaseAttributePreset | None:
    if not isinstance(item, dict):
        return None
    preset_id = str(item.get("id") or "").strip()
    level = item.get("level")
    stage = item.get("stage", 1)
    if (
        not preset_id
        or not isinstance(level, int) or isinstance(level, bool) or level <= 0
        or not isinstance(stage, int) or isinstance(stage, bool) or stage <= 0
    ):
        logger.warning(f"预置基础属性条目字段无效，已忽略: {school}/{preset_id!r}")
        return None
    raw_ps = item.get("playstyle")
    playstyle = str(raw_ps).strip() if isinstance(raw_ps, str) and raw_ps.strip() else None
    default_label = (
        f"{level}级 {stage}阶段（{playstyle}）"
        if playstyle else f"{level}级 {stage}阶段（默认）"
    )
    label = str(item.get("label") or "").strip() or default_label
    stats = _parse_stats(item.get("stats"))
    return BaseAttributePreset(
        id=preset_id,
        school=school,
        level=level,
        stage=stage,
        playstyle=playstyle,
        label=label,
        stats=stats,
        source=source,
    )


def _read_layer_presets(path: Path, school: str, *, source: str) -> list[BaseAttributePreset]:
    if not path.is_file():
        return []
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(f"读取预置基础属性失败 {path}: {exc}")
        return []
    if not isinstance(doc, dict):
        return []
    raw_presets = doc.get("presets")
    if not isinstance(raw_presets, list):
        return []
    result: list[BaseAttributePreset] = []
    for item in raw_presets:
        parsed = _parse_preset_item(school, item, source=source)
        if parsed is not None:
            result.append(parsed)
    return result


def get_base_attribute_presets(
    school: str, *, layer: str | None = None,
) -> list[BaseAttributePreset]:
    """获取指定流派的预置基础属性列表。

    ``layer`` 缺省时合并 ``system ← local``（local 同 id 覆盖 system）；
    传入 ``"system"`` 或 ``"local"`` 时仅读取对应单层，不做合并。
    排序：按最高 ``level`` → 最高 ``stage`` → 流派默认优先于玩法专属 → ``id``。
    """
    if layer is not None and layer not in (LAYER_SYSTEM, LAYER_LOCAL):
        raise ValueError(f"不支持的配置层: {layer!r}")
    if not school:
        return []
    resolver = get_resolver()
    rel = preset_rel_path(school)
    merged: dict[str, BaseAttributePreset] = {}
    if layer in (None, LAYER_SYSTEM):
        for preset in _read_layer_presets(resolver.system_dir / rel, school, source=LAYER_SYSTEM):
            merged[preset.id] = preset
    if layer == LAYER_LOCAL or (layer is None and resolver.local_dir != resolver.system_dir):
        for preset in _read_layer_presets(resolver.local_dir / rel, school, source=LAYER_LOCAL):
            merged[preset.id] = preset
    return sorted(
        merged.values(),
        key=lambda p: (-p.level, -p.stage, 0 if p.playstyle is None else 1, p.playstyle or "", p.id),
    )


def format_base_attribute_label(
    school: str,
    base_attr_ref: str,
    *,
    preset_tag: str = "预置",
    local_tag: str = "本地预置",
) -> str:
    """将基础属性引用键格式化为面向用户展示的标签（隐藏 ``preset:`` 前缀）。"""
    if not base_attr_ref:
        return ""
    preset_id = parse_preset_ref(base_attr_ref)
    if preset_id is None:
        return base_attr_ref
    preset = get_base_attribute_preset(school, preset_id) if school else None
    if preset is None:
        return f"[{preset_tag}] {preset_id}"
    tag = local_tag if preset.source == LAYER_LOCAL else preset_tag
    return f"[{tag}] {preset.label}"


def get_base_attribute_preset(school: str, preset_id: str) -> BaseAttributePreset | None:
    """按流派与预置 ID 获取单条预置基础属性。"""
    for preset in get_base_attribute_presets(school):
        if preset.id == preset_id:
            return preset
    return None


def resolve_default_preset(
    school: str,
    *,
    playstyle: str = "",
    level: int | None = None,
    stage: int | None = None,
) -> BaseAttributePreset | None:
    """为流派/玩法解析默认预置基础属性（自动取最高可用 level → stage，玩法专属优先于流派默认）。"""
    presets = get_base_attribute_presets(school)
    if not presets:
        return None
    if level is not None and level > 0:
        eligible = [p for p in presets if p.level <= level]
        pool = eligible or presets
    else:
        pool = presets
    target_level = max(p.level for p in pool)
    at_level = [p for p in pool if p.level == target_level]

    if stage is not None and stage > 0:
        at_stage = [p for p in at_level if p.stage == stage] or at_level
    else:
        target_stage = max(p.stage for p in at_level)
        at_stage = [p for p in at_level if p.stage == target_stage]

    cleaned_ps = playstyle.strip()
    if cleaned_ps:
        exact = next((p for p in at_stage if p.playstyle == cleaned_ps), None)
        if exact is not None:
            return exact
    school_default = next((p for p in at_stage if p.playstyle is None), None)
    return school_default or at_stage[0]


def resolve_base_attribute_data(school: str, base_attr_ref: str) -> dict[str, float] | None:
    """统一解析基础属性：支持 ``preset:<id>``（system/local 预置）与 ``session/play_styles.json`` 裸名。"""
    if not school or not base_attr_ref:
        return None
    preset_id = parse_preset_ref(base_attr_ref)
    if preset_id is not None:
        preset = get_base_attribute_preset(school, preset_id)
        return dict(preset.stats) if preset is not None else None
    stored = _play_styles_mod.get_play_styles(school).get(base_attr_ref)
    return dict(stored) if isinstance(stored, dict) else None


def list_all_base_attributes(school: str) -> dict[str, dict[str, float]]:
    """返回流派下全部可用基础属性（预置 ``preset:<id>`` + 个人 ``play_styles.json``）。"""
    result: dict[str, dict[str, float]] = {}
    if not school:
        return result
    for preset in get_base_attribute_presets(school):
        result[make_preset_ref(preset.id)] = dict(preset.stats)
    for name, attrs in _play_styles_mod.get_play_styles(school).items():
        if isinstance(attrs, dict):
            result[name] = dict(attrs)
    return result


def _serialize_presets_doc(school: str, presets: list[BaseAttributePreset]) -> dict[str, Any]:
    ordered = sorted(
        presets,
        key=lambda p: (-p.level, -p.stage, 0 if p.playstyle is None else 1, p.playstyle or "", p.id),
    )
    return {
        "school": school,
        "presets": [
            {
                "id": p.id,
                "level": p.level,
                "stage": p.stage,
                "playstyle": p.playstyle,
                "label": p.label,
                "stats": dict(p.stats),
            }
            for p in ordered
        ],
    }


def save_base_attribute_preset(
    school: str,
    preset: BaseAttributePreset,
    *,
    layer: str | None = None,
) -> None:
    """保存或更新一条预置基础属性（开发模式默认写 system，用户模式写 local）。"""
    resolver = get_resolver()
    rel = preset_rel_path(school)
    effective_layer = layer
    if effective_layer is None:
        effective_layer = LAYER_SYSTEM if resolver.is_dev_mode() else LAYER_LOCAL
    root = resolver.system_dir if effective_layer == LAYER_SYSTEM else resolver.local_dir
    existing = _read_layer_presets(root / rel, school, source=effective_layer)
    by_id = {p.id: p for p in existing}
    by_id[preset.id] = BaseAttributePreset(
        id=preset.id,
        school=school,
        level=preset.level,
        stage=preset.stage,
        playstyle=preset.playstyle,
        label=preset.label,
        stats=dict(preset.stats),
        source=effective_layer,
    )
    doc = _serialize_presets_doc(school, list(by_id.values()))
    resolver.write_entity(rel, dump_yaml(doc), layer=layer)
    logger.debug(f"已保存预置基础属性 ({effective_layer}): {school}/{preset.id}")


def delete_base_attribute_preset(
    school: str,
    preset_id: str,
    *,
    layer: str | None = None,
) -> bool:
    """删除一条预置基础属性；用户模式下系统预置受保护不可删，仅可删除 local 覆盖。"""
    resolver = get_resolver()
    rel = preset_rel_path(school)
    effective_layer = layer
    if effective_layer is None:
        effective_layer = LAYER_SYSTEM if resolver.is_dev_mode() else LAYER_LOCAL
    root = resolver.system_dir if effective_layer == LAYER_SYSTEM else resolver.local_dir
    existing = _read_layer_presets(root / rel, school, source=effective_layer)
    remaining = [p for p in existing if p.id != preset_id]
    if len(remaining) == len(existing):
        if not resolver.is_dev_mode():
            sys_ids = {
                p.id for p in _read_layer_presets(resolver.system_dir / rel, school, source="system")
            }
            if preset_id in sys_ids:
                raise SystemContentProtected(
                    f"系统预置基础属性 {school}/{preset_id} 在用户模式下不可删除"
                )
        return False
    if remaining:
        doc = _serialize_presets_doc(school, remaining)
        resolver.write_entity(rel, dump_yaml(doc), layer=layer)
    elif not resolver.is_dev_mode() and resolver.is_system_entity(rel):
        resolver.revert_entity_to_system(rel)
    else:
        resolver.delete_entity(rel, layer=layer)
    logger.debug(f"已删除预置基础属性 ({effective_layer}): {school}/{preset_id}")
    return True
