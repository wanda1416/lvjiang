"""随版本发布的流派角色属性表。

来源快照、成长选择、完整角色属性与条件声明分开保存。静态求值复用
attr_model.resolver；条件效果仅声明，不进入静态总量或毕业率程序。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

import yaml

from .....core.config.resolver import ConfigResolver, get_resolver
from .builtin import dimension_effects
from .models import WORKING_FIELDS, AttrModelError, Formula, ResolveResult, StatEffect
from .parsing import parse_entry, parse_formula
from .resolver import resolve, validate_formula_dependencies

REL_DIR = "yysls/attr_model/profiles"
_KINDS = {"level": "base", "talent": "base", "oddity": "oddity",
          "martial_art": "martial_art", "inner_way": "inner_way"}
_CONDITION_FIELDS = {
    "target.qi_ratio", "target.qi_unbalanced", "target.exhausted", "target.kind",
    "self.stamina_ratio", "self.buff", "self.in_combat", "skill", "event",
}
_DECLARATION_TYPES = {"damage_bonus", "stat_bonus", "force_outcome", "resource",
                      "skill_rule", "cooldown", "attack_scaling"}


def _mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise AttrModelError(f"{name} 必须是映射")
    if any(not isinstance(key, str) for key in value):
        raise AttrModelError(f"{name} 的字段名必须是字符串")
    return value


def _keys(data: dict, allowed: set[str], name: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise AttrModelError(f"{name} 含未知字段: {', '.join(sorted(unknown))}")


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise AttrModelError(f"{name} 必须是有限数值")
    return float(value)


def _text(value: Any, name: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise AttrModelError(f"{name} 必须是{'非空' if not empty else ''}字符串")
    return value


def _finite_formula(formula: Formula) -> None:
    for name in ("multiplier", "offset", "minimum", "maximum"):
        value = getattr(formula, name)
        if value is not None:
            _number(value, name)
    if formula.minimum is not None and formula.maximum is not None and formula.minimum > formula.maximum:
        raise AttrModelError("公式下限不能大于上限")


def validate_condition(value: Any) -> None:
    data = _mapping(value, "condition")
    if set(data) == {"recent_event"}:
        event = _mapping(data["recent_event"], "recent_event")
        _keys(event, {"name", "seconds"}, "recent_event")
        if not isinstance(event.get("name"), str) or not event["name"]:
            raise AttrModelError("recent_event 需要事件名称")
        if _number(event.get("seconds"), "recent_event.seconds") < 0:
            raise AttrModelError("事件时间窗口不能是负数")
        return
    if set(data) in ({"all"}, {"any"}):
        children = next(iter(data.values()))
        if not isinstance(children, list) or not children:
            raise AttrModelError("all/any 必须包含非空条件列表")
        for child in children:
            validate_condition(child)
        return
    _keys(data, {"field", "op", "value"}, "condition")
    if _text(data.get("field"), "condition.field") not in _CONDITION_FIELDS:
        raise AttrModelError(f"未知条件字段: {data.get('field')}")
    if _text(data.get("op"), "condition.op") not in {"eq", "lt", "le", "gt", "ge", "contains"} or "value" not in data:
        raise AttrModelError("条件需要 op 和 value")
    if data["op"] in {"lt", "le", "gt", "ge"}:
        _number(data["value"], "condition.value")
    elif not isinstance(data["value"], (str, bool, int, float)):
        raise AttrModelError("condition.value 必须是标量")


def validate_growth(value: Any) -> dict:
    data = _mapping(value, "growth")
    _keys(data, {"character_level", "solo_level", "martial_arts", "inner_ways", "oddities"}, "growth")
    for key in ("character_level", "solo_level"):
        v = data.get(key)
        if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 1):
            raise AttrModelError(f"growth.{key} 必须是正整数或 null")
    for key in ("martial_arts", "inner_ways", "oddities"):
        for name, v in _mapping(data.get(key, {}), f"growth.{key}").items():
            if not isinstance(name, str) or not name:
                raise AttrModelError(f"growth.{key} 名称不能为空")
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or v < 0):
                raise AttrModelError(f"growth.{key}.{name} 必须是非负整数或 null")
            if key == "inner_ways" and v is not None and v > 6:
                raise AttrModelError("心法重数不能超过六重")
    if len(data.get("inner_ways", {})) > 4:
        raise AttrModelError("最多装备四门心法")
    return deepcopy(data)


@dataclass
class CharacterSource:
    source_id: str
    kind: str
    label: str
    status: str
    notes: str
    basis: dict
    effect: StatEffect
    declarations: list[dict]
    shared_key: str = ""


@dataclass
class CharacterProfile:
    school: str
    growth: dict
    observations: dict[str, float]
    notes: str
    sources: list[CharacterSource]
    raw: dict


@dataclass
class CharacterResult:
    resolved: ResolveResult
    missing: list[str]
    declarations: list[tuple[str, dict]]
    excluded: list[str] = field(default_factory=list)


def parse_profile(raw: Any) -> CharacterProfile:
    data = _mapping(raw, "角色属性表")
    _keys(data, {"schema_version", "school", "provenance", "notes", "growth",
                 "observations", "sources"}, "角色属性表")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1 or not isinstance(data.get("school"), str) or not data["school"]:
        raise AttrModelError("角色属性表需要 schema_version: 1 和 school")
    growth = validate_growth(data.get("growth", {}))
    observations = {}
    for key, v in _mapping(data.get("observations", {}), "observations").items():
        if key not in WORKING_FIELDS:
            raise AttrModelError(f"未知面板观测字段: {key}")
        observations[key] = _number(v, key)
    sources = []
    shared: dict[str, dict] = {}
    for source_id, item in _mapping(data.get("sources"), "sources").items():
        entry = _mapping(item, source_id)
        _keys(entry, {"kind", "label", "status", "notes", "basis", "static", "declarations", "shared_key"}, source_id)
        kind = _text(entry.get("kind"), f"{source_id}.kind")
        if kind not in _KINDS:
            raise AttrModelError(f"{source_id}: 未知来源类别 {kind}")
        status = _text(entry.get("status"), f"{source_id}.status")
        if status not in {"complete", "partial", "pending"}:
            raise AttrModelError(f"{source_id}: status 必须为 complete/partial/pending")
        basis = _mapping(entry.get("basis", {}), f"{source_id}.basis")
        _keys(basis, {"character_level", "solo_level", "martial_art", "martial_tier",
                      "inner_way", "inner_tier", "map", "progress", "snapshot"}, f"{source_id}.basis")
        for key in ("character_level", "solo_level", "martial_tier", "inner_tier", "progress"):
            if key in basis and basis[key] is not None:
                v = basis[key]
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise AttrModelError(f"{source_id}.basis.{key} 必须是非负整数或 null")
        for key in ("martial_art", "inner_way", "map"):
            if key in basis:
                _text(basis[key], f"{source_id}.basis.{key}")
        if "snapshot" in basis and not isinstance(basis["snapshot"], bool):
            raise AttrModelError("basis.snapshot 必须是布尔值")
        for owner, tier in (("martial_art", "martial_tier"), ("inner_way", "inner_tier"), ("map", "progress")):
            if tier in basis and owner not in basis:
                raise AttrModelError(f"{source_id}: {tier} 缺少 {owner}")
        static = _mapping(entry.get("static", {}), f"{source_id}.static")
        _keys(static, {"stats", "extra", "scope"}, f"{source_id}.static")
        effect = parse_entry(source_id, {**static, "label": entry.get("label", source_id)}, _KINDS[kind])
        for value in effect.stats.values():
            if isinstance(value, Formula):
                _finite_formula(value)
            else:
                _number(value, source_id)
        for value in effect.extra.values():
            _number(value, source_id)
        declarations = entry.get("declarations", [])
        if not isinstance(declarations, list):
            raise AttrModelError(f"{source_id}.declarations 必须是列表")
        for declaration in declarations:
            d = _mapping(declaration, source_id)
            _keys(d, {"id", "type", "target", "value", "condition", "duration", "interval",
                      "description", "shared_key", "max_reduction"}, "declaration")
            _text(d.get("id"), "declaration.id")
            _text(d.get("target"), "declaration.target")
            if _text(d.get("type"), "declaration.type") not in _DECLARATION_TYPES:
                raise AttrModelError(f"{source_id}: 声明需要 id、有效 type 和 target")
            if "condition" in d:
                validate_condition(d["condition"])
            if "value" in d:
                v = d["value"]
                if isinstance(v, dict):
                    _keys(v, {"formula"}, "declaration.value")
                    _finite_formula(parse_formula(v.get("formula"), "declaration.value"))
                elif not isinstance(v, (str, bool)):
                    _number(v, "declaration.value")
            for key in ("duration", "interval", "max_reduction"):
                if key in d and _number(d[key], key) < 0:
                    raise AttrModelError(f"{key} 不能是负数")
            if d.get("shared_key"):
                identity = {k: v for k, v in d.items() if k not in {"id", "description"}}
                key = "declaration:" + _text(d["shared_key"], "declaration.shared_key")
                if key in shared and shared[key] != identity:
                    raise AttrModelError(f"共享效果 {key} 定义冲突")
                shared[key] = identity
        key = _text(entry.get("shared_key", ""), f"{source_id}.shared_key", empty=True)
        if key:
            if key in shared and shared[key] != static:
                raise AttrModelError(f"共享静态效果 {key} 定义冲突")
            shared[key] = static
        if status == "complete" and not effect.modeled and not declarations:
            raise AttrModelError(f"{source_id}: 空来源不能标为完成")
        if status == "pending" and (effect.modeled or declarations):
            raise AttrModelError(f"{source_id}: 已有部分数据请使用 partial")
        sources.append(CharacterSource(source_id, kind, _text(entry.get("label", source_id), "label"), status,
                                       str(entry.get("notes", "")), basis, effect, declarations, key))
    profile = CharacterProfile(data["school"], growth, observations, str(data.get("notes", "")), sources, deepcopy(data))
    # 保存前检查整张表的公式依赖，避免编辑后才发现无法打开。
    validate_formula_dependencies([s.effect for s in sources if s.effect.modeled] + dimension_effects())
    evaluate_profile(profile)
    return profile


def _availability(source: CharacterSource, growth: dict) -> str:
    """active / missing / excluded。快照档位必须精确匹配，不插值也不累计。"""
    b = source.basis
    for key in ("character_level", "solo_level"):
        if key in b:
            if b[key] is None or growth.get(key) is None:
                return "missing"
            if b[key] != growth[key]:
                return "missing"  # 无对应等级数据，不代表零贡献
    for name_key, tier_key, group in (("martial_art", "martial_tier", "martial_arts"),
                                      ("inner_way", "inner_tier", "inner_ways"),
                                      ("map", "progress", "oddities")):
        if name_key not in b:
            continue
        selected = growth.get(group, {})
        if b[name_key] not in selected or selected[b[name_key]] == 0:
            return "excluded"
        tier = selected[b[name_key]]
        if tier is None or b.get(tier_key) is None:
            return "missing"
        if group == "inner_ways":
            if tier < b[tier_key]:
                return "excluded"
        elif tier != b[tier_key]:
            return "missing"
    return "active"


def evaluate_profile(profile: CharacterProfile, *, growth: dict | None = None,
                     equipment: dict[str, float] | None = None) -> CharacterResult:
    """默认使用记录的成长状态；显式传入时按新状态严格匹配档位。

    equipment 是装备提供的原始常量与原始五维，不是角色最终面板。
    不把 observations 当成基础贡献，避免观测值与来源数值重复相加。
    """
    growth = validate_growth(profile.growth if growth is None else growth)
    effects = []
    missing: list[str] = []
    excluded: list[str] = []
    declarations = []
    seen: set[str] = set()
    declared: set[str] = set()
    for source in profile.sources:
        availability = _availability(source, growth)
        if availability != "active":
            (missing if availability == "missing" else excluded).append(source.label)
            continue
        if source.status != "complete":
            missing.append(source.label)
        if source.effect.modeled and (not source.shared_key or source.shared_key not in seen):
            effects.append(source.effect)
            if source.shared_key:
                seen.add(source.shared_key)
        for d in source.declarations:
            key = d.get("shared_key")
            if key and key in declared:
                continue
            declarations.append((source.label, d))
            if key:
                declared.add(key)
    if equipment:
        for name, value in equipment.items():
            if name not in WORKING_FIELDS:
                raise AttrModelError(f"未知装备输入字段: {name}")
            _number(value, name)
        effects.append(StatEffect("__equipment__", "装备输入", "gear_set", stats=dict(equipment)))
    result = resolve(effects + dimension_effects(), level=0, school_attr="鸣金",
                     caps_lookup=lambda *_: None)
    return CharacterResult(result, missing, declarations, excluded)


class CharacterProfileManager:
    def __init__(self, resolver: ConfigResolver | None = None):
        self.resolver = resolver or get_resolver()

    def schools(self) -> list[str]:
        return [name.removesuffix(".yaml") for name in self.resolver.enumerate_entities(REL_DIR, "*.yaml")]

    def load(self, school: str) -> CharacterProfile:
        if school not in self.schools():
            raise AttrModelError(f"没有内置角色属性表: {school}")
        path = self.resolver.resolve_read(f"{REL_DIR}/{school}.yaml")
        if path is None:
            raise AttrModelError(f"找不到角色属性表: {school}")
        return parse_profile(yaml.safe_load(path.read_text(encoding="utf-8")))

    def save(self, school: str, raw: dict) -> None:
        profile = parse_profile(raw)
        if profile.school != school or school not in self.schools():
            raise AttrModelError("不能在编辑中更换流派")
        self.resolver.write_entity(f"{REL_DIR}/{school}.yaml",
                                   yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
