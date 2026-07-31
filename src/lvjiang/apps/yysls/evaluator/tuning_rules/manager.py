"""调律规则 / 基础配置的加载、缓存、创建/删除与保存管理器"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml
from loguru import logger

from lvjiang.core.config_resolver import ConfigResolver, get_resolver

from .models import RuleValidationError, TuningBase, TuningRule
from .parsing import _KEY_RE, parse_tuning_base, parse_tuning_rule

# 规则目录相对 config 层根的路径
_RULES_REL_DIR = "yysls/tuning_rules"
_BASE_REL_PATH = "yysls/tuning_base.yaml"

# ─── 规则管理器 ────────────────────────────────────────────

class TuningRuleManager:
    """调律规则管理器

    加载目录下全部 YAML，校验失败的文件记录错误并跳过；
    提供按 order 排序的规则注册表、原始数据访问（UI 编辑用）、
    创建/删除与保存 + reload。
    """

    def __init__(self, rules_dir: str | Path | None = None):
        if rules_dir is None:
            self._resolver = get_resolver()
            self._rel_dir = _RULES_REL_DIR
        else:
            # 测试/孤立目录：单层语义（system=local=rules_dir，直写直删）
            self._resolver = ConfigResolver(
                system_dir=rules_dir, local_dir=rules_dir, dev_mode=True)
            self._rel_dir = ""
        self._rules: dict[str, TuningRule] = {}
        self._raw: dict[str, dict] = {}
        self._files: dict[str, str] = {}   # key -> 文件名
        self._errors: dict[str, str] = {}
        self.reload()

    def _rel(self, filename: str) -> str:
        return f"{self._rel_dir}/{filename}" if self._rel_dir else filename

    def reload(self) -> None:
        """重新加载全部规则文件（含 when 开关引用校验）"""
        self._rules.clear()
        self._raw.clear()
        self._files.clear()
        self._errors.clear()
        switch_keys = self._switch_keys()
        loaded: list[TuningRule] = []
        for name in self._resolver.enumerate_entities(self._rel_dir, "*.yaml"):
            path = self._resolver.resolve_read(self._rel(name))
            if path is None:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                rule = parse_tuning_rule(data, switch_keys)
            except Exception as e:
                logger.error(f"调律规则 {name} 加载失败，已跳过: {e}")
                self._errors[Path(name).stem] = str(e)
                continue
            if rule.key in self._files:
                logger.error(f"调律规则 {name} key 重复: {rule.key}")
                continue
            loaded.append(rule)
            self._raw[rule.key] = data
            self._files[rule.key] = name
        for rule in sorted(loaded, key=lambda r: (r.order, r.key)):
            self._rules[rule.key] = rule

    @staticmethod
    def _switch_keys() -> set[str] | None:
        """已注册开关 key 全集（tuning_base 加载失败时 None = 跳过校验）"""
        try:
            return set(get_tuning_base().switches)
        except Exception as e:
            logger.error(f"tuning_base 加载失败，跳过 when 开关校验: {e}")
            return None

    # ── 查询 ──

    def get_rules(self) -> dict[str, TuningRule]:
        """key → TuningRule（按 order 排序）"""
        return dict(self._rules)

    def get_rule(self, key: str) -> TuningRule | None:
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
            parse_tuning_rule(data, self._switch_keys())
            return None
        except RuleValidationError as e:
            return str(e)

    def save_rule(self, key: str, data: dict) -> None:
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload"""
        parse_tuning_rule(data, self._switch_keys())  # 先校验
        filename = self._files.get(key) or f"{key}.yaml"
        self._resolver.write_entity(
            self._rel(filename),
            yaml.dump(data, allow_unicode=True, sort_keys=False),
        )
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
        if key in self._files or self._resolver.resolve_read(
                self._rel(f"{key}.yaml")) is not None:
            raise RuleValidationError(f"规则 key 已存在: {key}")
        data = {
            "key": key,
            "name": name,
            "order": 100,
            "playstyles": {},
            "transmute_priority": [],
            "affix_pool": [],
            "patterns": {},
            "default_rating": "excellent",
        }
        parse_tuning_rule(data)  # 骨架自校验
        self._resolver.write_entity(
            self._rel(f"{key}.yaml"),
            yaml.dump(data, allow_unicode=True, sort_keys=False),
        )
        self.reload()

    def delete_rule(self, key: str) -> None:
        """删除规则文件并 reload

        Raises:
            RuleValidationError: key 未注册
        """
        filename = self._files.get(key)
        if filename is None:
            raise RuleValidationError(f"规则不存在: {key}")
        self._resolver.delete_entity(self._rel(filename))
        self.reload()

    def rename_rule(self, old_key: str, new_key: str) -> None:
        """重命名规则 key（同步重命名 YAML 文件、更新 data 内 key 字段并 reload）

        Raises:
            RuleValidationError: 旧 key 未注册 / 新 key 非法或已存在
        """
        old_key = old_key.strip()
        new_key = new_key.strip()
        if old_key not in self._files:
            raise RuleValidationError(f"规则不存在: {old_key}")
        if not _KEY_RE.match(new_key):
            raise RuleValidationError(
                "规则 key 须为小写字母开头的英文/数字/下划线")
        if new_key != old_key and (
                new_key in self._files
                or self._resolver.resolve_read(
                    self._rel(f"{new_key}.yaml")) is not None):
            raise RuleValidationError(f"规则 key 已存在: {new_key}")
        if new_key == old_key:
            return
        # 同步更新 data 内 key 字段，避免 reload 后 key 与文件名不一致
        data = self._raw.get(old_key) or {}
        data["key"] = new_key
        self._resolver.write_entity(
            self._rel(f"{new_key}.yaml"),
            yaml.dump(data, allow_unicode=True, sort_keys=False),
        )
        self._resolver.delete_entity(self._rel(self._files[old_key]))
        self.reload()


# ─── 全局单例 ──────────────────────────────────────────────

_instance: TuningRuleManager | None = None


def get_tuning_rule_manager() -> TuningRuleManager:
    """获取全局 TuningRuleManager 单例"""
    global _instance
    if _instance is None:
        _instance = TuningRuleManager()
    return _instance


# ─── 基础配置管理器（tuning_base.yaml） ──────────────────

class TuningBaseManager:
    """基础配置管理器（单文件 tuning_base.yaml）

    提供加载、校验、原始数据访问（UI 编辑用）与保存 + reload。
    """

    def __init__(self, path: str | Path | None = None):
        # path 非空（测试/孤立文件）时直读直写；否则走聚合键值接口
        self._path = Path(path) if path is not None else None
        self._base = TuningBase()
        self._raw: dict = {}
        self.reload()

    def reload(self) -> None:
        if self._path is not None:
            with open(self._path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = get_resolver().load_merged(_BASE_REL_PATH)
        self._raw = data
        self._base = parse_tuning_base(data)

    def get(self) -> TuningBase:
        return self._base

    def get_raw(self) -> dict:
        return copy.deepcopy(self._raw)

    def validate(self, data: dict) -> str | None:
        try:
            parse_tuning_base(data)
            return None
        except RuleValidationError as e:
            return str(e)

    def save(self, data: dict) -> None:
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload

        被规则条件组 when 引用的开关禁止删除。
        """
        base = parse_tuning_base(data)
        referenced: set[str] = set()
        for rule in get_tuning_rule_manager().get_rules().values():
            referenced |= rule.referenced_switches()
        removed = sorted(referenced - set(base.switches))
        if removed:
            raise RuleValidationError(
                f"开关仍被规则条件组引用，禁止删除: {removed}")
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        else:
            get_resolver().save_merged(_BASE_REL_PATH, data)
        self.reload()
        # 开关集变更后重新校验全部规则的 when 引用
        get_tuning_rule_manager().reload()


_tuning_base_manager: TuningBaseManager | None = None


def get_tuning_base_manager() -> TuningBaseManager:
    """获取全局 TuningBaseManager 单例"""
    global _tuning_base_manager
    if _tuning_base_manager is None:
        _tuning_base_manager = TuningBaseManager()
    return _tuning_base_manager


def get_tuning_base() -> TuningBase:
    """获取全局基础配置（品阶门槛 + 开关注册表）"""
    return get_tuning_base_manager().get()


def _on_config_change(rel_path: str):
    """配置写入后的失效通知：已创建的单例缓存失效重载"""
    if rel_path.startswith(_RULES_REL_DIR + "/") and _instance is not None:
        _instance.reload()
    elif rel_path == _BASE_REL_PATH and _tuning_base_manager is not None:
        _tuning_base_manager.reload()


get_resolver().add_change_listener(_on_config_change)
