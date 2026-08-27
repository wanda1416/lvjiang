"""配置解析器 —— system/remote/local 多层配置的唯一读写咽喉

目录职责：
- config/system  出厂默认（进 git，用户模式下只读）
- config/remote  在线下发层（不进 git，见 core.config.remote）：只对实体
  文件生效，且只在 content_version 严格新于 system 时才顶替 system
- config/local   用户覆盖层：影子文件 + 键级 diff + 墓碑（目录结构镜像 system）
- config/session 纯运行态（不经本模块，见 core.config.session.SessionStore）

读语义（两模式一致）：local > remote（版本更新才生效）> system。
写语义（按模式路由）：开发模式写 system，用户模式写 local；
**任何模式都不写 remote**——那层是下发下来的，本地改动一律落 local。

模式判定：LVJIANG_DEV_MODE 环境变量（1/0）强制覆盖 > PROJECT_ROOT/.git 探测。

两档合并语义：
- 实体文件（一物一文件：scenes/*.yaml、workflows/*.wf、
  layouts/{name}/{scene}.json、yysls/tuning_rules/*.yaml、
  references/**/*.png）→ 整文件影子 + 墓碑
  （local/<rel>.deleted 空标记文件）
- 聚合键值文件（scenes.yaml、layouts.yaml、
  yysls/game_config.yaml、yysls/tune_config.yaml）→ 键级 diff 深合并；
  dict 递归、列表与标量整键替换；
  每层 dict 支持 "__deleted__": [key, ...] 删除键

列表的两种语义：
- 枚举设定（quality_thresholds.武器、mouse_move_duration 等）→ 整键替换，
  用户就是要覆盖出厂值
- 注册表（REGISTRY_LIST_PATHS 声明的路径）→
  local 只存 __added__ / __removed__ / __order__ 增量。整键替换会让 local
  冻住整张表，出厂后续新增的条目永远进不到合并视图；用户除非删掉自己的
  local 否则再也看不到更新。存量 local 里的普通列表仍按整键替换处理，
  用户下次保存时由 compute_diff 自动转成增量形式。

以上举例中的 yysls/* 路径仅用于说明两档合并语义长什么样——本模块（core）
不认识任何插件领域词汇。这些路径的登记表/受保护列表声明由插件自己经
register_registry_list_paths / register_protected_list_paths 注册，
不写进本文件的常量表，见两个函数的定义处。
"""
from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml
from loguru import logger

from ... import constants
from . import versioning

# 层根目录：ConfigResolver 内部持有，外部经 ConfigResolver API 访问
SYSTEM_CONFIG_DIR = constants.CONFIG_DIR / "system"
LOCAL_CONFIG_DIR = constants.CONFIG_DIR / "local"
REMOTE_CONFIG_DIR = constants.CONFIG_DIR / "remote"
#: 在线配置的**暂存**层：本次会话下载的内容先落在这里，下次启动才提升为
#: 生效层（见 core.config.remote.promote_pending / stage_dir_for）。
#: 直接写生效层会让本次会话就读到新配置——工作流每次启动都会重新
#: load_layout，而场景注册表只在启动时加载一次，于是出现半新半旧。
REMOTE_STAGE_DIR = constants.CONFIG_DIR / "remote.staging"

# 聚合 diff 中的删除键标记
DELETED_KEY = "__deleted__"
# 墓碑文件后缀
TOMBSTONE_SUFFIX = ".deleted"

#: 实体来源层标识（见 ConfigResolver.describe_entity）。展示用的中文标签留给
#: UI 层做 i18n，core 只给稳定的 key。
LAYER_LOCAL = "local"
LAYER_REMOTE = "remote"
LAYER_SYSTEM = "system"


@dataclass(frozen=True)
class EntityOrigin:
    """某个实体文件实际生效的来源层与内容版本。

    ``layer`` 为空串表示该实体不存在（或被墓碑遮住）；``version`` 为 None
    表示这一层的文件没有 content_version（未参与在线下发的类型都是如此）。
    """

    layer: str
    version: int | None


class SystemContentProtected(PermissionError):
    """试图在用户模式下删除 system 层内容

    出厂内容属于开发者提供的初始版本，用户模式（拿不到 system 编辑权限）
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
#: 用户就是要覆盖。但注册表不同：local 若存下完整列表，出厂新增的条目就永远
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
    "scenes.yaml": ("layout_scenes.*",),
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
#: 出厂配置是开发者提供的初始内容，用户该做的是改值、复制、另存为、新建，
#: 而不是删掉它。想停用某项应走激活机制（调律规则的 tuning_rules 开关、
#: 脚本的 exposed 暴露列表），不是删除定义本身。允许删除的场景必须先在这里
#: 声明，未声明的删除会被拦下并记 warning。
#:
#: 键为聚合文件相对路径，值为允许删除的**父路径**模式（点分，``*`` 匹配单层
#: 任意键）；``""`` 表示顶层。删除 layouts.<名字> 就写 "layouts"。
DELETABLE_PATHS: dict[str, tuple[str, ...]] = {}

#: 受保护的**列表**：出厂条目不允许被移除，但可以改值、可以新增。
#:
#: 列表走整键替换，绕开了 __deleted__ 那条保护——用户存一份少了几项的列表
#: 就把出厂条目抹掉了。这里按「条目身份字段」比对：出厂条目缺失即补回，
#: 用户新增的条目和对出厂条目的改值都原样保留。
#:
#: 形如 {文件: {点分路径: 身份字段}}；身份字段为 None 表示条目本身即身份
#: （纯标量列表）。
#:
#: **core.config 不认识任何插件领域词汇**：core 目前没有自己的受保护列表，
#: 保持空表。插件私有配置文件（如燕云 `yysls/game_config.yaml` 的
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
    """补回 desired 里缺失的出厂条目，返回 (结果, 被补回的身份列表)

    补回位置取出厂列表中的原下标（越界则追加），这样既保留用户对留下条目
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

    __order__ 里没提到的条目（通常是出厂后来新增的）排在末尾，保持 system 里的
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

    protected 声明受保护的列表路径及其条目身份字段：出厂条目缺失即补回，
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
                f"出厂内容不允许删除——停用请走激活机制，"
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
                    f"配置删除被拦下：{path} 下的出厂条目 {restored} 不允许移除，"
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
    """system/remote/local 三层配置解析器

    不带参构造时各层根目录动态取自 constants（monkeypatch 友好）；
    测试可显式传入 tmp_path 与 dev_mode 构造隔离实例。

    remote 层只对**实体文件**生效，且只在版本更新时顶替 system，
    见 :meth:`resolve_read`。聚合键值文件（app.yaml 等）不走 remote。
    """

    def __init__(
        self,
        system_dir: Path | str | None = None,
        local_dir: Path | str | None = None,
        dev_mode: bool | None = None,
        remote_dir: Path | str | None = None,
    ):
        self._system_dir = Path(system_dir) if system_dir else None
        self._local_dir = Path(local_dir) if local_dir else None
        self._remote_dir = Path(remote_dir) if remote_dir else None
        self._dev_mode = dev_mode if dev_mode is not None else self._compute_dev_mode()
        self._listeners: list[Callable[[str], None]] = []
        #: 已记过「远端顶替出厂」日志的 (rel_path, 远端版本)，见 _log_supersede
        self._logged_supersedes: set[tuple[str, int]] = set()

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
        return SYSTEM_CONFIG_DIR

    @property
    def local_dir(self) -> Path:
        if self._local_dir is not None:
            return self._local_dir
        return LOCAL_CONFIG_DIR

    @property
    def remote_dir(self) -> Path:
        if self._remote_dir is not None:
            return self._remote_dir
        return REMOTE_CONFIG_DIR

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

    def add_change_listener(self, cb: Callable[[str], None]):
        if cb not in self._listeners:
            self._listeners.append(cb)

    def remove_change_listener(self, cb: Callable[[str], None]):
        if cb in self._listeners:
            self._listeners.remove(cb)

    def _notify(self, rel_path: str):
        for cb in list(self._listeners):
            try:
                cb(rel_path)
            except Exception as e:  # noqa: BLE001 监听器异常不阻断写入方
                logger.warning(f"配置变更监听器异常: {e}")

    # ─── 实体文件（整文件影子 + 墓碑）──────────────────────

    def _tombstone(self, rel_path: str) -> Path:
        return self.local_dir / (rel_path + TOMBSTONE_SUFFIX)

    def remote_supersedes(self, rel_path: str) -> bool:
        """remote 层该文件是否该顶替 system —— 版本更新才算数。

        **不是「remote 优先」**：用户升了 App，system 带来 v5 的场景坐标，
        而远端还停在给旧版本热修的 v3，无脑覆盖会把配置静默回退成旧的，
        没有报错，只表现为"识别又坏了"。所以恒要求 remote 严格新于 system。

        三道闸门，任一不过就用 system：
        1. 该路径参与在线下发（versioning 注册表里声明过）
        2. system 侧版本号可读——**缺失即拒绝**（fail-safe，见 versioning
           模块文档）；system 没有该文件时只有声明了 allow_remote_new 的
           目录才接受远端新增
        3. remote 侧版本号可读且严格大于 system
        """
        spec = versioning.spec_for(rel_path)
        if spec is None:
            return False
        remote = self.remote_dir / rel_path
        if not remote.exists():
            return False
        remote_version = versioning.read_version(remote)
        if remote_version is None:
            logger.warning(f"remote 配置缺 content_version，已忽略: {rel_path}")
            return False

        system = self.system_dir / rel_path
        if not system.exists():
            # 出厂没有这个文件 = 远端新增。只有明确允许的目录才接受，
            # 否则远端凭空多出的场景/布局是死的（没在 scenes.yaml 登记）。
            if not spec.allow_remote_new:
                logger.warning(
                    f"remote 下发了出厂不存在的文件，该目录未允许新增，已忽略: "
                    f"{rel_path}")
                return False
            self._log_supersede(rel_path, None, remote_version)
            return True

        system_version = versioning.read_version(system)
        if system_version is None:
            logger.warning(
                f"出厂配置缺 content_version，拒绝 remote 替换: {rel_path}")
            return False
        if remote_version <= system_version:
            return False
        self._log_supersede(rel_path, system_version, remote_version)
        return True

    def _log_supersede(self, rel_path: str, system_version: int | None,
                       remote_version: int) -> None:
        """远端顶替出厂时记一条，同一文件同一版本只记一次。

        没有这条日志，远端配置生效是**完全静默**的：开发者本地跑出来的行为
        和用户不一样却毫不知情，用户报"识别坏了"时根本复现不出来——而这
        恰恰是在线下发最需要被排查的一类问题。

        去重是必须的：resolve_read 是热路径，enumerate_entities 还会对整个
        目录逐个调用，不去重会刷屏到没人看日志。
        """
        token = (rel_path, remote_version)
        if token in self._logged_supersedes:
            return
        self._logged_supersedes.add(token)
        origin = "出厂无此文件" if system_version is None else f"出厂 v{system_version}"
        logger.info(f"[在线配置] 生效：{rel_path}（{origin} → 远端 v{remote_version}）")

    def resolve_read(self, rel_path: str) -> Path | None:
        """实体读解析：local 影子 → remote（版本更新才生效）→ system

        墓碑返回 None。local 恒为最高优先级——用户自己改过的东西，
        任何在线下发都不该盖掉。
        """
        if self._tombstone(rel_path).exists():
            return None
        local = self.local_dir / rel_path
        if local.exists():
            return local
        if self.remote_supersedes(rel_path):
            return self.remote_dir / rel_path
        system = self.system_dir / rel_path
        return system if system.exists() else None

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
        elif path == self.remote_dir / rel_path:
            layer = LAYER_REMOTE
        else:
            layer = LAYER_SYSTEM
        return EntityOrigin(layer=layer, version=versioning.read_version(path))

    def enumerate_entities(self, rel_dir: str, pattern: str) -> list[str]:
        """枚举实体文件名：system ∪ local ∪ remote 并集，剔除墓碑，跳过 _ 前缀

        remote 侧只有通过 :meth:`remote_supersedes` 闸门的文件才计入——
        否则远端一份版本更旧、或落在不允许新增的目录里的文件，会在编辑器
        列表里冒出来却永远读不到（resolve_read 会解析回 system），
        列表和实际内容对不上比少一个条目更难查。

        Returns:
            排序后的文件名列表（不含目录），local 遮盖同名天然成立。
        """
        names: set[str] = set()
        for root in (self.system_dir, self.local_dir):
            base = root / rel_dir
            if not base.is_dir():
                continue
            for p in base.glob(pattern):
                if p.is_file() and not p.name.startswith("_"):
                    names.add(p.name)
        remote_base = self.remote_dir / rel_dir if rel_dir else self.remote_dir
        if remote_base.is_dir():
            for p in remote_base.glob(pattern):
                if not p.is_file() or p.name.startswith("_"):
                    continue
                rel = f"{rel_dir}/{p.name}" if rel_dir else p.name
                if self.remote_supersedes(rel):
                    names.add(p.name)
        alive = [n for n in sorted(names)
                 if not self._tombstone(f"{rel_dir}/{n}" if rel_dir else n).exists()]
        return alive

    def write_entity(self, rel_path: str, data: str | bytes) -> Path:
        """按模式写实体文件（开发→system，用户→local 影子并清同名墓碑）

        开发模式写 system 且该路径参与在线下发（见 core.config.versioning）
        时，落盘前自动把 content_version +1——这三类文件全是 UI 编辑器写
        出来的，手写的版本号存一次就没了，靠人记着改必然漏。内容没变则
        保持原号，避免"打开编辑器又原样关掉"也推高版本。

        用户模式写 local 不动版本号：版本号是 system 与 remote 之间的仲裁
        依据，local 影子恒为最高优先级，不参与比较。

        远端正顶替这个文件时，新版本号以**远端那份**为下限——开发者在编辑器
        里看到的就是远端内容，拿出厂版本做基线会写出一个与线上同号但不同
        内容的文件，见 versioning.next_version_for_write 的 floor_version。
        """
        root = self.system_dir if self.is_dev_mode() else self.local_dir
        target = root / rel_path
        if isinstance(data, str) and self.is_dev_mode() \
                and versioning.spec_for(rel_path) is not None:
            floor = None
            if self.remote_supersedes(rel_path):
                floor = versioning.read_version(self.remote_dir / rel_path)
            data = versioning.next_version_for_write(
                rel_path, data, target, floor_version=floor)

        tomb = self._tombstone(rel_path)
        if not tomb.exists() and self._write_is_noop(rel_path, target, data):
            # 内容与「不写的话会读到的那一份」完全一致 —— 不落盘。
            #
            # 用户模式下这一步尤其要紧：照写会给一个其实没改过的文件生成
            # local 影子，而实体文件是**整文件影子**（local 有就完全顶掉
            # system/remote，不合并），于是这个文件从此收不到任何出厂更新
            # 与在线下发。场景编辑器里"什么都没改、随手点一下保存"就会
            # 把整个布局的场景全冻住，代价与操作的随意程度完全不匹配。
            #
            # 有墓碑时不能走这条：那说明该实体当前被隐藏着，必须真写一次
            # 才能连带把墓碑清掉。
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            target.write_bytes(data)
        else:
            target.write_text(data, encoding="utf-8")
        if tomb.exists():
            tomb.unlink()
        self._notify(rel_path)
        return target

    def _write_is_noop(self, rel_path: str, target: Path,
                       data: str | bytes) -> bool:
        """写下去会不会改变「读到的内容」——不会就没必要落盘。

        分两种情形，都要求结果与写之后严格一致，宁可多写不可错判：

        1. 目标文件已存在：与它比对，相同即纯粹的空操作。
        2. 目标文件不存在：只有**用户模式**才比较——此时目标是 local 影子，
           跳过写入后 resolve_read 仍会解析到当前那一层（system 或 remote），
           内容一致则读到的东西不变。开发模式不能这样判：目标是 system，
           而 resolve_read 可能解析到 local 影子，拿它比对会把"作者要新建
           出厂文件"这件事误判成空操作。

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
                return path.read_text(encoding="utf-8") == data
            except (OSError, UnicodeDecodeError):
                return False

        if target.exists():
            return _same(target)
        if self.is_dev_mode():
            return False
        current = self.resolve_read(rel_path)
        return _same(current) if current is not None else False

    def is_system_entity(self, rel_path: str) -> bool:
        """该实体是否属于出厂内容（供 UI 判断能否删除/重命名）

        远端下发的实体同样算出厂内容——它也是作者提供的、用户没写过的东西，
        「出厂内容不允许用户删除」这条约定对它一样成立（见本模块「删除：
        默认禁止」相关文档）。否则远端新增的调律规则会变成用户可删，
        删掉之后下次同步又回来，行为莫名其妙。
        """
        return ((self.system_dir / rel_path).exists()
                or self.remote_supersedes(rel_path))

    def ensure_entity_deletable(self, rel_path: str) -> None:
        """在删除或重命名实体前校验当前身份是否有权限。

        重命名通常是“先写新文件，再删旧文件”。调用方必须在写入新文件前
        执行本检查，避免删除被拒绝后留下半成品。
        """
        if not self.is_dev_mode() and self.is_system_entity(rel_path):
            raise SystemContentProtected(
                f"{rel_path} 由出厂配置提供，用户模式下不可删除或重命名；"
                f"如需停用请使用启用开关/展示勾选")

    def delete_entity(self, rel_path: str):
        """按模式删实体：开发→直删 system；用户→只删自己的 local 影子

        用户模式下若 system 存在同名文件，抛 :class:`SystemContentProtected`：
        出厂内容不允许用户删除。想让它不出现请走激活机制（调律规则的启用
        开关、脚本的展示勾选），需要真删就切到开发模式。
        """
        if self.is_dev_mode():
            target = self.system_dir / rel_path
            if target.exists():
                target.unlink()
        else:
            self.ensure_entity_deletable(rel_path)
            local = self.local_dir / rel_path
            if local.exists():
                local.unlink()
        self._notify(rel_path)

    # ─── 聚合键值文件（键级 diff 深合并）──────────────────

    def _load_yaml(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:  # noqa: BLE001
            logger.error(f"配置解析失败 {path}: {e}")
            return {}

    def load_system(self, rel_path: str) -> dict:
        """只读 system 层文档（不合并 local）

        UI 判断某个条目是不是出厂内容时用：出厂内容用户不允许删除，
        对应的删除按钮应置灰并给出停用替代方案。
        """
        return self._load_yaml(self.system_dir / rel_path)

    def load_merged(self, rel_path: str) -> dict:
        """聚合读：system 文档 ← local diff 深合并"""
        base = self._load_yaml(self.system_dir / rel_path)
        overlay_path = self.local_dir / rel_path
        if not overlay_path.exists():
            return base
        registry = REGISTRY_LIST_PATHS.get(rel_path, ())
        return merge_doc(base, self._load_yaml(overlay_path), registry)

    def save_merged(self, rel_path: str, full_doc: dict):
        """聚合写：开发→全量写 system；用户→逆向求 diff 写 local
        （diff 为空则删 local 覆盖文件）

        **入参必须是完整文档**，不是「本次要改的那几个键」。
        compute_diff 把「system 有而入参没有」的键判成用户删除，只传部分键会
        把其余顶层键写进 __deleted__ 永久抹掉；开发模式更直接——全量写 system
        会把没传的键从出厂配置里删掉。正确写法是先 load_merged 取回完整文档，
        改需要改的键再整个传回来。

        删除受白名单约束：未在 DELETABLE_PATHS 声明的路径，即使入参里少了某个
        键也不会被删掉，只记 warning。这既挡住「调用方只传部分文档」的误删，
        也贯彻「不允许用户删除出厂内容」的产品约定。
        """
        def _dump(doc: dict) -> str:
            return yaml.dump(doc, allow_unicode=True,
                             default_flow_style=False, sort_keys=False)

        if self.is_dev_mode():
            target = self.system_dir / rel_path
            text = _dump(full_doc)
            # 内容没变就不落盘：反复重写只会刷 mtime，还会把整份文件卷进
            # git diff（布局那批 JSON 就这么产生过一次上千行的无意义改动）。
            if target.exists() and target.read_text(encoding="utf-8") == text:
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        else:
            base = self._load_yaml(self.system_dir / rel_path)
            diff = compute_diff(
                base, full_doc, REGISTRY_LIST_PATHS.get(rel_path, ()),
                deletable=DELETABLE_PATHS.get(rel_path, ()),
                protected=PROTECTED_LIST_PATHS.get(rel_path, {}))
            overlay_path = self.local_dir / rel_path
            if diff:
                text = _dump(diff)
                if (overlay_path.exists()
                        and overlay_path.read_text(encoding="utf-8") == text):
                    return
                overlay_path.parent.mkdir(parents=True, exist_ok=True)
                overlay_path.write_text(text, encoding="utf-8")
            elif overlay_path.exists():
                overlay_path.unlink()
            else:
                return  # 本来就没有覆盖层，也不需要建
        self._notify(rel_path)


# ─── 模块级单例 ──────────────────────────────────────────

_resolver: ConfigResolver | None = None


def get_resolver() -> ConfigResolver:
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


def save_app_config(input_sim: dict, delay_params: dict, envs: list | None = None) -> None:
    """保存输入模拟 + 延迟参数 + 环境列表到 app.yaml

    读-改-写全量：先取合并视图再改这几个键。save_merged 的入参语义是**完整
    文档**，只传自己关心的键会让 compute_diff 把其余键判成「用户删除」写进
    __deleted__，出厂新增的顶层键一保存就永久消失。
    """
    data = load_app_config()
    data["input_simulation"] = input_sim
    data["delay_params"] = delay_params
    if envs is not None:
        data["envs"] = envs
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
