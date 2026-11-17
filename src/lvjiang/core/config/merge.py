"""配置合并：两层配置的合并、增量求解与合并策略登记。

system 为已安装的官方内容，local 为用户覆盖。一物一文件的实体按整文件影子
处理（local 有就完全顶掉）；聚合键值文件按键级 diff 深合并：dict 递归、列表
与标量整键替换，每层 dict 支持 ``__deleted__`` 删除键。

两类列表需要不同语义，策略以「相对路径」登记，登记表本身就在这里：

- 枚举设定（``quality_thresholds.武器`` 之类）→ 整键替换，用户就是要覆盖；
- 注册表（:data:`REGISTRY_LIST_PATHS`）→ local 只存 ``__added__`` /
  ``__removed__`` / ``__order__`` 增量。整键替换会让 local 冻住整张表，系统
  后续新增的条目永远进不到合并视图。存量 local 的普通列表仍按整键替换处理，
  用户下次保存时由 :func:`compute_diff` 自动转成增量形式。

**本模块不认识任何插件领域词汇**：插件私有的登记表路径、受保护列表由插件经
:func:`register_registry_list_paths` / :func:`register_protected_list_paths`
自己声明（``AppHooks.config_policy_modules`` import 即注册）。

这里的函数都不碰文件与数据库：入参是已解析的 dict/list，出参是新对象。
"""

from __future__ import annotations

from copy import deepcopy

from loguru import logger

# 聚合 diff 中的删除键标记
DELETED_KEY = "__deleted__"

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
    # 场景/布局注册表已是目录化结构（scenes/、layouts/ 一物一文件），
    # namespace 优先级链（system > local）取代了聚合文件 diff，见
    # core.scene.config / core.layout.config。本表保留给仍用聚合文件的登记表。
    # 环境与方案都是带稳定 key 的登记表：local 存整张表会把系统后续新增的
    # 环境/方案挡在合并视图之外。按身份对齐见 REGISTRY_LIST_IDENTITY。
    "app.yaml": ("envs", "plans"),
}


#: 注册表列表的「条目身份」：``{文件: {点分路径: 身份字段}}``。
#:
#: 未声明的注册表列表按**整条值**比较：改一个字段就变成「删一条 + 加一条」，
#: 合并时被改的条目会被挪到末尾，``diff_registry_list`` 便只能退化成写整张
#: ``__order__``，local 又冻住了整张表。
#:
#: 声明身份后按身份对齐：同身份条目在 ``__added__`` 里**原位替换**，新条目追加。
#: 并且这类列表**永不产生 __removed__**——随包分发的预置条目在用户层不允许
#: 删除，保存时缺失的系统条目也会按原位补回（与 PROTECTED_LIST_PATHS 同一
#: 产品约定：系统内容只可改值与新增，不可删除）。
REGISTRY_LIST_IDENTITY: dict[str, dict[str, str]] = {
    "app.yaml": {"envs": "key", "plans": "id"},
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
DELETABLE_PATHS: dict[str, tuple[str, ...]] = {
    # 模板索引：条目随模板图一起增删，系统侧的条目也必须允许被删掉
    # （删系统模板要开发模式；用户删除只会清自己的 local 影子）。
    "templates/templates.yaml": ("templates",),
}

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


def merge_registry_list(base_list: list, overlay: dict,
                        identity_field: str | None = None) -> list:
    """注册表列表：system 基底 − __removed__ + __added__，再按 __order__ 排序

    __order__ 里没提到的条目（通常是系统后来新增的）排在末尾，保持 system 里的
    相对顺序——新条目宁可靠后也不能消失。

    ``identity_field`` 声明后按条目身份对齐：``__added__`` 里同身份的条目原位
    替换、新身份追加，``__removed__`` 被忽略（预置条目不可删除，见
    REGISTRY_LIST_IDENTITY）。
    """
    overlay = overlay or {}
    if identity_field is None:
        removed = set(overlay.get(REMOVED_KEY) or [])
        result = [x for x in (base_list or []) if x not in removed]
        for item in overlay.get(ADDED_KEY) or []:
            if item not in result:
                result.append(item)
    else:
        result = list(base_list or [])
        position: dict = {}
        for index, item in enumerate(result):
            ident = _identity(item, identity_field)
            if ident is not None:
                position[ident] = index
        for item in overlay.get(ADDED_KEY) or []:
            ident = _identity(item, identity_field)
            if ident is not None and ident in position:
                result[position[ident]] = item
            elif item not in result:
                position[ident] = len(result)
                result.append(item)
    order = overlay.get(ORDER_KEY)
    if order:
        rank = (
            {name: i for i, name in enumerate(order)}
            if identity_field is None
            else {
                _identity(name, identity_field): i
                for i, name in enumerate(order)
            }
        )

        def _rank(item):
            """sort 稳定：未在 order 中的条目（系统新增）保持原相对顺序落末尾。"""
            ident = item if identity_field is None else _identity(item, identity_field)
            return (0, rank[ident]) if ident in rank else (1, 0)

        result.sort(key=_rank)
    return result


def diff_registry_list(base_list: list, desired: list,
                       identity_field: str | None = None) -> dict:
    """求注册表列表的增量；无差异返回空 dict

    仅在「增删还原不出目标顺序」时才写 __order__，保证
    merge_registry_list(base, diff) == desired 恒成立。

    ``identity_field`` 声明后只产出 ``__added__``（含被改动的同身份条目），
    不产出 ``__removed__``：预置条目在用户层不可删除。
    """
    base_list = list(base_list or [])
    desired = list(desired or [])
    diff: dict = {}
    if identity_field is not None:
        current = {
            _identity(item, identity_field): item
            for item in base_list
            if _identity(item, identity_field) is not None
        }
        added = [
            item for item in desired
            if current.get(_identity(item, identity_field)) != item
        ]
        if added:
            diff[ADDED_KEY] = added
        if merge_registry_list(base_list, diff, identity_field) == desired:
            return diff
        diff[ORDER_KEY] = desired
        return diff

    added = [x for x in desired if x not in base_list]
    removed = [x for x in base_list if x not in desired]
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
              registry: tuple[str, ...] = (), _prefix: str = "",
              identity: dict[str, str] | None = None) -> dict:
    """system 文档 ← local diff 深合并（不修改入参）

    dict 递归合并；列表与标量整键替换；overlay 每层的 __deleted__
    列表指定要从结果中删除的键。

    registry 声明哪些点分路径按注册表语义合并（见 REGISTRY_LIST_PATHS）。
    该路径下 overlay 若是 __added__/__removed__/__order__ 形式的 dict 则按增量
    合并；**若仍是普通列表则照旧整键替换**，保持存量 local 文件的行为不变，
    等用户下次保存时由 compute_diff 自动转成增量形式。

    ``identity`` 给出注册表路径的条目身份字段（见 REGISTRY_LIST_IDENTITY），
    声明后按身份原位替换而不是整条增删。
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
            result[key] = merge_registry_list(
                result.get(key) or [], value, (identity or {}).get(path))
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_doc(result[key], value, registry, path, identity)
        else:
            result[key] = deepcopy(value)
    return result


def compute_diff(base: dict, desired: dict,
                 registry: tuple[str, ...] = (), _prefix: str = "",
                 deletable: tuple[str, ...] | None = None,
                 protected: dict[str, str | None] | None = None,
                 identity: dict[str, str] | None = None) -> dict:
    """逆向求最小 diff，满足 merge_doc(base, diff) == desired

    新增/改动键入 diff；base 有而 desired 无的键入 __deleted__；
    嵌套 dict 递归求解，空节点不写入。

    registry 声明的路径上，两侧都是列表时改求增量（__added__/__removed__，
    必要时补 __order__），local 因此只记用户自己的改动，不再冻住整张表。
    ``identity`` 给出这些路径的条目身份字段：声明后按身份对齐、且不产生
    __removed__，缺失的系统条目会先按原位补回（预置条目不可删除）。

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
        identity_field = (identity or {}).get(path)
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
            if identity_field is not None:
                value, restored = restore_protected_list(
                    base_value, value, identity_field)
                if restored:
                    logger.warning(
                        f"配置删除被拦下：{path} 下的系统条目 {restored} 不允许移除，"
                        f"已补回；如需停用请走激活机制或直接改值")
            sub = diff_registry_list(base_value, value, identity_field)
            if sub:
                diff[key] = sub
        elif isinstance(value, dict) and isinstance(base_value, dict):
            sub = compute_diff(
                base_value, value, registry, path, deletable, protected, identity)
            if sub:
                diff[key] = sub
        elif key not in base or base_value != value:
            diff[key] = deepcopy(value)
    return diff
