"""调律规则 / 基础配置的加载、缓存、创建/删除与保存管理器"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml
from loguru import logger

from .models import RuleValidationError, TuningBase, TuningRule
from .parsing import _KEY_RE, parse_tuning_base, parse_tuning_rule


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
        self._rules: dict[str, TuningRule] = {}
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
        loaded: list[TuningRule] = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                rule = parse_tuning_rule(data)
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
            parse_tuning_rule(data)
            return None
        except RuleValidationError as e:
            return str(e)

    def save_rule(self, key: str, data: dict) -> None:
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload"""
        parse_tuning_rule(data)  # 先校验
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
            "playstyles": {},
            "transmute_priority": [],
            "affix_pool": [],
            "patterns": {},
        }
        parse_tuning_rule(data)  # 骨架自校验
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


# ─── 基础配置管理器（tuning_base.yaml） ──────────────────

class TuningBaseManager:
    """基础配置管理器（单文件 tuning_base.yaml）

    提供加载、校验、原始数据访问（UI 编辑用）与保存 + reload。
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from src.constants import SYSTEM_CONFIG_DIR
            path = SYSTEM_CONFIG_DIR / "yysls" / "tuning_base.yaml"
        self._path = Path(path)
        self._base = TuningBase()
        self._raw: dict = {}
        self.reload()

    def reload(self) -> None:
        with open(self._path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
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
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload"""
        parse_tuning_base(data)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        self.reload()


_tuning_base_manager: TuningBaseManager | None = None


def get_tuning_base_manager() -> TuningBaseManager:
    """获取全局 TuningBaseManager 单例"""
    global _tuning_base_manager
    if _tuning_base_manager is None:
        _tuning_base_manager = TuningBaseManager()
    return _tuning_base_manager


def get_tuning_base() -> TuningBase:
    """获取全局基础配置（品阶门槛 + PVP 等价）"""
    return get_tuning_base_manager().get()
