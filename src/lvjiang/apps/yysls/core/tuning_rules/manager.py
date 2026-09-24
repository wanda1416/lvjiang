"""调律规则 / 基础规则组 / 流派规则的加载、缓存、创建/删除与保存管理器"""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path

import yaml
from loguru import logger

from lvjiang.core.config.resolver import LAYER_LOCAL, ConfigResolver, get_resolver

from .....i18n import tr
from .models import (
    DEFAULT_ORDER,
    RuleValidationError,
    TuneConfig,
    TuningGroup,
    TuningRule,
)
from .parsing import (
    _KEY_RE,
    parse_tune_config,
    parse_tuning_group,
    parse_tuning_rule,
)

# 规则目录相对 config 层根的路径
_RULES_REL_DIR = "yysls/tuning_rules"
#: 「停用桩」：用户模式停用系统规则时，local 只写这几个键（disabled 加上
#: 身份/版本元数据），规则内容仍来自 system/remote。用户看一眼文件就知道
#: 删掉它等于重新启用，且不会丢自己的修改——因为本来就没有修改。
_STUB_KEYS = frozenset({"key", "disabled", "content_version"})
_GROUPS_REL_DIR = "yysls/base_groups"
_CONFIG_REL_PATH = "yysls/tune_config.yaml"

# ─── 规则管理器 ──────────────────────────────────────────────

class TuningRuleManager:
    """调律规则管理器

    加载目录下全部 YAML，校验失败的文件记录错误并跳过；
    提供按 tuning_rules 顺序排列（仅含启用规则）的规则注册表、
    原始数据访问（UI 编辑用）、创建/删除与保存 + reload。
    """

    def __init__(self, rules_dir: str | Path | None = None, *,
                 resolver: ConfigResolver | None = None,
                 origin_resolver: ConfigResolver | None = None,
                 tune_config_getter: Callable[[], TuneConfig] | None = None):
        self._tune_config_getter = tune_config_getter
        if resolver is not None:
            self._resolver = resolver
            self._rel_dir = _RULES_REL_DIR
        elif rules_dir is None:
            self._resolver = get_resolver()
            self._rel_dir = _RULES_REL_DIR
        else:
            # 测试/孤立目录：单层语义（system=local=rules_dir，直写直删）
            self._resolver = ConfigResolver(
                system_dir=rules_dir, local_dir=rules_dir, dev_mode=True)
            self._rel_dir = ""
        # 隔离编辑会话会把当前有效基底物化到临时 system，以便编辑后立即
        # 预览；版本来源展示不能因此把真实 remote 误标成 system。
        self._origin_resolver = origin_resolver or self._resolver
        self._rules: dict[str, TuningRule] = {}
        self._raw: dict[str, dict] = {}
        self._files: dict[str, str] = {}   # key -> 文件名
        self._all_names: dict[str, str] = {}  # 含禁用规则，供 UI 导航
        self._all_raw: dict[str, dict] = {}   # 含禁用规则的原始 dict
        self._all_files: dict[str, str] = {}  # 含禁用规则的文件名
        self._stubs: set[str] = set()         # local 只是停用桩的规则 key
        self._errors: dict[str, str] = {}
        self.reload()

    def _rel(self, filename: str) -> str:
        return f"{self._rel_dir}/{filename}" if self._rel_dir else filename

    def is_system_rule(self, key: str) -> bool:
        """规则是否来自 system 层且当前用户无权删除或重命名。"""
        filename = self._files.get(key, f"{key}.yaml")
        return bool(
            not self._resolver.is_dev_mode()
            and self._resolver.is_system_entity(self._rel(filename))
        )

    def reload(self) -> None:
        """重新加载全部规则文件（含 when 开关引用校验）

        存在性由目录决定（system ∪ local ∪ remote），顺序与启停由规则文件
        自己的 ``order`` / ``disabled`` 声明：``order`` 升序、同序按 key；
        ``disabled: true`` 的规则不进注册表，但保留在 ``_all_names`` /
        ``_all_raw`` 供 UI 导航与重新启用。
        """
        self._rules.clear()
        self._raw.clear()
        self._files.clear()
        self._all_names.clear()
        self._all_raw.clear()
        self._all_files.clear()
        self._errors.clear()
        self._migrate_legacy_declarations()
        switch_keys = self._switch_keys()
        loaded: dict[str, TuningRule] = {}
        self._stubs.clear()
        for name in self._resolver.enumerate_entities(self._rel_dir, "*.yaml"):
            path = self._resolver.resolve_read(self._rel(name))
            if path is None:
                continue
            try:
                data, is_stub = self._load_rule_data(name, path)
                rule = parse_tuning_rule(data, switch_keys)
            except Exception as e:
                logger.error(f"调律规则 {name} 加载失败，已跳过: {e}")
                self._errors[Path(name).stem] = str(e)
                continue
            if rule.key in self._all_files:
                logger.error(f"调律规则 {name} key 重复: {rule.key}")
                continue
            self._all_names[rule.key] = rule.name
            self._all_raw[rule.key] = data
            self._all_files[rule.key] = name
            if is_stub:
                self._stubs.add(rule.key)
            if rule.disabled:
                continue
            loaded[rule.key] = rule
            self._raw[rule.key] = data
            self._files[rule.key] = name
        for key in sorted(loaded, key=lambda k: (loaded[k].order, k)):
            self._rules[key] = loaded[key]
        # 导航顺序也按 order 排，禁用规则一样参与排序
        self._all_names = {
            key: self._all_names[key]
            for key in sorted(
                self._all_names,
                key=lambda k: (int(self._all_raw[k].get("order", DEFAULT_ORDER)), k))
        }

    def _migrate_legacy_declarations(self) -> None:
        """一次性迁移：tune_config 里旧的 ``tuning_rules`` 启停声明 → 规则文件的
        ``disabled``，然后把 ``tuning_rules`` / ``base_rules`` 两段从
        tune_config 移除。声明里的顺序不迁移（预置规则已随包写好 order）。"""
        try:
            data = self._resolver.load_merged(_CONFIG_REL_PATH)
        except Exception:  # noqa: BLE001 — 读不到就没有可迁的
            return
        if "tuning_rules" not in data and "base_rules" not in data:
            return
        legacy = data.get("tuning_rules") or {}
        if isinstance(legacy, dict):
            for key, enabled in legacy.items():
                if enabled:
                    continue
                rel = self._rel(f"{key}.yaml")
                path = self._resolver.resolve_read(rel)
                if path is None:
                    continue
                try:
                    raw = self._resolver._load_yaml(path)
                except Exception:  # noqa: BLE001
                    continue
                if raw.get("disabled"):
                    continue
                if self._stub_applicable(rel):
                    payload = {"key": key, "disabled": True}
                else:
                    raw["disabled"] = True
                    payload = raw
                self._resolver.write_entity(
                    rel, yaml.dump(payload, allow_unicode=True, sort_keys=False),
                    force=True)
                logger.info(f"已把旧 tuning_rules 启停声明迁入规则文件: {key} → disabled")
        data.pop("tuning_rules", None)
        data.pop("base_rules", None)
        try:
            self._resolver.save_merged(_CONFIG_REL_PATH, data)
            logger.info("tune_config.yaml 已移除旧的 tuning_rules / base_rules 声明")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"移除 tune_config 旧声明失败: {e}")
        if _tune_config_manager is not None:
            _tune_config_manager.reload()

    def _base_path(self, rel: str):
        """越过 local 影子，取 remote/system 里实际的规则内容路径。"""
        if self._resolver.remote_supersedes(rel):
            return self._resolver.remote_dir / rel
        system = self._resolver.system_dir / rel
        return system if system.exists() else None

    def _load_rule_data(self, name: str, path) -> tuple[dict, bool]:
        """读一份规则；local 是停用桩时把 disabled 叠到 base 内容上。

        返回 ``(data, is_stub)``。桩的判定：local 文件的键全部落在
        ``_STUB_KEYS`` 内，且 remote/system 有这条规则的正文。
        """
        rel = self._rel(name)
        data = self._resolver._load_yaml(path) or {}
        if (self._resolver.system_dir != self._resolver.local_dir
                and Path(path) == self._resolver.local_dir / rel
                and set(data) <= _STUB_KEYS):
            base_path = self._base_path(rel)
            if base_path is not None:
                base = self._resolver._load_yaml(base_path) or {}
                base["disabled"] = bool(data.get("disabled", False))
                return base, True
        return data, False

    def is_rule_local_stub(self, key: str) -> bool:
        """local 里只有停用桩、正文仍来自系统/远程。"""
        return key in self._stubs

    def _switch_keys(self) -> set[str] | None:
        """已注册开关 key 全集（tune_config 加载失败时 None = 跳过校验）"""
        try:
            config = (self._tune_config_getter()
                      if self._tune_config_getter is not None
                      else get_tune_config())
            return set(config.switches)
        except Exception as e:
            logger.error(f"tune_config 加载失败，跳过 when 开关校验: {e}")
            return None

    # ── 查询 ──

    def get_rules(self) -> dict[str, TuningRule]:
        """key → TuningRule（按 order 升序；仅含启用规则）"""
        return dict(self._rules)

    def get_rule(self, key: str) -> TuningRule | None:
        return self._rules.get(key)

    def get_raw(self, key: str) -> dict:
        """原始 YAML dict 的深拷贝（UI 编辑用）"""
        return copy.deepcopy(self._raw.get(key) or {})

    def rule_rel_path(self, key: str) -> str:
        """规则实体在配置层里的相对路径"""
        return self._rel(self._all_files.get(key) or f"{key}.yaml")

    def is_rule_enabled(self, key: str) -> bool:
        """规则是否启用（文件里的 ``disabled`` 取反）；未知规则视为启用。"""
        raw = self._all_raw.get(key)
        return True if raw is None else not bool(raw.get("disabled", False))

    def describe_rule_version(self, key: str):
        """返回规则实体来源与版本，供配置页展示。

        local 只是停用桩时，生效的正文仍是系统/远程那一份，展示它而不是桩。
        """
        rel = self.rule_rel_path(key)
        if key in self._stubs:
            for origin in self._origin_resolver.list_entity_origins(rel):
                if origin.layer != LAYER_LOCAL:
                    return origin
        return self._origin_resolver.describe_entity(rel)

    def list_rule_versions(self, key: str):
        """返回规则在本地、远程、系统各层现存的版本。"""
        return self._origin_resolver.list_entity_origins(self.rule_rel_path(key))

    def system_save_override(self, key: str):
        """开发模式写 system 后仍由其他层生效时，返回该来源。

        local 恒高于 system；remote 在版本更新时也会顶替 system。两者都会
        让“已保存并生效”成为假话，不能只检查 remote。
        """
        from lvjiang.core.config.resolver import LAYER_SYSTEM

        if not self._resolver.is_dev_mode():
            return None      # 用户模式写 local，local 恒赢，不存在这个问题
        if self._resolver.system_dir == self._resolver.local_dir:
            return None      # 测试/孤立目录采用单层直写语义
        origin = self._resolver.describe_entity(self.rule_rel_path(key))
        return origin if origin.layer and origin.layer != LAYER_SYSTEM else None

    def can_bump_rule_version(self) -> bool:
        return self._resolver.is_dev_mode()

    def bump_rule_version(self, key: str,
                          data: dict | None = None) -> int:
        """显式提升规则版本并立即写盘生效。

        ``data`` 是 UI 正在编辑的完整规则。remote 正在顶替 system
        时，普通自动保存会保留 system 旧版本，reload 仍会读到 remote；
        因此提升时必须优先使用 UI 工作副本，不能反过来用 reload
        后的 remote 缓存覆盖掉用户刚做的修改。
        """
        filename = self._files.get(key) or f"{key}.yaml"
        rel_path = self._rel(filename)
        source = self._resolver.resolve_read(rel_path)
        if source is None:
            raise RuleValidationError(f"规则不存在: {key}")
        target = self._resolver.next_entity_version(rel_path)
        content = copy.deepcopy(data) if data else self._raw.get(key)
        if content is None:
            with open(source, "r", encoding="utf-8") as f:
                content = yaml.safe_load(f) or {}
        parse_tuning_rule(content, self._switch_keys())
        self._resolver.write_entity(
            rel_path,
            yaml.dump(content, allow_unicode=True, sort_keys=False),
            content_version=target,
        )
        self.reload()
        return target

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
        filename = self._all_files.get(key) or f"{key}.yaml"
        self._resolver.write_entity(
            self._rel(filename),
            yaml.dump(data, allow_unicode=True, sort_keys=False),
        )
        self.reload()

    def create_rule(self, key: str, name: str) -> None:
        """新建规则（最小骨架 YAML），key 作为文件名

        新建规则默认启用，``order`` 写默认值 10。

        Raises:
            RuleValidationError: key 非法 / 已存在 / 名称为空
        """
        key = key.strip()
        name = name.strip()
        if not _KEY_RE.match(key):
            raise RuleValidationError(
                tr("规则 key 须为小写字母开头的英文/数字/下划线"))
        if not name:
            raise RuleValidationError(tr("规则名称不能为空"))
        if key in self._all_files or self._resolver.resolve_read(
                self._rel(f"{key}.yaml")) is not None:
            raise RuleValidationError(f"规则 key 已存在: {key}")
        data = {
            "key": key,
            "name": name,
            "order": DEFAULT_ORDER,
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
        """删除规则文件，然后 reload

        Raises:
            RuleValidationError: key 未注册
        """
        filename = self._all_files.get(key)
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
        if old_key not in self._all_files:
            raise RuleValidationError(f"规则不存在: {old_key}")
        if not _KEY_RE.match(new_key):
            raise RuleValidationError(
                tr("规则 key 须为小写字母开头的英文/数字/下划线"))
        if new_key != old_key and (
                new_key in self._all_files
                or self._resolver.resolve_read(
                    self._rel(f"{new_key}.yaml")) is not None):
            raise RuleValidationError(f"规则 key 已存在: {new_key}")
        if new_key == old_key:
            return
        old_rel = self._rel(self._all_files[old_key])
        # 必须先鉴权再写新文件，避免系统规则删除被拒后留下新 key 的孤立影子。
        self._resolver.ensure_entity_deletable(old_rel)
        # 同步更新 data 内 key 字段，避免 reload 后 key 与文件名不一致
        data = copy.deepcopy(self._all_raw.get(old_key) or {})
        data["key"] = new_key
        self._resolver.write_entity(
            self._rel(f"{new_key}.yaml"),
            yaml.dump(data, allow_unicode=True, sort_keys=False),
        )
        self._resolver.delete_entity(old_rel)
        self.reload()

    def set_rule_enabled(self, key: str, enabled: bool) -> None:
        """设置规则启用状态：写规则文件自己的 ``disabled`` 字段。

        用户模式停用一条没改过的系统/远程规则时，local 只写一个停用桩
        （``key`` + ``disabled: true``），正文继续跟随系统更新；重新启用就是
        删掉这个桩。local 已有完整影子（用户改过内容）时才在影子里改字段。
        """
        raw = self._all_raw.get(key)
        if raw is None:
            raise RuleValidationError(f"规则不存在: {key}")
        rel = self.rule_rel_path(key)
        if self._stub_applicable(rel):
            if enabled:
                self._resolver.revert_entity_to_system(rel)
            else:
                self._resolver.write_entity(
                    rel, yaml.dump({"key": key, "disabled": True},
                                   allow_unicode=True, sort_keys=False),
                    force=True)
            self.reload()
            return
        data = copy.deepcopy(raw)
        if enabled:
            data.pop("disabled", None)
        else:
            data["disabled"] = True
        self._resolver.write_entity(
            rel, yaml.dump(data, allow_unicode=True, sort_keys=False))
        self.reload()

    def _stub_applicable(self, rel: str) -> bool:
        """用户模式、正文在系统/远程、local 要么没有要么只是桩 → 走桩逻辑。"""
        if self._resolver.is_dev_mode():
            return False
        if self._base_path(rel) is None:
            return False
        local = self._resolver.local_dir / rel
        if not local.exists():
            return True
        try:
            return set(self._resolver._load_yaml(local) or {}) <= _STUB_KEYS
        except Exception:  # noqa: BLE001 — 读不出来就按完整影子处理
            return False

    def get_all_rule_keys_and_names(self) -> list[tuple[str, str]]:
        """全部规则 key + 名称（含禁用），供对话框导航使用"""
        return list(self._all_names.items())


# ─── 全局单例 ──────────────────────────────────────────────

_instance: TuningRuleManager | None = None


def get_tuning_rule_manager() -> TuningRuleManager:
    """获取全局 TuningRuleManager 单例"""
    global _instance
    if _instance is None:
        _instance = TuningRuleManager()
    return _instance


# ─── 基础规则组管理器（base_groups/） ──────────────────────

class TuningGroupManager:
    """基础规则组管理器（目录型，一组一个 YAML）

    存在性由目录决定（system ∪ local ∪ remote），展示顺序由文件自己的
    ``order`` 声明（升序、同序按 key）；校验失败的文件记录错误并跳过。
    提供原始数据访问（UI 编辑用）、新增/复制/删除、保存与跨层搬移 + reload。
    """

    def __init__(self, groups_dir: str | Path | None = None, *,
                 resolver: ConfigResolver | None = None):
        if resolver is not None:
            self._resolver = resolver
            self._rel_dir = _GROUPS_REL_DIR
        elif groups_dir is None:
            self._resolver = get_resolver()
            self._rel_dir = _GROUPS_REL_DIR
        else:
            # 测试/孤立目录：单层语义（system=local=groups_dir，直写直删）
            self._resolver = ConfigResolver(
                system_dir=groups_dir, local_dir=groups_dir, dev_mode=True)
            self._rel_dir = ""
        self._groups: dict[str, TuningGroup] = {}
        self._raw: dict[str, dict] = {}
        self._files: dict[str, str] = {}   # key -> 文件名
        self._errors: dict[str, str] = {}
        self.reload()

    def _rel(self, filename: str) -> str:
        return f"{self._rel_dir}/{filename}" if self._rel_dir else filename

    def group_rel_path(self, key: str) -> str:
        return self._rel(self._files.get(key) or f"{key}.yaml")

    def is_system_group(self, key: str) -> bool:
        """规则组是否来自 system 层且当前用户无权删除。"""
        filename = self._files.get(key)
        return bool(
            filename
            and not self._resolver.is_dev_mode()
            and self._resolver.is_system_entity(self._rel(filename))
        )

    def layer_of(self, key: str) -> str:
        """规则组文件实际生效的层（system / local / remote）。"""
        return self._resolver.describe_entity(self.group_rel_path(key)).layer

    def can_choose_layer(self) -> bool:
        """只有开发模式才能指定文件放 system 还是 local。"""
        return (self._resolver.is_dev_mode()
                and self._resolver.system_dir != self._resolver.local_dir)

    def reload(self) -> None:
        """扫描目录重新加载规则组，按 order 排序"""
        self._groups.clear()
        self._raw.clear()
        self._files.clear()
        self._errors.clear()
        loaded: dict[str, tuple[TuningGroup, dict, str]] = {}
        for filename in self._resolver.enumerate_entities(self._rel_dir, "*.yaml"):
            path = self._resolver.resolve_read(self._rel(filename))
            if path is None:
                continue
            try:
                data = self._resolver._load_yaml(path)
                group = parse_tuning_group(data)
            except Exception as e:
                logger.error(f"基础规则组 {filename} 加载失败，已跳过: {e}")
                self._errors[Path(filename).stem] = str(e)
                continue
            if group.key != Path(filename).stem:
                logger.error(
                    f"基础规则组 {filename} key 不匹配: "
                    f"文件内 {group.key!r} != 文件名 {Path(filename).stem!r}")
                self._errors[Path(filename).stem] = (
                    f"key 不匹配: {group.key!r} != {Path(filename).stem!r}")
                continue
            loaded[group.key] = (group, data, filename)
        for key in sorted(loaded, key=lambda k: (loaded[k][0].order, k)):
            group, data, filename = loaded[key]
            self._groups[key] = group
            self._raw[key] = data
            self._files[key] = filename

    # ── 查询 ──

    def get_groups(self) -> dict[str, TuningGroup]:
        """统一显示顺序：系统/远程在前、本地在后，再按 order、key 升序。"""
        return {
            key: self._groups[key]
            for key in sorted(self._groups, key=lambda key: (
                self.layer_of(key) == LAYER_LOCAL,
                self._groups[key].order,
                key,
            ))
        }

    def get_group(self, key: str) -> TuningGroup | None:
        return self._groups.get(key)

    def get_raw(self, key: str) -> dict:
        """原始 YAML dict 的深拷贝（UI 编辑用）"""
        return copy.deepcopy(self._raw.get(key) or {})

    @property
    def errors(self) -> dict[str, str]:
        """加载失败的文件（文件名 stem → 错误信息）"""
        return dict(self._errors)

    # ── 保存 / 创建 / 复制 / 删除 / 搬移 ──

    def validate(self, data: dict) -> str | None:
        """校验原始 dict；返回错误文案（None 表示通过）"""
        try:
            parse_tuning_group(data)
            return None
        except RuleValidationError as e:
            return str(e)

    def _write(self, key: str, data: dict, layer: str | None) -> None:
        """写规则组文件；``layer`` 只在开发模式下允许指定（None 按模式默认）。"""
        self._resolver.write_entity(
            self._rel(self._files.get(key) or f"{key}.yaml"),
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            layer=layer,
        )

    def save_group(self, key: str, data: dict) -> None:
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload。

        开发模式下写回文件当前所在层（local 里的组不会被“保存”悄悄搬进 system）。
        """
        parse_tuning_group(data)  # 先校验
        layer = None
        if self.can_choose_layer() and key in self._files:
            current = self.layer_of(key)
            layer = current if current in ("system", "local") else None
        self._write(key, data, layer)
        self.reload()

    def _check_new_key(self, key: str, name: str) -> None:
        if not _KEY_RE.match(key):
            raise RuleValidationError(
                tr("规则组 key 须为小写字母开头的英文/数字/下划线"))
        if not name:
            raise RuleValidationError(tr("规则组名称不能为空"))
        if key in self._files or self._resolver.resolve_read(
                self._rel(f"{key}.yaml")) is not None:
            raise RuleValidationError(f"规则组 key 已存在: {key}")

    def create_group(self, key: str, name: str, *,
                     layer: str | None = None) -> None:
        """新建空白规则组（仅含 key/name/order，其余全空，由 UI 页逐段编辑）

        ``layer``：开发模式可指定 ``system`` / ``local``；None 按模式默认。

        Raises:
            RuleValidationError: key 非法 / 已存在 / 名称为空
        """
        key = key.strip()
        name = name.strip()
        self._check_new_key(key, name)
        data = {
            "key": key,
            "name": name,
            "order": DEFAULT_ORDER,
            "description": "",
            "materials": {"food_rules": []},
            "scan": {"rules": []},
            "tune": {"rules": []},
        }
        parse_tuning_group(data)  # 空白骨架自校验
        self._write(key, data, layer)
        self.reload()

    def copy_group(self, src_key: str, new_key: str, new_name: str, *,
                   layer: str | None = None) -> None:
        """复制规则组为独立副本

        Raises:
            RuleValidationError: 源组不存在 / 新 key 非法或已存在
        """
        new_key = new_key.strip()
        new_name = new_name.strip()
        if src_key not in self._files:
            raise RuleValidationError(f"规则组不存在: {src_key}")
        self._check_new_key(new_key, new_name)
        data = copy.deepcopy(self._raw.get(src_key) or {})
        data["key"] = new_key
        data["name"] = new_name
        data.setdefault("order", DEFAULT_ORDER)
        parse_tuning_group(data)
        self._write(new_key, data, layer)
        self.reload()

    def move_group(self, key: str, layer: str) -> None:
        """开发模式：把规则组文件搬到另一层（写目标层，删源层）。

        Raises:
            RuleValidationError: 组不存在 / 当前模式不允许指定层 / 目标层相同
        """
        if key not in self._files:
            raise RuleValidationError(f"规则组不存在: {key}")
        if not self.can_choose_layer():
            raise RuleValidationError(tr("只有开发模式可以指定保存位置"))
        if layer not in ("system", "local"):
            raise RuleValidationError(f"未知保存位置: {layer}")
        current = self.layer_of(key)
        if current == layer:
            return
        rel = self.group_rel_path(key)
        self._resolver.write_entity(
            rel, yaml.dump(self._raw[key], allow_unicode=True, sort_keys=False),
            layer=layer)
        if current in ("system", "local"):
            self._resolver.delete_entity(rel, layer=current)
        self.reload()

    def delete_group(self, key: str) -> None:
        """删除规则组文件并 reload（至少保留一个）

        开发模式删的是文件实际所在层；用户模式只能删自己的 local 影子。

        Raises:
            RuleValidationError: key 未注册 / 仅剩一个规则组
        """
        filename = self._files.get(key)
        if filename is None:
            raise RuleValidationError(f"规则组不存在: {key}")
        if len(self._files) <= 1:
            raise RuleValidationError(tr("至少保留一个规则组"))
        layer = None
        if self.can_choose_layer():
            current = self.layer_of(key)
            layer = current if current in ("system", "local") else None
        self._resolver.delete_entity(self._rel(filename), layer=layer)
        self.reload()


_group_manager: TuningGroupManager | None = None


def get_tuning_group_manager() -> TuningGroupManager:
    """获取全局 TuningGroupManager 单例"""
    global _group_manager
    if _group_manager is None:
        _group_manager = TuningGroupManager()
    return _group_manager


def get_tuning_group(key: str) -> TuningGroup | None:
    """按 key 获取基础规则组（不存在时 None）"""
    return get_tuning_group_manager().get_group(key)


class TuneConfigManager:
    """全局调律配置管理器（单文件 tune_config.yaml）

    承载品阶门槛与开关注册表；提供加载、校验、
    原始数据访问（UI 编辑用）与保存 + reload。
    """

    def __init__(self, path: str | Path | None = None, *,
                 resolver: ConfigResolver | None = None):
        # path 非空（测试/孤立文件）时直读直写；否则走聚合键值接口
        self._path = Path(path) if path is not None else None
        self._resolver = resolver or get_resolver()
        self._rule_manager: TuningRuleManager | None = None
        self._config = TuneConfig()
        self._raw: dict = {}
        self.reload()

    def reload(self) -> None:
        if self._path is not None:
            with open(self._path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = self._resolver.load_merged(_CONFIG_REL_PATH)
        self._raw = data
        self._config = parse_tune_config(data)

    def get(self) -> TuneConfig:
        return self._config

    def get_raw(self) -> dict:
        return copy.deepcopy(self._raw)

    def validate(self, data: dict) -> str | None:
        try:
            parse_tune_config(data)
            return None
        except RuleValidationError as e:
            return str(e)

    def save(self, data: dict) -> None:
        """校验并写盘（校验失败抛 RuleValidationError），然后 reload

        被规则条件组 when 引用的开关禁止删除。
        """
        config = parse_tune_config(data)
        referenced: set[str] = set()
        rule_manager = self._rule_manager or get_tuning_rule_manager()
        for rule in rule_manager.get_rules().values():
            referenced |= rule.referenced_switches()
        removed = sorted(referenced - set(config.switches))
        if removed:
            raise RuleValidationError(
                f"开关仍被规则条件组引用，禁止删除: {removed}")
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        else:
            self._resolver.save_merged(_CONFIG_REL_PATH, data)
        self.reload()
        # 开关集变更后重新校验全部规则的 when 引用
        rule_manager.reload()

    def set_rule_manager(self, manager: TuningRuleManager) -> None:
        """绑定同一编辑会话内的规则管理器，避免校验读取全局缓存。"""
        self._rule_manager = manager


_tune_config_manager: TuneConfigManager | None = None


def get_tune_config_manager() -> TuneConfigManager:
    """获取全局 TuneConfigManager 单例"""
    global _tune_config_manager
    if _tune_config_manager is None:
        _tune_config_manager = TuneConfigManager()
    return _tune_config_manager


def get_tune_config() -> TuneConfig:
    """获取全局调律配置。"""
    return get_tune_config_manager().get()


def _on_config_change(rel_path: str):
    """配置写入后的失效通知：已创建的单例缓存失效重载"""
    if rel_path.startswith(_RULES_REL_DIR + "/") and _instance is not None:
        _instance.reload()
    elif rel_path.startswith(
            _GROUPS_REL_DIR + "/") and _group_manager is not None:
        _group_manager.reload()
    elif rel_path == _CONFIG_REL_PATH and _tune_config_manager is not None:
        _tune_config_manager.reload()


get_resolver().add_change_listener(_on_config_change)
