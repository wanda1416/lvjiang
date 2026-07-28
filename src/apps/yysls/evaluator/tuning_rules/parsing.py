"""调律规则 / 基础配置的 YAML 解析与 schema 校验"""

from __future__ import annotations

import re

from .models import (
    COND_KINDS, GENERIC_ATTR, PART_KEYS,
    Condition, PartPattern, Playstyle, PvpPartRule, RuleValidationError,
    TuningBase, TuningRule, WeaponSide,
    standard_affix_names, standard_playstyle_attrs,
)

# 规则 key 合法性（作为 YAML 文件名）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 已废弃的旧版 schema 顶层字段（出现即拒绝，提示迁移）
_LEGACY_KEYS = ("variants", "sub_schools", "weapons", "own_attr",
                "optional_pool", "junk_rules", "has_keep_pvp",
                "needs_sub_school", "sub_school_label", "weapon_rules")

_VALID_QUALITIES = ("gold", "purple", "blue")


def _check_names(names: list, vocab: set[str], where: str) -> list[str]:
    """词条名列表校验（须全部在标准词条全集内）"""
    items = list(names or [])
    bad = [s for s in items if s not in vocab]
    if bad:
        raise RuleValidationError(
            f"{where}: 词条名不在标准词条全集内: {bad}")
    return items


def _parse_condition(raw: dict, vocab: set[str], where: str) -> Condition:
    """{原语类型: 参数} 单键 dict → Condition"""
    if not isinstance(raw, dict) or len(raw) != 1:
        raise RuleValidationError(f"{where}: 条件必须是单键 dict: {raw!r}")
    kind, args = next(iter(raw.items()))
    if kind not in COND_KINDS:
        raise RuleValidationError(f"{where}: 未知条件原语 {kind!r}")
    if kind in ("not_contains", "contains_all", "not_together"):
        symbols = list(args or [])
        extra: dict = {}
    else:
        if not isinstance(args, dict):
            raise RuleValidationError(f"{where}: {kind} 参数必须是 dict")
        symbols = list(args.get("symbols") or [])
        extra = {
            "max": int(args.get("max", 0)),
            "min": int(args.get("min", 0)),
            "include_first": bool(args.get("include_first", False)),
        }
    if not symbols:
        raise RuleValidationError(f"{where}: 条件 {kind} 词条列表为空")
    _check_names(symbols, vocab, where)
    if kind == "not_together" and len(symbols) != 2:
        raise RuleValidationError(f"{where}: not_together 须恰好 2 个词条")
    return Condition(kind=kind, symbols=symbols, **extra)


def _parse_condition_groups(raw: list | None, vocab: set[str],
                            where: str) -> list[list[Condition]]:
    """条件组列表解析：单键 dict 视作单条件组，list 为组内 AND"""
    groups: list[list[Condition]] = []
    for i, g_raw in enumerate(raw or []):
        if isinstance(g_raw, dict):
            g_raw = [g_raw]
        if not isinstance(g_raw, list) or not g_raw:
            raise RuleValidationError(
                f"{where}[{i}]: 条件组必须是条件 dict 或条件 dict 列表")
        groups.append([
            _parse_condition(c, vocab, f"{where}[{i}][{j}]")
            for j, c in enumerate(g_raw)])
    return groups


def _parse_weapon_side(raw, vocab: set[str], where: str) -> WeaponSide:
    """{weapon, damage} → WeaponSide"""
    if not isinstance(raw, dict):
        raise RuleValidationError(
            f"{where}: 必须是 dict（weapon/damage）")
    weapon = str(raw.get("weapon") or "").strip()
    if not weapon:
        raise RuleValidationError(f"{where}: weapon 不能为空")
    damage = raw.get("damage")
    if damage:
        _check_names([damage], vocab, f"{where}.damage")
    return WeaponSide(weapon=weapon, damage=damage or None)


def _parse_pattern(raw: dict, vocab: set[str], where: str) -> PartPattern:
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where}: 模式必须是 dict")
    first = _check_names(raw.get("first"), vocab, f"{where}.first")
    if not first:
        raise RuleValidationError(f"{where}: first 不能为空")
    return PartPattern(
        first=first,
        junk_conditions=_parse_condition_groups(
            raw.get("junk_conditions"), vocab, f"{where}.junk_conditions"),
        usable_conditions=_parse_condition_groups(
            raw.get("usable_conditions"), vocab,
            f"{where}.usable_conditions"),
        top_conditions=_parse_condition_groups(
            raw.get("top_conditions"), vocab, f"{where}.top_conditions"),
    )


def parse_tuning_rule(data: dict) -> TuningRule:
    """原始 YAML dict → TuningRule（校验失败抛 RuleValidationError）"""
    if not isinstance(data, dict):
        raise RuleValidationError("规则文件顶层必须是 dict")
    key = data.get("key")
    name = data.get("name")
    if not key or not name:
        raise RuleValidationError("缺少必填字段 key/name")
    legacy = [k for k in _LEGACY_KEYS if k in data]
    if legacy:
        raise RuleValidationError(
            f"旧版 schema 字段不再支持: {legacy}，请迁移到 "
            "playstyles + 三档条件结构")

    vocab = set(standard_affix_names())
    if not vocab:
        raise RuleValidationError("标准词条全集为空（attributes.yaml 异常）")
    attr_vocab = set(standard_playstyle_attrs())

    playstyles: dict[str, Playstyle] = {}
    for w_name, w_raw in (data.get("playstyles") or {}).items():
        w_name = str(w_name).strip()
        if not w_name:
            raise RuleValidationError("playstyles: 名字不能为空")
        if w_name in playstyles:
            raise RuleValidationError(
                f"playstyles: 名字重复: {w_name}")
        w_raw = w_raw or {}
        attr = str(w_raw.get("attr") or GENERIC_ATTR).strip() or GENERIC_ATTR
        if attr_vocab and attr not in attr_vocab:
            raise RuleValidationError(
                f"playstyles.{w_name}.attr: 属性 {attr!r} 不在属性攻击词组内: "
                f"{sorted(attr_vocab)}")
        playstyles[w_name] = Playstyle(
            name=w_name,
            main=_parse_weapon_side(
                w_raw.get("main"), vocab, f"playstyles.{w_name}.main"),
            sub=_parse_weapon_side(
                w_raw.get("sub"), vocab, f"playstyles.{w_name}.sub"),
            attr=attr,
        )

    # ── 词条库与模式（顶层，允许为空 = 新建骨架） ──
    affix_pool = _check_names(data.get("affix_pool"), vocab, "affix_pool")
    priority = _check_names(data.get("transmute_priority"), vocab,
                            "transmute_priority")

    patterns: dict[str, PartPattern] = {}
    for part, p_raw in (data.get("patterns") or {}).items():
        if part not in PART_KEYS:
            raise RuleValidationError(f"未知部位 key {part!r}")
        patterns[part] = _parse_pattern(p_raw, vocab, f"patterns.{part}")

    return TuningRule(
        key=str(key),
        name=str(name),
        order=int(data.get("order", 100)),
        playstyles=playstyles,
        transmute_priority=priority,
        affix_pool=affix_pool,
        patterns=patterns,
    )


def parse_tuning_base(data: dict) -> TuningBase:
    """原始 tuning_base.yaml dict → TuningBase（校验失败抛 RuleValidationError）"""
    if not isinstance(data, dict):
        raise RuleValidationError("tuning_base 顶层必须是 dict")
    vocab = set(standard_affix_names())

    # ── quality_thresholds ──
    raw_q = data.get("quality_thresholds") or {}
    if not isinstance(raw_q, dict):
        raise RuleValidationError("quality_thresholds 必须是 dict")
    quality_thresholds: dict[str, list[str]] = {}
    for cat, qs in raw_q.items():
        items = list(qs or [])
        bad = [q for q in items if q not in _VALID_QUALITIES]
        if bad:
            raise RuleValidationError(
                f"quality_thresholds.{cat}: 非法品阶 {bad}（需为 "
                f"{list(_VALID_QUALITIES)}）")
        quality_thresholds[str(cat)] = items
    if "default" not in quality_thresholds:
        raise RuleValidationError("quality_thresholds 缺少 default 项")

    # ── pvp ──
    raw_pvp = data.get("pvp") or {}
    if not isinstance(raw_pvp, dict):
        raise RuleValidationError("pvp 必须是 dict")
    pvp_names = list(raw_pvp.get("names") or [])
    _check_names(pvp_names, vocab, "pvp.names")
    pvp_parts: dict[str, PvpPartRule] = {}
    raw_subs = raw_pvp.get("substitutions") or {}
    if not isinstance(raw_subs, dict):
        raise RuleValidationError("pvp.substitutions 必须是 dict")
    for part, spec in raw_subs.items():
        if part not in PART_KEYS:
            raise RuleValidationError(f"pvp.substitutions: 未知部位 {part!r}")
        spec = spec or {}
        add_to_pool = list(spec.get("add_to_pool") or [])
        _check_names(add_to_pool, vocab,
                     f"pvp.substitutions.{part}.add_to_pool")
        subs = {str(s): str(d) for s, d in spec.items()
                if s != "add_to_pool"}
        _check_names(list(subs.keys()), vocab,
                     f"pvp.substitutions.{part}源词条")
        _check_names(list(subs.values()), vocab,
                     f"pvp.substitutions.{part}目标词条")
        pvp_parts[part] = PvpPartRule(substitutions=subs,
                                      add_to_pool=add_to_pool)

    return TuningBase(
        quality_thresholds=quality_thresholds,
        pvp_names=set(pvp_names),
        pvp_parts=pvp_parts,
    )
