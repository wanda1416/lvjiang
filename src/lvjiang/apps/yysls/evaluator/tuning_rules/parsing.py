"""调律规则 / 基础配置的 YAML 解析与 schema 校验"""

from __future__ import annotations

import re

from .models import (
    BEHAVIOR_STAGE_ACTIONS,
    COND_KINDS,
    DYNAMIC_AFFIXES,
    FOOD_EXPECT_KEYS,
    FOOD_LABELS,
    GENERIC_ATTR,
    INSUFFICIENT_ACTIONS,
    JUDGE_SCOPES,
    MAX_TUNE_RESETS,
    PART_KEYS,
    PCT_OPS,
    QUALITY_PARTS,
    RATING_KEYS,
    STONE_ACTIONS,
    TIER_KEYS,
    BehaviorRule,
    BehaviorSettings,
    CommonConditions,
    Condition,
    ConditionGroup,
    FoodRule,
    MaterialSettings,
    PartPattern,
    Playstyle,
    RuleValidationError,
    ScanBehavior,
    TuneBehavior,
    TuningBase,
    TuningRule,
    WeaponSide,
    rule_affix_candidates,
    standard_playstyle_attrs,
)

# 规则 key / 开关 key 合法性（作为 YAML 文件名 / when 引用键）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 已废弃的旧版 schema 顶层字段（出现即拒绝，提示迁移）
_LEGACY_KEYS = ("variants", "sub_schools", "weapons", "own_attr",
                "optional_pool", "junk_rules", "has_keep_pvp",
                "needs_sub_school", "sub_school_label", "weapon_rules")

_VALID_QUALITIES = ("gold", "purple", "blue")

# 行为规则的品阶条件额外支持 gold_only（仅金装精确匹配）与
# purple_only（仅紫装精确匹配）
_VALID_RULE_QUALITIES = ("gold", "gold_only", "purple_only", "purple",
                         "blue")


def _parse_quality_thresholds(raw, where: str,
                              require_all: bool = False,
                              ) -> dict[str, list[str]]:
    """品阶门槛解析：部位 key 锁定 QUALITY_PARTS，品阶枚举校验

    require_all=True（全局基础配置）时须列全 7 个部位；
    False（规则级覆盖）时允许子集。返回按 QUALITY_PARTS 定序。
    """
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    result: dict[str, list[str]] = {}
    for part, qs in raw.items():
        if part not in QUALITY_PARTS:
            raise RuleValidationError(
                f"{where}: 未知部位 {part!r}（须为 {list(QUALITY_PARTS)}）")
        items = list(qs or [])
        bad = [q for q in items if q not in _VALID_QUALITIES]
        if bad:
            raise RuleValidationError(
                f"{where}.{part}: 非法品阶 {bad}（需为 "
                f"{list(_VALID_QUALITIES)}）")
        result[str(part)] = items
    if require_all:
        missing = [p for p in QUALITY_PARTS if p not in result]
        if missing:
            raise RuleValidationError(f"{where}: 缺少部位 {missing}")
    return {p: result[p] for p in QUALITY_PARTS if p in result}


def _check_names(names: list, vocab: set[str], where: str) -> list[str]:
    """词条名列表校验（须全部在规则可引用词表内）"""
    items = list(names or [])
    bad = [s for s in items if s not in vocab]
    if bad:
        raise RuleValidationError(
            f"{where}: 词条名不在规则可引用词表内: {bad}")
    return items


def _parse_condition(raw: dict, vocab: set[str], where: str) -> Condition:
    """{原语类型: 参数} 单键 dict → Condition

    集合式原语（contains_all/not_together）参数为词条 list 或
    {symbols, include_first}；计数式原语（count_max/count_min）参数为
    {symbols, max/min, include_first}。
    """
    if not isinstance(raw, dict) or len(raw) != 1:
        raise RuleValidationError(f"{where}: 条件必须是单键 dict: {raw!r}")
    kind, args = next(iter(raw.items()))
    if kind == "not_contains":
        raise RuleValidationError(
            f"{where}: not_contains 已废弃，请改用 "
            "count_max（symbols + max: 0）")
    if kind not in COND_KINDS:
        raise RuleValidationError(f"{where}: 未知条件原语 {kind!r}")
    if kind in ("contains_all", "not_together"):
        if isinstance(args, dict):
            symbols = list(args.get("symbols") or [])
            extra: dict = {
                "include_first": bool(args.get("include_first", False)),
            }
        else:
            symbols = list(args or [])
            extra = {}
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
    if kind == "not_together" and len(symbols) < 2:
        raise RuleValidationError(f"{where}: not_together 须至少 2 个词条")
    return Condition(kind=kind, symbols=symbols, **extra)


def _parse_when(raw, where: str) -> dict[str, bool]:
    """条件组开关前提解析：{开关 key: bool}，key 格式校验

    key 是否已注册由 manager 层在全部规则加载后统一校验。
    """
    if not isinstance(raw, dict) or not raw:
        raise RuleValidationError(f"{where}: when 必须是非空 dict")
    result: dict[str, bool] = {}
    for k, v in raw.items():
        k = str(k).strip()
        if not _KEY_RE.match(k):
            raise RuleValidationError(
                f"{where}: when 开关 key 非法: {k!r}（须为小写字母开头"
                "的英文/数字/下划线）")
        if not isinstance(v, bool):
            raise RuleValidationError(
                f"{where}: when.{k} 期望值必须是 true/false")
        result[k] = v
    return result


def _parse_condition_groups(raw: list | None, vocab: set[str],
                            where: str) -> list[ConditionGroup]:
    """条件组列表解析（三种形态）

    - 单键条件 dict：视作单条件组；
    - list：组内 AND；
    - {when: {...}, all: [...]}：带开关前提的条件组。
    """
    groups: list[ConditionGroup] = []
    for i, g_raw in enumerate(raw or []):
        when: dict[str, bool] = {}
        if isinstance(g_raw, dict) and ("when" in g_raw or "all" in g_raw):
            extra_keys = set(g_raw) - {"when", "all"}
            if extra_keys:
                raise RuleValidationError(
                    f"{where}[{i}]: 开关条件组只允许 when/all 键，"
                    f"多余: {sorted(extra_keys)}")
            if "when" in g_raw:
                when = _parse_when(g_raw.get("when"), f"{where}[{i}]")
            g_raw = g_raw.get("all")
        elif isinstance(g_raw, dict):
            g_raw = [g_raw]
        if not isinstance(g_raw, list) or not g_raw:
            raise RuleValidationError(
                f"{where}[{i}]: 条件组必须是条件 dict、条件 dict 列表或 "
                "{when, all} 形态")
        groups.append(ConditionGroup(
            conditions=[
                _parse_condition(c, vocab, f"{where}[{i}][{j}]")
                for j, c in enumerate(g_raw)],
            when=when,
        ))
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


def _parse_common(raw, vocab: set[str]) -> CommonConditions:
    """通用判定解析：只允许四档条件键（无 first/default_rating）"""
    if raw is None:
        return CommonConditions()
    if not isinstance(raw, dict):
        raise RuleValidationError("common_conditions 必须是 dict")
    unknown = set(raw) - set(TIER_KEYS)
    if unknown:
        raise RuleValidationError(
            f"common_conditions: 只允许四档条件键 {list(TIER_KEYS)}，"
            f"多余: {sorted(unknown)}")
    return CommonConditions(**{
        tier_key: _parse_condition_groups(
            raw.get(tier_key), vocab, f"common_conditions.{tier_key}")
        for tier_key in TIER_KEYS})


def _parse_pattern(raw: dict, vocab: set[str], where: str) -> PartPattern:
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where}: 模式必须是 dict")
    if "usable_conditions" in raw:
        raise RuleValidationError(
            f"{where}: usable_conditions 已废弃，请改用 normal_conditions")
    first = _check_names(raw.get("first") or [], vocab, f"{where}.first")
    if not first:
        raise RuleValidationError(f"{where}: first 不能为空")
    # 部位级默认判定覆盖（可选，缺省跟随规则级）
    default_rating = raw.get("default_rating")
    if default_rating is not None:
        default_rating = str(default_rating)
        if default_rating not in RATING_KEYS:
            raise RuleValidationError(
                f"{where}.default_rating 非法: {default_rating!r}（须为 "
                f"{list(RATING_KEYS)}）")
    return PartPattern(
        first=first,
        default_rating=default_rating,
        junk_conditions=_parse_condition_groups(
            raw.get("junk_conditions"), vocab, f"{where}.junk_conditions"),
        normal_conditions=_parse_condition_groups(
            raw.get("normal_conditions"), vocab,
            f"{where}.normal_conditions"),
        excellent_conditions=_parse_condition_groups(
            raw.get("excellent_conditions"), vocab,
            f"{where}.excellent_conditions"),
        top_conditions=_parse_condition_groups(
            raw.get("top_conditions"), vocab, f"{where}.top_conditions"),
    )


def parse_tuning_rule(data: dict,
                      switch_keys: set[str] | None = None) -> TuningRule:
    """原始 YAML dict → TuningRule（校验失败抛 RuleValidationError）

    switch_keys 为已注册开关 key 全集（来自 tuning_base.switches），
    传入时校验全部条件组 when 引用；None 跳过（单测/离线解析）。
    """
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
            "playstyles + 四档条件结构")

    default_rating = str(data.get("default_rating", "excellent"))
    if default_rating not in RATING_KEYS:
        raise RuleValidationError(
            f"default_rating 非法: {default_rating!r}（须为 "
            f"{list(RATING_KEYS)}）")

    vocab = set(rule_affix_candidates())
    if not vocab:
        raise RuleValidationError("规则可引用词表为空（attributes.yaml 异常）")
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
    affix_pool = _check_names(data.get("affix_pool") or [], vocab, "affix_pool")
    priority = _check_names(data.get("transmute_priority") or [], vocab,
                            "transmute_priority")

    patterns: dict[str, PartPattern] = {}
    for part, p_raw in (data.get("patterns") or {}).items():
        if part not in PART_KEYS:
            raise RuleValidationError(f"未知部位 key {part!r}")
        patterns[part] = _parse_pattern(p_raw, vocab, f"patterns.{part}")

    rule = TuningRule(
        key=str(key),
        name=str(name),
        order=int(data.get("order", 100)),
        playstyles=playstyles,
        transmute_priority=priority,
        affix_pool=affix_pool,
        patterns=patterns,
        common=_parse_common(data.get("common_conditions"), vocab),
        default_rating=default_rating,
        quality_thresholds=_parse_quality_thresholds(
            data.get("quality_thresholds") or {}, "quality_thresholds"),
    )
    if switch_keys is not None:
        unknown = sorted(rule.referenced_switches() - switch_keys)
        if unknown:
            raise RuleValidationError(
                f"when 引用了未注册的开关 key: {unknown}（请先在基础"
                "配置的开关设定中注册）")
    # 交叉校验：通用属性玩法（混搭流）不做动态归类，引用动态
    # 词条属死引用（永不匹配），严格拒绝
    if any(ps.attr == GENERIC_ATTR for ps in playstyles.values()):
        used_dynamic = sorted(
            rule.referenced_affixes() & set(DYNAMIC_AFFIXES))
        if used_dynamic:
            raise RuleValidationError(
                f"通用属性玩法（混搭流）不允许使用动态属攻词条: "
                f"{used_dynamic}")
    return rule


def _parse_food_rule(raw, where: str) -> FoodRule:
    """单条狗粮规则解析：字段可缺省（落 FoodRule 默认值）"""
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    defaults = FoodRule()
    pct = raw.get("pct", defaults.pct)
    if isinstance(pct, bool) or not isinstance(pct, int):
        raise RuleValidationError(f"{where}.pct 必须是整数")
    if not (0 <= pct <= 100):
        raise RuleValidationError(f"{where}.pct 超出范围 [0, 100]: {pct}")
    expect = str(raw.get("min_expect", defaults.min_expect))
    if expect not in FOOD_EXPECT_KEYS:
        raise RuleValidationError(
            f"{where}.min_expect 非法: {expect!r}"
            f"（须为 {list(FOOD_EXPECT_KEYS)}）")
    quality = str(raw.get("min_quality", defaults.min_quality))
    if quality not in _VALID_QUALITIES:
        raise RuleValidationError(
            f"{where}.min_quality 非法: {quality!r}"
            f"（须为 {list(_VALID_QUALITIES)}）")
    food = str(raw.get("food") or "")
    if food and food not in FOOD_LABELS:
        raise RuleValidationError(
            f"{where}.food 非法: {food!r}"
            f"（须为 {list(FOOD_LABELS)} 或空=不添加）")
    action = str(raw.get("on_insufficient", defaults.on_insufficient))
    if action not in INSUFFICIENT_ACTIONS:
        raise RuleValidationError(
            f"{where}.on_insufficient 非法: {action!r}"
            f"（须为 {list(INSUFFICIENT_ACTIONS)}）")
    return FoodRule(pct=pct, min_expect=expect, min_quality=quality,
                    food=food, on_insufficient=action)


def _parse_materials(raw, where: str = "materials") -> MaterialSettings:
    """材料设置解析：缺省段/缺省字段取 MaterialSettings 默认值

    food_rules 为有序规则列表（可空列表=从不添加；缺省落默认
    两条），各字段枚举锁定，数值字段拒绝 bool 伪装的 int。
    """
    if raw is None:
        return MaterialSettings()
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    defaults = MaterialSettings()

    def _int_field(value, name: str, lo: int, hi: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuleValidationError(f"{where}.{name} 必须是整数")
        if not (lo <= value <= hi):
            raise RuleValidationError(
                f"{where}.{name} 超出范围 [{lo}, {hi}]: {value}")
        return value

    stone = raw.get("stone_check") or {}
    if not isinstance(stone, dict):
        raise RuleValidationError(f"{where}.stone_check 必须是 dict")
    enabled = bool(stone.get("enabled", defaults.stone_check_enabled))
    min_count = _int_field(
        stone.get("min_count", defaults.stone_min_count),
        "stone_check.min_count", 1, 99999)
    insufficient = stone.get("insufficient_action",
                             defaults.stone_insufficient_action)
    if insufficient not in STONE_ACTIONS:
        raise RuleValidationError(
            f"{where}.stone_check.insufficient_action 非法: "
            f"{insufficient!r}（须为 {list(STONE_ACTIONS)}）")

    if "food_strategy" in raw:
        raise RuleValidationError(
            f"{where}.food_strategy 已废弃，请改用 food_rules 规则表")
    raw_rules = raw.get("food_rules")
    if raw_rules is None:
        food_rules = defaults.food_rules
    elif isinstance(raw_rules, list):
        food_rules = [
            _parse_food_rule(item, f"{where}.food_rules[{i}]")
            for i, item in enumerate(raw_rules)
        ]
    else:
        raise RuleValidationError(f"{where}.food_rules 必须是 list")

    return MaterialSettings(
        stone_check_enabled=enabled,
        stone_min_count=min_count,
        stone_insufficient_action=insufficient,
        food_rules=food_rules,
    )


def _parse_behavior_rule(raw, where: str,
                         allowed_actions: tuple[str, ...],
                         allow_first_affix: bool) -> BehaviorRule:
    """单条行为规则解析：条件字段可缺省（=不限/最高档），action 必填；
    first_affix_only 仅扫描处置表可声明"""
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    parts = list(raw.get("parts") or [])
    bad = [p for p in parts if p not in QUALITY_PARTS]
    if bad:
        raise RuleValidationError(
            f"{where}.parts: 未知部位 {bad}（须为 {list(QUALITY_PARTS)}）")
    max_quality = raw.get("max_quality", "gold")
    if max_quality not in _VALID_RULE_QUALITIES:
        raise RuleValidationError(
            f"{where}.max_quality 非法: {max_quality!r}（须为 "
            f"{list(_VALID_RULE_QUALITIES)}）")
    pct_op, pct = _parse_rule_pct(raw, where)
    scope, keys = _parse_judge(raw, where)
    ratings = _parse_rule_ratings(raw, where, scope)
    first_affix = bool(raw.get("first_affix_only", False))
    if first_affix and not allow_first_affix:
        raise RuleValidationError(
            f"{where}.first_affix_only 仅扫描处置表规则可声明")
    action = str(raw.get("action") or "")
    if action not in allowed_actions:
        raise RuleValidationError(
            f"{where}.action 非法: {action!r}（须为 {list(allowed_actions)}）")
    return BehaviorRule(parts=parts, max_quality=max_quality,
                        pct_op=pct_op, pct=pct, ratings=ratings,
                        judge_scope=scope, judge_rules=keys,
                        first_affix_only=first_affix, action=action)


def _parse_rule_pct(raw: dict, where: str) -> tuple[str, int]:
    """首词条条件解析：pct_op（le/ge）+ pct（[0, 100] 整数）

    兼容历史字段 max_pct（固定 ≤ 语义）：换算为 pct_op=le。
    """
    pct_op = raw.get("pct_op", "le")
    if pct_op not in PCT_OPS:
        raise RuleValidationError(
            f"{where}.pct_op 非法: {pct_op!r}（须为 {list(PCT_OPS)}）")
    key = "pct" if "pct" in raw else "max_pct"
    default = 100 if pct_op == "le" else 0
    pct = raw.get(key, default)
    if isinstance(pct, bool) or not isinstance(pct, int):
        raise RuleValidationError(f"{where}.{key} 必须是整数")
    if not (0 <= pct <= 100):
        raise RuleValidationError(
            f"{where}.{key} 超出范围 [0, 100]: {pct}")
    return pct_op, pct


def _parse_rule_ratings(raw: dict, where: str, scope: str) -> list[str]:
    """结果条件解析，语义随 judge_scope 分流：

    评级语义：ratings 档位集合（自由多选，空/全选 = 不限）；
    全选归一化为空（不限 = 不取评级，未知评级也命中）；去重
    并按 RATING_KEYS 声明序归一。兼容历史字段 max_rating（≤
    语义）：top → 不限，其余 → 该档及以下的档位集合。
    自选词条语义（affix）：ratings 存词条名集合，按
    rule_affix_candidates 词表校验，去重按词表序归一；必须非空
    （空 = 永不命中的僵尸规则，直接报错）；不做全选归一
    （全选词条 = 全词条命中，语义不同于不限）。
    """
    if scope == "affix":
        return _parse_rule_affix_ratings(raw, where)
    raw_ratings = raw.get("ratings")
    if raw_ratings is not None:
        if not isinstance(raw_ratings, list):
            raise RuleValidationError(f"{where}.ratings 必须是 list")
        bad = [r for r in raw_ratings if r not in RATING_KEYS]
        if bad:
            raise RuleValidationError(
                f"{where}.ratings 非法: {bad}（须为 {list(RATING_KEYS)}）")
        keys = [r for r in RATING_KEYS if r in raw_ratings]
        return [] if len(keys) == len(RATING_KEYS) else keys
    # 历史字段 max_rating（≤ 语义）自动换算
    max_rating = raw.get("max_rating", "top")
    if max_rating not in RATING_KEYS:
        raise RuleValidationError(
            f"{where}.max_rating 非法: {max_rating!r}（须为 "
            f"{list(RATING_KEYS)}）")
    if max_rating == "top":
        return []
    cut = RATING_KEYS.index(max_rating)
    return list(RATING_KEYS[:cut + 1])


def _parse_rule_affix_ratings(raw: dict, where: str) -> list[str]:
    """自选词条语义的词条集解析：按 rule_affix_candidates 词表
    校验（未知词条报错），去重并按词表声明序归一；必须非空
    （空 = 永不命中的僵尸规则，直接报错）；不做全选归一
    （全选词条 = 全词条命中，语义不同于不限）。"""
    raw_ratings = raw.get("ratings")
    if not isinstance(raw_ratings, list) or not raw_ratings:
        raise RuleValidationError(
            f"{where}.ratings: 自选词条语义须至少勾选一个词条")
    vocab = rule_affix_candidates()
    bad = [r for r in raw_ratings if r not in vocab]
    if bad:
        raise RuleValidationError(
            f"{where}.ratings 非法: {bad}（须为词条词表内词条名）")
    return [name for name in vocab if name in raw_ratings]


def _parse_judge(raw: dict, where: str) -> tuple[str, list[str]]:
    """判定语义解析（逐规则声明）：judge_scope 四选一 + judge_rules
    key 列表

    judge_rules 仅 scope=custom 时允许非空（key 格式校验；是否
    真实存在由运行期过滤兜底，避免规则删除后配置加载即崩）。
    """
    scope = raw.get("judge_scope", "incoming")
    if scope not in JUDGE_SCOPES:
        raise RuleValidationError(
            f"{where}.judge_scope 非法: {scope!r}（须为 {list(JUDGE_SCOPES)}）")
    raw_keys = raw.get("judge_rules")
    if raw_keys is None:
        keys: list[str] = []
    elif isinstance(raw_keys, list):
        keys = []
        for k in raw_keys:
            k = str(k).strip()
            if not _KEY_RE.match(k):
                raise RuleValidationError(
                    f"{where}.judge_rules: 规则 key 非法: {k!r}"
                    "（须为小写字母开头的英文/数字/下划线）")
            keys.append(k)
    else:
        raise RuleValidationError(f"{where}.judge_rules 必须是 list")
    if keys and scope != "custom":
        raise RuleValidationError(
            f"{where}.judge_rules 仅 judge_scope=custom 时可声明"
            f"（当前 {scope!r}）")
    return scope, keys


def _parse_behavior_rules(raw, where: str, stage: str) -> list[BehaviorRule]:
    """行为规则表解析：动作按行为点白名单锁定"""
    allowed = BEHAVIOR_STAGE_ACTIONS[stage]
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuleValidationError(f"{where} 必须是 list")
    return [
        _parse_behavior_rule(item, f"{where}[{i}]", allowed,
                             allow_first_affix=(stage == "scan"))
        for i, item in enumerate(raw)
    ]


def _reject_stage_judge(raw: dict, where: str) -> None:
    """段级判定语义已下沉到逐条规则，残留即报错（不做迁移兼容）"""
    for key in ("judge_scope", "judge_rules"):
        if key in raw:
            raise RuleValidationError(
                f"{where}.{key} 已废弃，判定语义改为逐条规则声明"
                f"（{where}.rules[].{key}）")


def _parse_scan(raw, where: str) -> ScanBehavior:
    """扫描处理解析：{enabled, entry_min_rating, rules}；缺省段取
    ScanBehavior 默认值"""
    if raw is None:
        return ScanBehavior()
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    _reject_stage_judge(raw, where)
    if "first_affix_only" in raw:
        raise RuleValidationError(
            f"{where}.first_affix_only 已废弃，改为逐条规则声明"
            f"（{where}.rules[].first_affix_only）")
    entry = raw.get("entry_min_rating", "excellent")
    if entry not in RATING_KEYS:
        raise RuleValidationError(
            f"{where}.entry_min_rating 非法: {entry!r}（须为 "
            f"{list(RATING_KEYS)}）")
    return ScanBehavior(
        enabled=bool(raw.get("enabled", True)),
        entry_min_rating=entry,
        rules=_parse_behavior_rules(raw.get("rules"), f"{where}.rules",
                                    "scan"),
    )


def _parse_tune(raw, where: str) -> TuneBehavior:
    """结束处理解析：{enabled, rules, max_resets,
    reset_exhausted_action}；缺省段取 TuneBehavior 默认值"""
    if raw is None:
        return TuneBehavior()
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    _reject_stage_judge(raw, where)
    max_resets = raw.get("max_resets", MAX_TUNE_RESETS)
    if isinstance(max_resets, bool) or not isinstance(max_resets, int):
        raise RuleValidationError(f"{where}.max_resets 必须是整数")
    if not (0 <= max_resets <= MAX_TUNE_RESETS):
        raise RuleValidationError(
            f"{where}.max_resets 超出范围 [0, {MAX_TUNE_RESETS}]: "
            f"{max_resets}")
    exhausted = raw.get("reset_exhausted_action", "skip")
    if exhausted not in ("recycle", "skip"):
        raise RuleValidationError(
            f"{where}.reset_exhausted_action 非法: {exhausted!r}"
            "（须为 ['recycle', 'skip']）")
    return TuneBehavior(
        enabled=bool(raw.get("enabled", False)),
        rules=_parse_behavior_rules(raw.get("rules"), f"{where}.rules",
                                    "tune"),
        max_resets=max_resets,
        reset_exhausted_action=exhausted,
    )


def _parse_behavior(raw, where: str = "behavior") -> BehaviorSettings:
    """行为配置解析：缺省段取 BehaviorSettings 默认值"""
    if raw is None:
        return BehaviorSettings()
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where} 必须是 dict")
    return BehaviorSettings(
        scan=_parse_scan(raw.get("scan"), f"{where}.scan"),
        tune=_parse_tune(raw.get("tune"), f"{where}.tune"),
    )


def parse_tuning_base(data: dict) -> TuningBase:
    """原始 tuning_base.yaml dict → TuningBase（校验失败抛 RuleValidationError）"""
    if not isinstance(data, dict):
        raise RuleValidationError("tuning_base 顶层必须是 dict")
    if "pvp" in data:
        raise RuleValidationError(
            "pvp 段已废弃，请改用 switches 开关注册表 + 规则条件组 when")
    if "recycle" in data:
        raise RuleValidationError(
            "recycle 段已废弃，请改用 behavior 行为配置"
            "（behavior.scan / behavior.tune）")

    # ── min_level（等级门槛，缺省 100）──
    min_level = data.get("min_level", TuningBase.min_level)
    if isinstance(min_level, bool) or not isinstance(min_level, int):
        raise RuleValidationError("min_level 必须是整数")
    if not (1 <= min_level <= 999):
        raise RuleValidationError(
            f"min_level 超出范围 [1, 999]: {min_level}")

    # ── quality_thresholds（固定 7 个标准部位，须列全）──
    quality_thresholds = _parse_quality_thresholds(
        data.get("quality_thresholds") or {}, "quality_thresholds",
        require_all=True)

    # ── switches（开关注册表：key → {name}）──
    raw_switches = data.get("switches") or {}
    if not isinstance(raw_switches, dict):
        raise RuleValidationError("switches 必须是 dict")
    switches: dict[str, str] = {}
    for k, spec in raw_switches.items():
        k = str(k).strip()
        if not _KEY_RE.match(k):
            raise RuleValidationError(
                f"switches: 开关 key 非法: {k!r}（须为小写字母开头"
                "的英文/数字/下划线）")
        if not isinstance(spec, dict):
            raise RuleValidationError(f"switches.{k} 必须是 dict（name）")
        sw_name = str(spec.get("name") or "").strip()
        if not sw_name:
            raise RuleValidationError(f"switches.{k}.name 不能为空")
        switches[k] = sw_name

    return TuningBase(
        min_level=min_level,
        quality_thresholds=quality_thresholds,
        switches=switches,
        materials=_parse_materials(data.get("materials")),
        behavior=_parse_behavior(data.get("behavior")),
    )
