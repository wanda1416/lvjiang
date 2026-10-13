"""配置解析器 —— local 与 system 两层配置的唯一读写入口。

system 为已经安装的官方内容；local 为用户覆盖；updates 仅负责更新归档。
session 为运行与用户数据，不经过此模块。读取 local > system；
开发模式写 system，用户模式写 local。在线安装器独立负责 system 的事务替换。

模式判定：LVJIANG_DEV_MODE 环境变量（1/0）强制覆盖 > PROJECT_ROOT/.git 探测。

两档合并语义：
- 实体文件（一物一文件：scenes/*.yaml、workflows/*.wf、
  layouts/{name}/{scene}.json、yysls/tuning_rules/*.yaml、
  references/**/*.png）→ 整文件影子（local 有就完全顶掉，不做内容合并）
- 聚合键值文件（scenes.yaml、layouts.yaml、
  yysls/game_config/*.yaml、yysls/tune_config.yaml）→ 键级 diff 深合并；
  dict 递归、列表与标量整键替换；
  每层 dict 支持 "__deleted__": [key, ...] 删除键

列表的两种语义：
- 枚举设定（quality_thresholds.武器、mouse_move_duration 等）→ 整键替换，
  用户就是要覆盖系统值
- 注册表（REGISTRY_LIST_PATHS 声明的路径）→
  local 只存 __added__ / __removed__ / __order__ 增量。整键替换会让 local
  冻住整张表，系统后续新增的条目永远进不到合并视图；用户除非删掉自己的
  local 否则再也看不到更新。存量 local 里的普通列表仍按整键替换处理，
  用户下次保存时由 compute_diff 自动转成增量形式。

以上举例中的 yysls/* 路径仅用于说明两档合并语义长什么样——本模块（core）
不认识任何插件领域词汇。这些路径的登记表/受保护列表声明由插件自己经
register_registry_list_paths / register_protected_list_paths 注册，
不写进本文件的常量表，见两个函数的定义处。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Callable

import yaml
from loguru import logger

from ... import constants
from ..fs_util import atomic_write_bytes, atomic_write_text
from ..paths import get_config_local_dir, get_config_system_dir
from . import versioning

# 聚合 diff 中的删除键标记
DELETED_KEY = "__deleted__"

#: 实体来源层标识（见 ConfigResolver.describe_entity）。展示用的中文标签留给
#: UI 层做 i18n，core 只给稳定的 key。
LAYER_LOCAL = "local"
LAYER_SYSTEM = "system"

_write_batches: ContextVar[dict[int, dict[str, None]] | None] = ContextVar("configuration_write_batches", default=None)


def _configuration_write(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        from ..access import require_primary_write
        require_primary_write()
        from .conflicts import checked_write, file_baseline
        from .content import configuration_lock
        rel_path = args[0] if args else kwargs.get("rel_path")
        def write():
            result = function(self, *args, **kwargs)
            if rel_path in self._read_baselines:
                self._read_baselines[rel_path] = file_baseline(self, rel_path)
            return result
        if self.track_content and rel_path in self._read_baselines:
            return checked_write(self, {rel_path: self._read_baselines[rel_path]}, write)
        with configuration_lock(self.config_dir):
            return write()
    return wrapped


@dataclass(frozen=True)
class EntityOrigin:
    """某个实体文件实际生效的来源层与内容版本。

    ``layer`` 为空串表示该实体不存在（两层都没有）；``version`` 为 None
    表示这一层的文件没有 content_version（未参与在线下发的类型都是如此）。
    """

    layer: str
    version: int | None


class SystemContentProtected(PermissionError):
    """试图在用户模式下删除 system 层内容

    系统内容属于开发者提供的初始版本，用户模式（拿不到 system 编辑权限）
    只能改值、复制、另存为、新建，或走激活机制停用，不能删除。
    要真正删除必须切到开发模式（``LVJIANG_DEV_MODE=1`` 或仓库带 .git）。
    """


# 注册表列表的增量键
ADDED_KEY = "__added__"
REMOVED_KEY = "__removed__"
ORDER_KEY = "__order__"
_REGISTRY_KEYS = (ADDED_KEY, REMOVED_KEY, ORDER_KEY)

#: 「注册表列表」路径声明：这些列表是**可增长的条目登记表**，不是枚举设定。
#:
#: 普通列表整键替换是对的——``quality_thresholds.武器: [gold]`` 这类枚举设定，
#: 用户就是要覆盖。但注册表不同：local 若存下完整列表，系统新增的条目就永远
#: 进不到合并视图里，用户除非删掉自己的 local 否则再也看不到更新。
#: 这些路径改存增量（``__added__`` / ``__removed__`` / ``__order__``），
#: 使 system 的新增条目自动出现，同时保留用户的增、删、排序。
#:
#: 路径为点分键名，``*`` 匹配单层任意键。
#:
#: **core.config 不认识任何插件领域词汇**：这里只声明 core 自己拥有的
#: `scenes.yaml`（跨插件通用的场景注册表）。插件私有配置文件（如燕云的
#: `yysls/tune_config.yaml`）的登记表路径由插件自己经
#: :func:`register_registry_list_paths` 声明——见
#: `apps/yysls/config/merge_policy.py`，经 `AppHooks.config_policy_modules`
#: 「import 即注册」接入（同 `builtin_modules`/`telemetry_modules` 的约定）。
REGISTRY_LIST_PATHS: dict[str, tuple[str, ...]] = {
    # v1 路径保留给通用 resolver 的存量兼容；v2 的专用跨 schema 合并见
    # core.scene_config（必须先分别转换 system/local，不能直接混合结构）。
    "scenes.yaml": ("scenes.*.items", "scenes.*.disabled"),
}


def register_registry_list_paths(rel_path: str, paths: tuple[str, ...]) -> None:
    """供插件声明自己私有配置文件里的「注册表列表」路径（见上）。

    插件在自己的配置模块顶层调用本函数（经 `AppHooks.config_policy_modules`
    在插件加载时 import 触发），而不是把路径写进本文件的常量表——
    core.config 不应该认识任何插件领域词汇。
    """
    REGISTRY_LIST_PATHS[rel_path] = tuple(paths)


#: 允许 local 删除 system 内容的路径白名单——**默认禁止删除**。
#:
#: 系统配置是开发者提供的初始内容，用户该做的是改值、复制、另存为、新建，
#: 而不是删掉它。想停用某项应走激活机制（调律规则的 tuning_rules 开关、
#: 脚本的 exposed 暴露列表），不是删除定义本身。允许删除的场景必须先在这里
#: 声明，未声明的删除会被拦下并记 warning。
#:
#: 键为聚合文件相对路径，值为允许删除的**父路径**模式（点分，``*`` 匹配单层
#: 任意键）；``""`` 表示顶层。删除 layouts.<名字> 就写 "layouts"。
DELETABLE_PATHS: dict[str, tuple[str, ...]] = {}

#: 受保护的**列表**：系统条目不允许被移除，但可以改值、可以新增。
#:
#: 列表走整键替换，绕开了 __deleted__ 那条保护——用户存一份少了几项的列表
#: 就把系统条目抹掉了。这里按「条目身份字段」比对：系统条目缺失即补回，
#: 用户新增的条目和对系统条目的改值都原样保留。
#:
#: 形如 {文件: {点分路径: 身份字段}}；身份字段为 None 表示条目本身即身份
#: （纯标量列表）。
#:
#: **core.config 不认识任何插件领域词汇**：core 目前没有自己的受保护列表，
#: 保持空表。插件私有配置文件（如燕云 `yysls/game_config/*.yaml` 的
#: `weapon_types`/`level_configs`/`season_configs`）经
#: :func:`register_protected_list_paths` 由插件自己声明，理由同上。
PROTECTED_LIST_PATHS: dict[str, dict[str, str | None]] = {}


def register_protected_list_paths(rel_path: str, mapping: dict[str, str | None]) -> None:
    """供插件声明自己私有配置文件里的受保护列表及其身份字段（见上）。

    同 :func:`register_registry_list_paths`，由插件在配置模块顶层调用，
    不写进本文件的常量表。
    """
    PROTECTED_LIST_PATHS.setdefault(rel_path, {}).update(mapping)


def _identity(item, field: str | None):
    """取条目身份；取不到返回 None（不参与保护，避免误判）"""
    if field is None:
        return item if isinstance(item, (str, int, float)) else None
    return item.get(field) if isinstance(item, dict) else None


def restore_protected_list(base_list: list, desired: list,
                           field: str | None) -> tuple[list, list]:
    """补回 desired 里缺失的系统条目，返回 (结果, 被补回的身份列表)

    补回位置取系统列表中的原下标（越界则追加），这样既保留用户对留下条目
    的排序，又让补回的条目落在大致原位而不是全堆在末尾。
    """
    kept = {_identity(x, field) for x in desired}
    kept.discard(None)
    result = list(desired)
    restored: list = []
    for index, item in enumerate(base_list or []):
        ident = _identity(item, field)
        if ident is None or ident in kept:
            continue
        result.insert(min(index, len(result)), item)
        restored.append(ident)
    return result, restored


def _path_matches(path: str, patterns: tuple[str, ...]) -> bool:
    """点分路径是否命中任一模式（``*`` 匹配单层任意键）"""
    parts = path.split(".") if path else []
    for pattern in patterns:
        if pattern == "" and not parts:
            return True
        pieces = pattern.split(".") if pattern else []
        if len(pieces) != len(parts):
            continue
        if all(pi == "*" or pi == pa for pi, pa in zip(pieces, parts, strict=True)):
            return True
    return False


def merge_registry_list(base_list: list, overlay: dict) -> list:
    """注册表列表：system 基底 − __removed__ + __added__，再按 __order__ 排序

    __order__ 里没提到的条目（通常是系统后来新增的）排在末尾，保持 system 里的
    相对顺序——新条目宁可靠后也不能消失。
    """
    removed = set(overlay.get(REMOVED_KEY) or [])
    result = [x for x in (base_list or []) if x not in removed]
    for item in overlay.get(ADDED_KEY) or []:
        if item not in result:
            result.append(item)
    order = overlay.get(ORDER_KEY)
    if order:
        rank = {name: i for i, name in enumerate(order)}
        # sort 稳定：未在 order 中的条目保持原相对顺序落在末尾
        result.sort(key=lambda x: (0, rank[x]) if x in rank else (1, 0))
    return result


def diff_registry_list(base_list: list, desired: list) -> dict:
    """求注册表列表的增量；无差异返回空 dict

    仅在「增删还原不出目标顺序」时才写 __order__，保证
    merge_registry_list(base, diff) == desired 恒成立。
    """
    base_list = list(base_list or [])
    desired = list(desired or [])
    added = [x for x in desired if x not in base_list]
    removed = [x for x in base_list if x not in desired]
    diff: dict = {}
    if added:
        diff[ADDED_KEY] = added
    if removed:
        diff[REMOVED_KEY] = removed
    if diff and merge_registry_list(base_list, diff) == desired:
        return diff
    if not diff and base_list == desired:
        return {}
    diff[ORDER_KEY] = desired
    return diff


# ─── 纯函数：聚合 diff 的合并与求解 ─────────────────────────

def merge_doc(base: dict, overlay: dict,
              registry: tuple[str, ...] = (), _prefix: str = "") -> dict:
    """system 文档 ← local diff 深合并（不修改入参）

    dict 递归合并；列表与标量整键替换；overlay 每层的 __deleted__
    列表指定要从结果中删除的键。

    registry 声明哪些点分路径按注册表语义合并（见 REGISTRY_LIST_PATHS）。
    该路径下 overlay 若是 __added__/__removed__/__order__ 形式的 dict 则按增量
    合并；**若仍是普通列表则照旧整键替换**，保持存量 local 文件的行为不变，
    等用户下次保存时由 compute_diff 自动转成增量形式。
    """
    result = deepcopy(base) if isinstance(base, dict) else {}
    if not isinstance(overlay, dict):
        return result
    for key in overlay.get(DELETED_KEY, []) or []:
        result.pop(key, None)
    for key, value in overlay.items():
        if key == DELETED_KEY:
            continue
        path = f"{_prefix}.{key}" if _prefix else key
        if (registry and _path_matches(path, registry)
                and isinstance(value, dict)
                and any(k in value for k in _REGISTRY_KEYS)):
            result[key] = merge_registry_list(result.get(key) or [], value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_doc(result[key], value, registry, path)
        else:
            result[key] = deepcopy(value)
    return result


def compute_diff(base: dict, desired: dict,
                 registry: tuple[str, ...] = (), _prefix: str = "",
                 deletable: tuple[str, ...] | None = None,
                 protected: dict[str, str | None] | None = None) -> dict:
    """逆向求最小 diff，满足 merge_doc(base, diff) == desired

    新增/改动键入 diff；base 有而 desired 无的键入 __deleted__；
    嵌套 dict 递归求解，空节点不写入。

    registry 声明的路径上，两侧都是列表时改求增量（__added__/__removed__，
    必要时补 __order__），local 因此只记用户自己的改动，不再冻住整张表。

    deletable 为 None 时保持「缺键即删除」的原始语义（纯函数用法、单测）；
    传入元组则启用白名单：只有父路径被声明的键才允许进 __deleted__，
    其余缺失一律忽略并记 warning。经 save_merged 调用时恒会传入。

    protected 声明受保护的列表路径及其条目身份字段：系统条目缺失即补回，
    用户的新增与改值照常保留（见 PROTECTED_LIST_PATHS）。
    """
    base = base if isinstance(base, dict) else {}
    diff: dict = {}
    missing = [k for k in base if k not in desired]
    if deletable is None:
        deleted = missing
    elif missing and _path_matches(_prefix, deletable):
        deleted = missing
    else:
        deleted = []
        if missing:
            where = _prefix or "<顶层>"
            logger.warning(
                f"配置删除被拦下：{where} 下的 {missing} 未在 DELETABLE_PATHS 声明。"
                f"系统内容不允许删除——停用请走激活机制，"
                f"调用方若只传了部分文档请改为先 load_merged 取完整文档。")
    if deleted:
        diff[DELETED_KEY] = deleted
    for key, value in desired.items():
        base_value = base.get(key)
        path = f"{_prefix}.{key}" if _prefix else key
        if (protected is not None and path in protected
                and isinstance(value, list) and isinstance(base_value, list)):
            value, restored = restore_protected_list(
                base_value, value, protected[path])
            if restored:
                logger.warning(
                    f"配置删除被拦下：{path} 下的系统条目 {restored} 不允许移除，"
                    f"已补回；如需停用请走激活机制或直接改值")
            if value != base_value:
                diff[key] = deepcopy(value)
        elif (registry and _path_matches(path, registry)
                and isinstance(value, list) and isinstance(base_value, list)):
            sub = diff_registry_list(base_value, value)
            if sub:
                diff[key] = sub
        elif isinstance(value, dict) and isinstance(base_value, dict):
            sub = compute_diff(
                base_value, value, registry, path, deletable, protected)
            if sub:
                diff[key] = sub
        elif key not in base or base_value != value:
            diff[key] = deepcopy(value)
    return diff


# ─── 解析器 ──────────────────────────────────────────────

class ConfigResolver:
    """system/local 两层配置解析器

    不带参构造时各层根目录动态取自 constants（monkeypatch 友好）；
    测试可显式传入 tmp_path 与 dev_mode 构造隔离实例。
    """

    def __init__(
        self,
        system_dir: Path | str | None = None,
        local_dir: Path | str | None = None,
        dev_mode: bool | None = None,
        track_content: bool = True,
        config_dir: Path | None = None,
    ):
        self._system_dir = Path(system_dir) if system_dir else None
        self._local_dir = Path(local_dir) if local_dir else None
        self._dev_mode = dev_mode if dev_mode is not None else self._compute_dev_mode()
        self.track_content = track_content
        # 逻辑安装上下文与实际配置层位置分离；锁、索引和基底不反推父目录。
        self.config_dir = config_dir if config_dir is not None else constants.CONFIG_DIR
        self._read_baselines: dict = {}
        self._listeners: list[Callable[[str], None]] = []
        self._batch_listeners: list[Callable[[tuple[str, ...]], None]] = []
        # YAML 解析结果按文件缓存；返回时必须深拷贝，因为
        # save_merged 等调用方会就地修改读到的文档。
        self._yaml_cache: dict[
            str, tuple[tuple[int, int], dict]
        ] = {}

    @staticmethod
    def _compute_dev_mode() -> bool:
        """计算开发模式：环境变量强制 > .git 探测"""
        env = os.environ.get("LVJIANG_DEV_MODE", "").strip().lower()
        if env in ("1", "true", "yes"):
            return True
        if env in ("0", "false", "no"):
            return False
        return (constants.PROJECT_ROOT / ".git").exists()

    # ─── 层根目录与模式 ─────────────────────────────────

    @property
    def system_dir(self) -> Path:
        if self._system_dir is not None:
            return self._system_dir
        return get_config_system_dir(self.config_dir)

    @property
    def local_dir(self) -> Path:
        if self._local_dir is not None:
            return self._local_dir
        return get_config_local_dir(self.config_dir)

    def is_dev_mode(self) -> bool:
        """开发模式（写 system）or 用户模式（写 local）

        构造时已计算并缓存，此处直接返回。
        """
        return self._dev_mode

    def write_dir(self, rel_dir: str = "") -> Path:
        """当前模式的可写目录（确保存在），编辑器默认目录等场景用"""
        root = self.system_dir if self.is_dev_mode() else self.local_dir
        target = root / rel_dir if rel_dir else root
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ─── 失效通知 ────────────────────────────────────────

    def clear_read_cache(self) -> None:
        """外部完整配置快照应用后使读取缓存失效，不改变层优先级或监听器。"""
        self._yaml_cache.clear()

    def reset_edit_baselines(self) -> None:
        """显式刷新编辑上下文时放弃旧读盘基线，不用于后台安装通知。"""
        self._read_baselines.clear()

    def add_change_listener(self, cb: Callable[[str], None]):
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_change_listener(self, cb: Callable[[str], None]):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def add_batch_change_listener(self, cb: Callable[[tuple[str, ...]], None]):
        if cb not in self._batch_listeners:
            self._batch_listeners.append(cb)

    @classmethod
    def for_single_directory(cls, directory: Path | str) -> ConfigResolver:
        """独立模型/规则目录：单层直写，自己的锁，不参与客户端修订索引。"""
        root = Path(directory)
        return cls(system_dir=root, local_dir=root, dev_mode=True,
                   config_dir=root, track_content=False)

    def _notify(self, rel_path: str, *, strict: bool = False):
        self.notify_changes((rel_path,), strict=strict)

    def notify_changes(self, paths: tuple[str, ...], *, strict: bool = False, refresh_content: bool = True):
        # 批次内读取仍需立即看到刚写入的文件；索引与监听通知在批次结束时合并。
        batches = _write_batches.get()
        batch = batches.get(id(self)) if batches is not None else None
        if batch is not None:
            batch.update(dict.fromkeys(paths))
            for rel_path in paths:
                for root in (self.system_dir, self.local_dir):
                    self._yaml_cache.pop(str(root / rel_path), None)
            return
        if paths and self.track_content and refresh_content:
            from .content import content_changed
            content_changed(self)
        # 写入路径主动失效，补上“同一 mtime tick 内写入了
        # 同尺寸内容”这个 stat 印记无法区分的极端情况。
        for rel_path in paths:
            for root in (self.system_dir, self.local_dir):
                self._yaml_cache.pop(str(root / rel_path), None)
        callbacks: list[tuple[Callable[..., None], Any]] = [(cb, paths) for cb in self._batch_listeners]
        callbacks.extend((cb, path) for path in paths for cb in self._listeners)
        for cb, argument in callbacks:
            try:
                cb(argument)
            except Exception as e:  # noqa: BLE001 监听器异常不阻断写入方
                if strict:
                    raise
                logger.warning(f"配置变更监听器异常: {e}")

    @contextmanager
    def batch_changes(self):
        """一轮多文件保存只维护一次索引；部分写入后失败也必须通知失效。"""
        from .content import configuration_lock
        batches = _write_batches.get() or {}
        if id(self) in batches:
            yield
            return
        with configuration_lock(self.config_dir):
            changed: dict[str, None] = {}
            token = _write_batches.set({**batches, id(self): changed})
            try:
                yield
            finally:
                _write_batches.reset(token)
                if changed:
                    self.notify_changes(tuple(changed))

    # ─── 实体文件（整文件影子）───────────────────────────

    def resolve_read(self, rel_path: str) -> Path | None:
        """实体读解析：local 影子 → system

        local 恒为最高优先级——用户自己改过的东西，任何在线下发都不该盖掉。
        """
        self._remember_baseline(rel_path)
        local = self.local_dir / rel_path
        if local.exists():
            return local
        system = self.system_dir / rel_path
        return system if system.exists() else None

    def _remember_baseline(self, rel_path: str) -> None:
        spec = versioning.spec_for(rel_path)
        if self.track_content and spec is not None and spec.has_content_version and rel_path not in self._read_baselines:
            from .conflicts import file_baseline
            self._read_baselines[rel_path] = file_baseline(self, rel_path)

    def describe_entity(self, rel_path: str) -> EntityOrigin:
        """这个实体实际来自哪一层、版本多少——供编辑器把来源显示给人看。

        日志只在文件第一次被顶替时留一条，人不会盯着日志编辑配置；编辑器
        里直接标出「来源 / 版本号」，才是开发者与用户都能随时"意识到"自己
        正在看哪一份的办法。
        """
        path = self.resolve_read(rel_path)
        if path is None:
            return EntityOrigin(layer="", version=None)
        if path == self.local_dir / rel_path:
            layer = LAYER_LOCAL
        else:
            layer = LAYER_SYSTEM
        return EntityOrigin(layer=layer, version=versioning.read_version(path))

    def list_entity_origins(self, rel_path: str) -> tuple[EntityOrigin, ...]:
        """列出实体在各配置层实际存在的副本，按读取优先级排序。

        与 :meth:`describe_entity` 的区别是：后者只回答当前生效的是谁；本方法
        同时保留被遮蔽的 system 副本，供界面解释完整版本关系。
        """
        candidates = (
            (LAYER_LOCAL, self.local_dir / rel_path),
            (LAYER_SYSTEM, self.system_dir / rel_path),
        )
        return tuple(
            EntityOrigin(layer=layer, version=versioning.read_version(path))
            for layer, path in candidates
            if path.is_file()
        )

    def enumerate_entities(self, rel_dir: str, pattern: str, *,
                           include_internal: bool = False) -> list[str]:
        """枚举实体文件名：system ∪ local 并集，跳过 _ 前缀

        ``include_internal=True`` 时保留 ``_`` 前缀文件。发现层要跳过它们
        （编辑器临时运行、录制产物不该注册成脚本），但文件树是「磁盘上有
        什么就显示什么」，藏起来反而让人找不到自己刚录的东西。

        """
        names: set[str] = set()
        for root in (self.system_dir, self.local_dir):
            base = root / rel_dir
            if not base.is_dir():
                continue
            for p in base.glob(pattern):
                if p.is_file() and (include_internal
                                    or not p.name.startswith("_")):
                    names.add(p.name)
        return sorted(names)

    def enumerate_entity_tree(self, rel_dir: str, pattern: str, *,
                              include_internal: bool = False) -> list[str]:
        """递归枚举实体，返回**相对 rel_dir 的 posix 路径**（含子目录）。

        与 :meth:`enumerate_entities` 的区别只在递归与返回形态：后者只扫一层
        且返回裸文件名，够用于「某个目录下有哪些场景/布局」，但文件树要展示
        ``subcall/`` ``batch/`` 这些子目录，需要带路径的全集。

        合并、遮蔽、``_`` 前缀跳过的规则与单层版本完全一致——刻意复用
        它逐目录调用，避免两套枚举对「哪些文件算数」给出不同答案。
        """
        results: list[str] = []
        seen_dirs: set[str] = set()

        def walk(sub: str) -> None:
            if sub in seen_dirs:
                return
            seen_dirs.add(sub)
            full = f"{rel_dir}/{sub}" if sub else rel_dir
            for name in self.enumerate_entities(
                    full, pattern, include_internal=include_internal):
                results.append(f"{sub}/{name}" if sub else name)
            # 子目录同样取并集：local 可以新建 system 没有的目录
            child_names: set[str] = set()
            for root in (self.system_dir, self.local_dir):
                base = root / full
                if not base.is_dir():
                    continue
                for d in base.iterdir():
                    if not d.is_dir() or d.name.startswith("."):
                        continue
                    if include_internal or not d.name.startswith("_"):
                        child_names.add(d.name)
            for name in sorted(child_names):
                walk(f"{sub}/{name}" if sub else name)

        walk("")
        return sorted(results)

    def _layer_root(self, layer: str | None, default: Path) -> Path:
        """把显式指定的层解析成根目录。

        只有开发模式可以指定层（system 或 local）；用户模式只允许 local
        ——它本来就只能写 local，显式说一遍不是开口子。
        """
        if layer is None:
            return default
        if layer == LAYER_LOCAL:
            return self.local_dir
        if layer == LAYER_SYSTEM and self.is_dev_mode():
            return self.system_dir
        raise PermissionError(f"当前模式不允许写入 {layer} 层")

    def local_only_layer(self, rel_path: str) -> str | None:
        """开发模式下，已有且只存在于 local 的实体继续写回 local。"""
        if (self.is_dev_mode() and (self.local_dir / rel_path).is_file()
                and not (self.system_dir / rel_path).exists()):
            return LAYER_LOCAL
        return None

    @_configuration_write
    def write_entity(self, rel_path: str, data: str | bytes, *,
                     force: bool = False,
                     layer: str | None = None) -> Path:
        """按模式写实体文件（开发→system，用户→local 影子）

        ``layer`` 显式指定目标层：开发者可以把自己的私有规则组之类放进
        local 而不写进随包的 system（见 :meth:`_layer_root`）。

        实体实际修改时自动递增目标文件 content_version；新文件从 1 起步。
        修订号不参与来源仲裁，local 影子恒为最高优先级。
        ``force`` 关掉下面的空操作检测，用于「用户明确要求生成 local 影子」
        （脚本编辑器的「复制到本地以修改」）。这种场景内容本来就与系统一致，
        不强制的话一个字节都不会落盘，用户点完发现还是不能编辑。
        """
        root = self._layer_root(
            layer, self.system_dir if self.is_dev_mode() else self.local_dir)
        target = root / rel_path
        spec = versioning.spec_for(rel_path)
        if isinstance(data, str) and spec is not None and spec.has_content_version:
            # 先对齐当前修订号检测空操作，再在真正修改时递增。
            current = self.resolve_read(rel_path)
            previous = target if target.exists() else current
            version = (versioning.read_version(previous) if previous is not None else None) or 0
            data = versioning.with_version(data, Path(rel_path).suffix, version or 1)
        if (not force or target.exists()) and self._write_is_noop(rel_path, target, data):
            # 内容与「不写的话会读到的那一份」完全一致 —— 不落盘。
            #
            # 用户模式下这一步尤其要紧：照写会给一个其实没改过的文件生成
            # local 影子，而实体文件是**整文件影子**（local 有就完全顶掉
            # system，不合并），于是这个文件从此收不到任何系统更新
            # 与在线下发。场景编辑器里"什么都没改、随手点一下保存"就会
            # 把整个布局的场景全冻住，代价与操作的随意程度完全不匹配。
            return target

        if isinstance(data, str) and spec is not None and spec.has_content_version:
            next_version = (versioning.read_version(target) or 0) + 1 if target.exists() else 1
            data = versioning.with_version(data, Path(rel_path).suffix, next_version)

        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            atomic_write_bytes(target, data, prefix=".config_")
        else:
            atomic_write_text(target, data, prefix=".config_")
        self._notify(rel_path)
        return target

    def _write_is_noop(self, rel_path: str, target: Path,
                       data: str | bytes) -> bool:
        """写下去会不会改变「读到的内容」——不会就没必要落盘。

        分两种情形，都要求结果与写之后严格一致，宁可多写不可错判：

        1. 目标文件已存在：与它比对，相同即纯粹的空操作。
        2. 目标文件不存在：只有**用户模式**才比较——此时目标是 local 影子，
           跳过写入后 resolve_read 仍会解析到当前那一层（system），
           内容一致则读到的东西不变。开发模式不能这样判：目标是 system，
           而 resolve_read 可能解析到 local 影子，拿它比对会把"作者要新建
           系统文件"这件事误判成空操作。

        文本一律用 ``read_text`` 比对，**不能逐字节比**：``write_text`` 在
        Windows 上会把 ``\\n`` 换成 ``\\r\\n`` 落盘，拿入参的 ``\\n`` 去和盘上的
        ``\\r\\n`` 比永远不相等，空操作检测会整个失效——本函数要防的"随手点
        一下保存就把整个布局冻成 local 影子"在 Windows 上照旧发生。
        ``read_text`` 的通用换行会把两侧都归一到 ``\\n``，与 ``write_text``
        的转换恰好对称。二进制（参考图等）不涉及换行转换，仍逐字节比。
        """
        def _same(path: Path) -> bool:
            try:
                if isinstance(data, bytes):
                    return path.read_bytes() == data
                previous = path.read_text(encoding="utf-8")
                spec = versioning.spec_for(rel_path)
                if spec is not None and spec.has_content_version:
                    if path.suffix == ".json":
                        import json
                        before, after = json.loads(previous), json.loads(data)
                        if isinstance(before, dict) and isinstance(after, dict):
                            before.pop("content_version", None)
                            after.pop("content_version", None)
                            return before == after
                    def payload(text):
                        return "\n".join(line for line in text.splitlines()
                                         if not line.startswith("content_version:")).strip()
                    return payload(previous) == payload(data)
                return previous == data
            except (OSError, UnicodeDecodeError):
                return False

        if target.exists():
            return _same(target)
        if self.is_dev_mode():
            return False
        current = self.resolve_read(rel_path)
        return _same(current) if current is not None else False

    def is_system_entity(self, rel_path: str) -> bool:
        """该实体是否属于系统内容（供 UI 判断能否删除/重命名）

        远程下发的实体同样算系统内容——它也是作者提供的、用户没写过的东西，
        「系统内容不允许用户删除」这条约定对它一样成立（见本模块「删除：
        默认禁止」相关文档）。否则远程新增的调律规则会变成用户可删，
        删掉之后下次同步又回来，行为莫名其妙。
        """
        return (self.system_dir / rel_path).exists()

    def ensure_entity_deletable(self, rel_path: str) -> None:
        """在删除或重命名实体前校验当前身份是否有权限。

        重命名通常是“先写新文件，再删旧文件”。调用方必须在写入新文件前
        执行本检查，避免删除被拒绝后留下半成品。
        """
        if not self.is_dev_mode() and self.is_system_entity(rel_path):
            raise SystemContentProtected(
                f"{rel_path} 由系统配置提供，用户模式下不可删除或重命名；"
                f"如需停用请使用启用开关/展示勾选")

    @_configuration_write
    def delete_entity(self, rel_path: str, *, layer: str | None = None):
        """按模式删实体：开发→直删 system；用户→只删自己的 local 影子

        用户模式下若 system 存在同名文件，抛 :class:`SystemContentProtected`：
        系统内容不允许用户删除。想让它不出现请走激活机制（调律规则的启用
        开关、脚本的展示勾选），需要真删就切到开发模式。

        ``layer`` 显式指定要删哪一层的文件（规则同 :meth:`write_entity`）。
        """
        if layer is not None:
            target = self._layer_root(layer, self.system_dir) / rel_path
            if not self.is_dev_mode():
                self.ensure_entity_deletable(rel_path)
            if target.exists():
                target.unlink()
        elif self.is_dev_mode():
            target = self.system_dir / rel_path
            if target.exists():
                target.unlink()
        else:
            self.ensure_entity_deletable(rel_path)
            local = self.local_dir / rel_path
            if local.exists():
                local.unlink()
        self._notify(rel_path)

    @_configuration_write
    def revert_entity_to_system(self, rel_path: str) -> bool:
        """撤掉 local 影子，让实体回到系统那一份；返回是否真删了影子

        与 :meth:`delete_entity` 是两回事：这不是删除实体，是**放弃自己的
        覆盖**。所以只在系统有同名内容时才允许——没有的话删掉 local 就等于
        把实体整个抹掉，那条路归 delete_entity 管（用户模式下会被
        SystemContentProtected 拦下）。

        没有这个操作，「复制到本地以修改」就是一条单行道：用户复制完发现
        改坏了，既删不掉（系统内容受保护）也回不去。
        """
        if not self.is_system_entity(rel_path):
            raise SystemContentProtected(
                f"{rel_path} 没有系统版本，无法还原")
        local = self.local_dir / rel_path
        if not local.is_file():
            return False
        local.unlink()
        self._notify(rel_path)
        return True

    # ─── 聚合键值文件（键级 diff 深合并）──────────────────

    def _load_yaml(self, path: Path) -> dict:
        for root in (self.system_dir, self.local_dir):
            if path.is_relative_to(root):
                self._remember_baseline(path.relative_to(root).as_posix())
                break
        try:
            stat = path.stat()
        except OSError:
            return {}
        stamp = (stat.st_mtime_ns, stat.st_size)
        cache_key = str(path)
        cached = self._yaml_cache.get(cache_key)
        if cached is not None and cached[0] == stamp:
            return deepcopy(cached[1])
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            doc = data if isinstance(data, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.error(f"配置解析失败 {path}: {e}")
            doc = {}
        self._yaml_cache[cache_key] = (stamp, doc)
        return deepcopy(doc)

    def load_system(self, rel_path: str) -> dict:
        """只读 system 层文档（不合并 local）

        UI 判断某个条目是不是系统内容时用：系统内容用户不允许删除，
        对应的删除按钮应置灰并给出停用替代方案。
        """
        return self._load_yaml(self.system_dir / rel_path)

    def load_local(self, rel_path: str) -> dict:
        """只读 local 层原始文档，供需要分层 schema 迁移的聚合配置使用。"""
        return self._load_yaml(self.local_dir / rel_path)

    def load_merged(self, rel_path: str) -> dict:
        """聚合读：system 有效基底 ← local diff 深合并。"""
        base = self._load_yaml(self.system_dir / rel_path)
        overlay_path = self.local_dir / rel_path
        if not overlay_path.exists():
            return base
        registry = REGISTRY_LIST_PATHS.get(rel_path, ())
        return merge_doc(base, self._load_yaml(overlay_path), registry)

    @_configuration_write
    def save_merged(self, rel_path: str, full_doc: dict, *,
                    base_doc: dict | None = None,
                    registry: tuple[str, ...] | None = None):
        """聚合写：开发→全量写 system；用户→逆向求 diff 写 local
        （diff 为空则删 local 覆盖文件）

        **入参必须是完整文档**，不是「本次要改的那几个键」。
        compute_diff 把「system 有而入参没有」的键判成用户删除，只传部分键会
        把其余顶层键写进 __deleted__ 永久抹掉；开发模式更直接——全量写 system
        会把没传的键从系统配置里删掉。正确写法是先 load_merged 取回完整文档，
        改需要改的键再整个传回来。

        删除受白名单约束：未在 DELETABLE_PATHS 声明的路径，即使入参里少了某个
        键也不会被删掉，只记 warning。这既挡住「调用方只传部分文档」的误删，
        也贯彻「不允许用户删除系统内容」的产品约定。
        """
        def _dump(doc: dict) -> str:
            return yaml.dump(doc, allow_unicode=True,
                             default_flow_style=False, sort_keys=False)

        if self.is_dev_mode():
            self.write_entity(rel_path, _dump(full_doc))
            return
        else:
            base = (deepcopy(base_doc) if base_doc is not None
                    else self._load_yaml(
                        self.system_dir / rel_path))
            diff = compute_diff(
                base, full_doc, (registry if registry is not None
                                 else REGISTRY_LIST_PATHS.get(rel_path, ())),
                deletable=DELETABLE_PATHS.get(rel_path, ()),
                protected=PROTECTED_LIST_PATHS.get(rel_path, {}))
            overlay_path = self.local_dir / rel_path
            if diff:
                text = _dump(diff)
                if versioning.spec_for(rel_path) is not None:
                    self.write_entity(rel_path, text)
                    return
                if (overlay_path.exists()
                        and overlay_path.read_text(encoding="utf-8") == text):
                    return
                overlay_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(overlay_path, text, prefix=".config_")
            elif overlay_path.exists():
                overlay_path.unlink()
            else:
                return  # 本来就没有覆盖层，也不需要建
        self._notify(rel_path)


# ─── 模块级单例 ──────────────────────────────────────────

_resolver: ConfigResolver | None = None


def get_resolver() -> ConfigResolver:
    from .runtime_snapshot import current_snapshot
    snapshot = current_snapshot()
    if snapshot is not None:
        return snapshot.resolver
    return get_root_resolver()


def get_root_resolver() -> ConfigResolver:
    """仅用于进程级单例初始化与监听注册；任务读取必须使用 get_resolver。"""
    global _resolver
    if _resolver is None:
        _resolver = ConfigResolver()
    return _resolver


# ─── 便捷函数：app.yaml ────────────────────────────────

_APP_CONFIG_REL = "app.yaml"


def load_app_config() -> dict:
    """读取 app.yaml 的 system←local 合并视图（解析失败返回空 dict）"""
    try:
        return get_resolver().load_merged(_APP_CONFIG_REL)
    except Exception as e:  # noqa: BLE001 配置缺失/损坏不应阻断启动
        logger.error(f"加载 app.yaml 失败: {e}")
        return {}


def save_app_config(
    input_sim: dict,
    delay_params: dict,
    envs: list | None = None,
    android_apps: dict | None = None,
) -> None:
    """保存输入模拟 + 延迟参数 + 环境列表到 app.yaml

    读-改-写全量：先取合并视图再改这几个键。save_merged 的入参语义是**完整
    文档**，只传自己关心的键会让 compute_diff 把其余键判成「用户删除」写进
    __deleted__，系统新增的顶层键一保存就永久消失。
    """
    data = load_app_config()
    data["input_simulation"] = input_sim
    data["delay_params"] = delay_params
    if envs is not None:
        data["envs"] = envs
    if android_apps is not None:
        data["apps"] = android_apps
        # 保存一次即完成旧键迁移。
        data.pop("android_apps", None)
    get_resolver().save_merged(_APP_CONFIG_REL, data)


def save_app_config_node(key: str, value: object) -> None:
    """只改 app.yaml 的一个顶层键，其余键原样保留。

    同样是读-改-写全量：save_merged 的入参语义是**完整文档**，直接传
    ``{key: value}`` 会把其余顶层键判成用户删除。
    """
    data = load_app_config()
    data[key] = value
    get_resolver().save_merged(_APP_CONFIG_REL, data)


def load_available_envs() -> list[tuple[str, str]]:
    """从 app.yaml 读取可用环境列表，返回 [(key, display_name), ...]"""
    app = load_app_config()
    envs = app.get("envs", [])
    if not isinstance(envs, list):
        return [("desktop", "桌面")]
    result: list[tuple[str, str]] = []
    for item in envs:
        if isinstance(item, dict) and "key" in item:
            key = str(item["key"])
            name = str(item.get("name", key))
            result.append((key, name))
    return result or [("desktop", "桌面")]
