"""调律规则加载与管理

规则全部外置为 YAML（config/system/yysls/tuning_rules/ 下每规则一个
文件），本模块负责加载、schema 校验、缓存、创建/删除与保存。判定
逻辑见 generic.GenericSchoolJudge，规则变更零代码改动。

规则中的全部词条引用一律使用标准词条名（attributes.yaml 普通词组
_aliases 全集，经 AttrRuleManager.get_normal_affix_names() 提供），
校验失败即保存拒绝，消除符号二次映射与静默失配。

schema 要点：
- weapon_rules: 名字 → {main/sub: {weapon, damage}}，damage 为具体
  增伤词条名或 null（不需要增伤）；判定武器部位时按用户勾选的
  名字展开尝试，装备武器名匹配主/副武器即产生一次判定；
- patterns.<部位>: first + 三档条件 junk/usable/top_conditions，
  每档为「条件组」列表：组间 OR（任一组命中即触发该档）、组内
  AND；单个条件 dict 视作单条件组。判定顺序 junk → usable → top，
  全不命中默认「优秀」。
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from loguru import logger


# ─── 固定词汇（写死在代码，不进 YAML） ─────────────────────

# PVP 词条（全局 keep_pvp 开启时按部位做等价处理）
PVP_NAMES = {"单体类奇术增伤", "对玩家单位增效"}

# 条件原语类型
COND_KINDS = {"not_contains", "contains_all", "not_together",
              "count_max", "count_min"}

# 部位归并：佩→环、胸甲→冠胄、腕甲→胫甲
PART_ALIAS = {"佩": "环", "胸甲": "冠胄", "腕甲": "胫甲"}

# 模式部位 key 全集
PART_KEYS = ("主武器", "副武器", "环", "冠胄", "胫甲")

# 规则 key 合法性（作为 YAML 文件名）
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# 已废弃的旧版 schema 顶层字段（出现即拒绝，提示迁移）
_LEGACY_KEYS = ("variants", "sub_schools", "weapons", "own_attr",
                "optional_pool", "junk_rules", "has_keep_pvp",
                "needs_sub_school", "sub_school_label")


def standard_affix_names() -> list[str]:
    """标准词条全集（普通词组 _aliases 并集，按 YAML 声明序）"""
    from .attr_rules import get_attr_rule_manager
    return get_attr_rule_manager().get_normal_affix_names()


# ─── 规则数据结构 ──────────────────────────────────────────

@dataclass
class Condition:
    """条件原语（三档条件组内 AND）

    symbols 为标准词条名列表。
    - not_contains: 非首词条未出现任一 symbols
    - contains_all: 非首词条必须同时出现全部 symbols
    - not_together: symbols（恰 2 个）不同时出现
    - count_max:    symbols 计数 ≤ max（include_first 时含首词条）
    - count_min:    symbols 计数 ≥ min 即触发
    """
    kind: str
    symbols: list[str]
    max: int = 0
    min: int = 0
    include_first: bool = False

    def _count(self, first_token: str, tokens: list[str]) -> int:
        count = sum(1 for t in tokens if t in self.symbols)
        if self.include_first and first_token in self.symbols:
            count += 1
        return count

    def check(self, first_token: str, tokens: list[str]) -> bool:
        """条件是否成立（tokens 为非首词条名列表）"""
        s = set(tokens)
        if self.kind == "not_contains":
            return not (s & set(self.symbols))
        if self.kind == "contains_all":
            return set(self.symbols) <= s
        if self.kind == "not_together":
            return not (set(self.symbols) <= s)
        count = self._count(first_token, tokens)
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

    def still_hits(self, first_token: str, tokens: list[str],
                   n_avail: int) -> bool:
        """潜力求值：n_avail 张万能牌按最优填法能否解除命中

        junk/usable 条件专用——补牌只增不减：
        - contains_all/count_min: 命中后加词条不会反转 → 维持命中；
        - not_contains: 补 1 个 symbols 内词条即可解除；
        - not_together: 补齐 2 词条同现即可解除；
        - count_max: 补 symbols 内词条至超出上限即可解除。
        """
        if not self.check(first_token, tokens):
            return False
        if self.kind in ("contains_all", "count_min"):
            return True
        if self.kind == "not_contains":
            return n_avail < 1
        if self.kind == "not_together":
            missing = len(set(self.symbols) - set(tokens))
            return missing > n_avail
        # count_max：需补 max+1-count 个才能突破上限
        count = self._count(first_token, tokens)
        return (self.max + 1 - count) > n_avail


@dataclass
class WeaponSide:
    """武器规则单侧（主或副）：武器名 + 增伤词条名（None = 不需要）"""
    weapon: str
    damage: str | None = None


@dataclass
class WeaponRule:
    """武器规则（如 纯唐/双切）：规定主/副武器及各自增伤要求"""
    name: str
    main: WeaponSide
    sub: WeaponSide

    def summary(self) -> str:
        """UI 摘要文案"""
        return f"主 {self.main.weapon} / 副 {self.sub.weapon}"


@dataclass
class PartPattern:
    """单部位模式：首词条 + 三档条件

    三档均为条件组列表（组间 OR、组内 AND），判定顺序
    junk → usable → top，全不命中默认「优秀」。
    """
    first: list[str]
    junk_conditions: list[list[Condition]] = field(default_factory=list)
    usable_conditions: list[list[Condition]] = field(default_factory=list)
    top_conditions: list[list[Condition]] = field(default_factory=list)


@dataclass
class SchoolRule:
    """单条调律规则（一个 YAML 文件，对应 UI 一个 Tab）"""
    key: str
    name: str
    order: int = 100
    weapon_rules: dict[str, WeaponRule] = field(default_factory=dict)
    transmute_priority: list[str] = field(default_factory=list)
    affix_pool: list[str] = field(default_factory=list)
    patterns: dict[str, PartPattern] = field(default_factory=dict)

    @property
    def pool_set(self) -> set[str]:
        return set(self.affix_pool)

    # ── UI 元数据接口 ──

    @property
    def school_name(self) -> str:
        return self.name

    @property
    def implemented(self) -> bool:
        return True

    @property
    def weapon_rule_options(self) -> dict[str, str]:
        """武器规则名字 → 摘要（UI 勾选项）"""
        return {name: wr.summary() for name, wr in self.weapon_rules.items()}


# ─── YAML 解析与校验 ───────────────────────────────────────

class RuleValidationError(ValueError):
    """规则 schema 校验失败"""


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


def parse_school_rule(data: dict) -> SchoolRule:
    """原始 YAML dict → SchoolRule（校验失败抛 RuleValidationError）"""
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
            "weapon_rules + 三档条件结构")

    vocab = set(standard_affix_names())
    if not vocab:
        raise RuleValidationError("标准词条全集为空（attributes.yaml 异常）")

    weapon_rules: dict[str, WeaponRule] = {}
    for w_name, w_raw in (data.get("weapon_rules") or {}).items():
        w_name = str(w_name).strip()
        if not w_name:
            raise RuleValidationError("weapon_rules: 名字不能为空")
        if w_name in weapon_rules:
            raise RuleValidationError(
                f"weapon_rules: 名字重复: {w_name}")
        w_raw = w_raw or {}
        weapon_rules[w_name] = WeaponRule(
            name=w_name,
            main=_parse_weapon_side(
                w_raw.get("main"), vocab, f"weapon_rules.{w_name}.main"),
            sub=_parse_weapon_side(
                w_raw.get("sub"), vocab, f"weapon_rules.{w_name}.sub"),
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

    return SchoolRule(
        key=str(key),
        name=str(name),
        order=int(data.get("order", 100)),
        weapon_rules=weapon_rules,
        transmute_priority=priority,
        affix_pool=affix_pool,
        patterns=patterns,
    )


# ─── 规则管理器 ────────────────────────────────────────────

class TuningRuleManager:
    """调律规则管理器

    加载目录下全部 YAML，校验失败的文件记录错误并跳过；
    提供按 order 排序的规则注册表、原始数据访问（UI 编辑用）、
    创建/删除与保存 + reload。
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

    # ── 保存 / 创建 / 删除 ──

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

    def create_rule(self, key: str, name: str) -> None:
        """新建规则（最小骨架 YAML），key 作为文件名

        Raises:
            RuleValidationError: key 非法 / 已存在 / 名称为空
        """
        key = key.strip()
        name = name.strip()
        if not _KEY_RE.match(key):
            raise RuleValidationError(
                "规则 key 须为小写字母开头的英文/数字/下划线")
        if not name:
            raise RuleValidationError("规则名称不能为空")
        if key in self._paths or (self._dir / f"{key}.yaml").exists():
            raise RuleValidationError(f"规则 key 已存在: {key}")
        data = {
            "key": key,
            "name": name,
            "order": 100,
            "weapon_rules": {},
            "transmute_priority": [],
            "affix_pool": [],
            "patterns": {},
        }
        parse_school_rule(data)  # 骨架自校验
        self._dir.mkdir(parents=True, exist_ok=True)
        with open(self._dir / f"{key}.yaml", "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        self.reload()

    def delete_rule(self, key: str) -> None:
        """删除规则文件并 reload

        Raises:
            RuleValidationError: key 未注册
        """
        path = self._paths.get(key)
        if path is None:
            raise RuleValidationError(f"规则不存在: {key}")
        path.unlink(missing_ok=True)
        self.reload()

    def rename_rule(self, old_key: str, new_key: str) -> None:
        """重命名规则 key（同步重命名 YAML 文件、更新 data 内 key 字段并 reload）

        Raises:
            RuleValidationError: 旧 key 未注册 / 新 key 非法或已存在
        """
        old_key = old_key.strip()
        new_key = new_key.strip()
        if old_key not in self._paths:
            raise RuleValidationError(f"规则不存在: {old_key}")
        if not _KEY_RE.match(new_key):
            raise RuleValidationError(
                "规则 key 须为小写字母开头的英文/数字/下划线")
        if new_key != old_key and (
                new_key in self._paths
                or (self._dir / f"{new_key}.yaml").exists()):
            raise RuleValidationError(f"规则 key 已存在: {new_key}")
        if new_key == old_key:
            return
        old_path = self._paths[old_key]
        new_path = self._dir / f"{new_key}.yaml"
        # 同步更新 data 内 key 字段，避免 reload 后 key 与文件名不一致
        data = self._raw.get(old_key) or {}
        data["key"] = new_key
        self._raw[old_key] = data
        with open(old_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        old_path.rename(new_path)
        self.reload()


# ─── 全局单例 ──────────────────────────────────────────────

_instance: TuningRuleManager | None = None


def get_tuning_rule_manager() -> TuningRuleManager:
    """获取全局 TuningRuleManager 单例"""
    global _instance
    if _instance is None:
        _instance = TuningRuleManager()
    return _instance
