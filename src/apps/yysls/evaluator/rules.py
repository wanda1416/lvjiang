"""调律规则加载与管理

规则全部外置为 YAML（config/system/yysls/tuning_rules/ 下每流派一个
文件），本模块负责加载、schema 校验、缓存与保存。判定逻辑见
generic.GenericSchoolJudge，规则变更零代码改动。

规则定义将持续频繁变更（04 文档），条件原语共 4 种 + junk 用的
count_min，覆盖当前全部规则；将来不够时再扩原语。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger


# ─── 固定词汇（写死在代码，不进 YAML） ─────────────────────

# 模式符号词汇表（全部流派共用）
SYMBOL_VOCAB = {
    "大外", "小外", "劲", "势", "敏", "会意", "会心", "精准",
    "大无相", "小无相", "小外属",
}

# 词条全称 → 模式符号（属攻类归一化由 own_attr 参数化，见 generic）
SYMBOL_MAP = {
    "最大外功攻击": "大外",
    "最小外功攻击": "小外",
    "劲": "劲",
    "势": "势",
    "敏": "敏",
    "会意率": "会意",
    "会心率": "会心",
    "精准率": "精准",
}

# 全部流派属名（own_attr 候选；用于识别属攻词条）
SCHOOL_ATTRS = {"裂石", "牵丝", "破竹", "鸣金"}

# PVP 词条（keep_pvp 开启时视作有效）
PVP_NAMES = {"单体类奇术增伤", "对玩家单位增效"}

# DMG 占位符（由 weapons 表按武器角色解析）
DMG_PLACEHOLDER = "DMG"

# 条件原语类型
COND_KINDS = {"not_contains", "contains_all", "not_together",
              "count_max", "count_min"}

# 部位归并：佩→环、胸甲→冠胄、腕甲→胫甲
PART_ALIAS = {"佩": "环", "胸甲": "冠胄", "腕甲": "胫甲"}

# 模式部位 key 全集
PART_KEYS = ("主武器", "副武器", "环", "冠胄", "胫甲")


# ─── 规则数据结构 ──────────────────────────────────────────

@dataclass
class Condition:
    """条件原语（顶级判定条件行间 AND；junk_rules 用 count_min）

    - not_contains: 非首词条未出现任一 symbols
    - contains_all: 非首词条必须同时出现全部 symbols
    - not_together: symbols（恰 2 个）不同时出现
    - count_max:    symbols 计数 ≤ max（include_first 时含首词条）
    - count_min:    symbols 计数 ≥ min 即触发（junk 规则专用）
    """
    kind: str
    symbols: list[str]
    max: int = 0
    min: int = 0
    include_first: bool = False

    def check(self, first_token: str, tokens: list[str]) -> bool:
        """条件是否成立（tokens 为非首词条符号列表）"""
        s = set(tokens)
        if self.kind == "not_contains":
            return not (s & set(self.symbols))
        if self.kind == "contains_all":
            return set(self.symbols) <= s
        if self.kind == "not_together":
            return not (set(self.symbols) <= s)
        count = sum(1 for t in tokens if t in self.symbols)
        if self.include_first and first_token in self.symbols:
            count += 1
        if self.kind == "count_max":
            return count <= self.max
        if self.kind == "count_min":
            return count >= self.min
        return False

    def potential(self, first_token: str, tokens: list[str],
                  n_avail: int) -> bool:
        """潜力求值：剩余 n_avail 张牌能否使条件仍有机会成立

        排除类条件（not_contains/not_together/count_max）当前已满足
        即可（空槽按最优填法不会引入排除词条）；contains_all 缺失数
        不超过可补牌数即可。
        """
        if self.kind == "contains_all":
            missing = set(self.symbols) - set(tokens)
            return len(missing) <= n_avail
        return self.check(first_token, tokens)


@dataclass
class PartPattern:
    """单部位模式：首词条 + 必选槽 + 可选槽 + 顶级条件"""
    first: list[str]
    required: list[list[str]]
    required_damage: str | None = None       # DMG / 词条全称 / None
    damage_pvp_substitute: str | None = None  # keep_pvp 时可顶替增伤槽
    optional_n: int = 0
    allowed_divine_pvp: list[str] = field(default_factory=list)
    top: list[Condition] = field(default_factory=list)


@dataclass
class RuleVariant:
    """规则变体（huiyi/huixin 单变体 default；heal 为 pure/fire）"""
    key: str
    name: str = ""
    transmute_priority: list[str] = field(default_factory=list)
    affix_pool: list[str] = field(default_factory=list)
    optional_pool: list[str] | None = None    # 缺省 = affix_pool
    junk_rules: list[Condition] = field(default_factory=list)
    patterns: dict[str, PartPattern] = field(default_factory=dict)

    @property
    def pool_set(self) -> set[str]:
        return set(self.affix_pool)

    @property
    def optional_pool_set(self) -> set[str]:
        if self.optional_pool is None:
            return set(self.affix_pool)
        return set(self.optional_pool)


@dataclass
class SubSchool:
    """子流派（会心的指定流派 / 治疗的玩法）"""
    key: str
    name: str
    playstyles: dict[str, str] = field(default_factory=dict)


@dataclass
class WeaponRole:
    """武器角色表条目：主武器 → 主武学增伤，副武器列表"""
    main: dict[str, str] = field(default_factory=dict)
    sub: list[str] = field(default_factory=list)


@dataclass
class SchoolRule:
    """单流派完整规则（一个 YAML 文件）

    对 UI 暴露与旧判定器类属性同名的元数据接口
    （school_name/implemented/sub_school_options 等）。
    """
    key: str
    name: str
    order: int = 100
    has_keep_pvp: bool = False
    needs_sub_school: bool = False
    sub_school_label: str = "指定流派（必选）："
    own_attr: str = ""                        # 属名 或 from_sub_schools
    sub_schools: dict[str, SubSchool] = field(default_factory=dict)
    weapons: dict[str, WeaponRole] = field(default_factory=dict)
    variants: dict[str, RuleVariant] = field(default_factory=dict)

    # ── UI 元数据接口（与旧判定器类属性同名） ──

    @property
    def school_name(self) -> str:
        return self.name

    @property
    def implemented(self) -> bool:
        return True

    @property
    def sub_school_options(self) -> dict[str, str]:
        return {k: s.name for k, s in self.sub_schools.items()}

    @property
    def sub_school_playstyles(self) -> dict[str, dict[str, str]]:
        return {k: dict(s.playstyles)
                for k, s in self.sub_schools.items() if s.playstyles}


# ─── YAML 解析与校验 ───────────────────────────────────────

class RuleValidationError(ValueError):
    """规则 schema 校验失败"""


def _parse_condition(raw: dict, where: str) -> Condition:
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
        raise RuleValidationError(f"{where}: 条件 {kind} 符号列表为空")
    bad = [s for s in symbols if s not in SYMBOL_VOCAB]
    if bad:
        raise RuleValidationError(f"{where}: 条件符号不在词汇表内: {bad}")
    if kind == "not_together" and len(symbols) != 2:
        raise RuleValidationError(f"{where}: not_together 须恰好 2 个符号")
    return Condition(kind=kind, symbols=symbols, **extra)


def _check_symbols(symbols: list, where: str) -> list[str]:
    """符号列表校验（须全部在词汇表内）"""
    items = list(symbols or [])
    bad = [s for s in items if s not in SYMBOL_VOCAB]
    if bad:
        raise RuleValidationError(f"{where}: 符号不在词汇表内: {bad}")
    return items


def _parse_pattern(raw: dict, where: str) -> PartPattern:
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where}: 模式必须是 dict")
    first = _check_symbols(raw.get("first"), f"{where}.first")
    if not first:
        raise RuleValidationError(f"{where}: first 不能为空")

    required_raw = raw.get("required") or []
    required: list[list[str]] = []
    for i, slot in enumerate(required_raw):
        cands = list(slot or [])
        if not cands:
            raise RuleValidationError(f"{where}: 必选槽 {i + 1} 为空")
        required.append(cands)

    optional_n = int(raw.get("optional_n", 0))
    if len(required) + optional_n + 1 != 5:
        raise RuleValidationError(
            f"{where}: 必选槽数 {len(required)} + 可选槽数 {optional_n} "
            f"+ 首词条 != 5")

    top = [_parse_condition(c, f"{where}.top[{i}]")
           for i, c in enumerate(raw.get("top") or [])]
    return PartPattern(
        first=first,
        required=required,
        required_damage=raw.get("required_damage"),
        damage_pvp_substitute=raw.get("damage_pvp_substitute"),
        optional_n=optional_n,
        allowed_divine_pvp=list(raw.get("allowed_divine_pvp") or []),
        top=top,
    )


def _parse_variant(key: str, raw: dict, where: str) -> RuleVariant:
    if not isinstance(raw, dict):
        raise RuleValidationError(f"{where}: 变体必须是 dict")
    affix_pool = _check_symbols(raw.get("affix_pool"), f"{where}.affix_pool")
    if not affix_pool:
        raise RuleValidationError(f"{where}: affix_pool 不能为空")
    priority = _check_symbols(raw.get("transmute_priority"),
                              f"{where}.transmute_priority")
    optional_pool = raw.get("optional_pool")
    if optional_pool is not None:
        optional_pool = _check_symbols(optional_pool,
                                       f"{where}.optional_pool")
        bad = [s for s in optional_pool if s not in affix_pool]
        if bad:
            raise RuleValidationError(
                f"{where}: optional_pool 符号不在 affix_pool 内: {bad}")
    junk_rules = [_parse_condition(c, f"{where}.junk_rules[{i}]")
                  for i, c in enumerate(raw.get("junk_rules") or [])]

    patterns_raw = raw.get("patterns") or {}
    patterns: dict[str, PartPattern] = {}
    for part, p_raw in patterns_raw.items():
        if part not in PART_KEYS:
            raise RuleValidationError(f"{where}: 未知部位 key {part!r}")
        patterns[part] = _parse_pattern(p_raw, f"{where}.patterns.{part}")
    if not patterns:
        raise RuleValidationError(f"{where}: patterns 不能为空")

    return RuleVariant(
        key=key,
        name=str(raw.get("name") or ""),
        transmute_priority=priority,
        affix_pool=affix_pool,
        optional_pool=optional_pool,
        junk_rules=junk_rules,
        patterns=patterns,
    )


def parse_school_rule(data: dict) -> SchoolRule:
    """原始 YAML dict → SchoolRule（校验失败抛 RuleValidationError）"""
    if not isinstance(data, dict):
        raise RuleValidationError("规则文件顶层必须是 dict")
    key = data.get("key")
    name = data.get("name")
    if not key or not name:
        raise RuleValidationError("缺少必填字段 key/name")

    sub_schools: dict[str, SubSchool] = {}
    for sk, s_raw in (data.get("sub_schools") or {}).items():
        s_raw = s_raw or {}
        s_name = s_raw.get("name")
        if not s_name:
            raise RuleValidationError(f"sub_schools.{sk}: 缺少 name")
        sub_schools[sk] = SubSchool(
            key=sk, name=s_name,
            playstyles=dict(s_raw.get("playstyles") or {}))

    own_attr = str(data.get("own_attr") or "")
    if own_attr and own_attr != "from_sub_schools" \
            and own_attr not in SCHOOL_ATTRS:
        raise RuleValidationError(
            f"own_attr 必须是 {sorted(SCHOOL_ATTRS)} 之一或 from_sub_schools")
    if own_attr == "from_sub_schools":
        bad = [s.name for s in sub_schools.values()
               if s.name not in SCHOOL_ATTRS]
        if bad:
            raise RuleValidationError(
                f"own_attr=from_sub_schools 但子流派名不是属名: {bad}")

    weapons: dict[str, WeaponRole] = {}
    for wk, w_raw in (data.get("weapons") or {}).items():
        w_raw = w_raw or {}
        weapons[wk] = WeaponRole(
            main=dict(w_raw.get("main") or {}),
            sub=list(w_raw.get("sub") or []))
        if wk != "default":
            school = wk.split(".", 1)[0]
            if school not in sub_schools:
                raise RuleValidationError(
                    f"weapons.{wk}: 子流派 {school!r} 未定义")

    variants_raw = data.get("variants") or {}
    if not variants_raw:
        raise RuleValidationError("缺少 variants")
    variants = {
        vk: _parse_variant(vk, v_raw, f"variants.{vk}")
        for vk, v_raw in variants_raw.items()
    }
    for vk in variants:
        if vk != "default" and vk not in sub_schools:
            raise RuleValidationError(
                f"variants.{vk}: 变体 key 须为 default 或子流派 key")

    return SchoolRule(
        key=str(key),
        name=str(name),
        order=int(data.get("order", 100)),
        has_keep_pvp=bool(data.get("has_keep_pvp", False)),
        needs_sub_school=bool(data.get("needs_sub_school", False)),
        sub_school_label=str(data.get("sub_school_label")
                             or "指定流派（必选）："),
        own_attr=own_attr,
        sub_schools=sub_schools,
        weapons=weapons,
        variants=variants,
    )


# ─── 规则管理器 ────────────────────────────────────────────

class TuningRuleManager:
    """调律规则管理器

    加载目录下全部 YAML，校验失败的文件记录错误并跳过；
    提供按 order 排序的规则注册表、原始数据访问（UI 编辑用）
    与保存 + reload。
    """

    def __init__(self, rules_dir: str | Path | None = None):
        if rules_dir is None:
            from src.constants import SYSTEM_CONFIG_DIR
            rules_dir = SYSTEM_CONFIG_DIR / "yysls" / "tuning_rules"
        self._dir = Path(rules_dir)
        self._rules: dict[str, SchoolRule] = {}
        self._raw: dict[str, dict] = {}
        self._paths: dict[str, Path] = {}
        self._errors: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        """重新加载目录下全部规则文件"""
        self._rules.clear()
        self._raw.clear()
        self._paths.clear()
        self._errors.clear()
        loaded: list[SchoolRule] = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                rule = parse_school_rule(data)
            except Exception as e:
                logger.error(f"调律规则 {path.name} 加载失败，已跳过: {e}")
                self._errors[path.stem] = str(e)
                continue
            if rule.key in self._paths:
                logger.error(f"调律规则 {path.name} key 重复: {rule.key}")
                continue
            loaded.append(rule)
            self._raw[rule.key] = data
            self._paths[rule.key] = path
        for rule in sorted(loaded, key=lambda r: (r.order, r.key)):
            self._rules[rule.key] = rule

    # ── 查询 ──

    def get_rules(self) -> dict[str, SchoolRule]:
        """key → SchoolRule（按 order 排序）"""
        return dict(self._rules)

    def get_rule(self, key: str) -> SchoolRule | None:
        return self._rules.get(key)

    def get_raw(self, key: str) -> dict:
        """原始 YAML dict 的深拷贝（UI 编辑用）"""
        return copy.deepcopy(self._raw.get(key) or {})

    @property
    def errors(self) -> dict[str, str]:
        """加载失败的文件（文件名 stem → 错误信息）"""
        return dict(self._errors)

    # ── 保存 ──

    def validate(self, data: dict) -> str | None:
        """校验原始 dict；返回错误文案（None 表示通过）"""
        try:
            parse_school_rule(data)
            return None
        except RuleValidationError as e:
            return str(e)

    def save_rule(self, key: str, data: dict) -> None:
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload"""
        parse_school_rule(data)  # 先校验
        path = self._paths.get(key) or (self._dir / f"{key}.yaml")
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        self.reload()


# ─── 全局单例 ──────────────────────────────────────────────

_instance: TuningRuleManager | None = None


def get_tuning_rule_manager() -> TuningRuleManager:
    """获取全局 TuningRuleManager 单例"""
    global _instance
    if _instance is None:
        _instance = TuningRuleManager()
    return _instance
