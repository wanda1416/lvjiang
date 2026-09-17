"""脚本发现层

统一从两个来源自动发现「脚本」（对外称谓，内部仍为 workflow/wf）：

1. **.wf 来源**：**递归**扫描 ``workflows/`` 全树的 ``*.wf``（system ∪ local
   ∪ remote 合并视图），跳过 ``_`` 前缀的文件与目录（``_editor_run.wf`` /
   ``_recorded.wf``）。**是否注册为脚本由文件自己的 front-matter 决定**——
   见 :func:`~.metadata.script_traits`：未声明 ``runnable`` / ``batchable``
   的一律不注册。被 import 的过程库、批量生命周期钩子、归档文件因此和正式
   脚本可以放在同一棵树里，靠内容而不是路径区分。
2. **class 来源**：``implementations.list_workflows()`` 中已注册的内置类实现，
   name/parameters 取自类属性，id = 注册名，恒为可运行且可批量。

同 id 时按来源优先级仲裁：**local > class > system > remote**
（见 :class:`.policy.WorkflowDiscoveryPolicy.SOURCE_PRIORITY`）——local 恒
最高是因为用户自己的东西任何在线下发都不该盖掉；remote 恒最低是因为在线
下发只新增、永不抢占随包或用户脚本。

每项统一 shape：``{id, name, note, wf_file|class, parameters, runnable,
batchable, scope, hidden, source_layer, is_remote}``。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..core.config.resolver import get_resolver
from . import implementations
from .file_tree import WORKFLOWS_DIR
from .metadata import SCRIPT_ID_RE, metadata_for_script_config, script_traits
from .policy import WorkflowDiscoveryPolicy as Policy
from .preferences import load_preferences, migrate_legacy_workflows_yaml


@dataclass(frozen=True)
class DiscoveryProblem:
    """一个需要用户在脚本编辑器中处理的 ``.wf`` 问题。

    发现层逐文件隔离故障，绝不让一个坏文件拖垮全集；但"被忽略"不能只
    留在日志里——用户在外部编辑器改坏一行 YAML，看到的现象就是脚本从
    列表里消失了。id 冲突时入口会保留其中一份，但所有冲突文件仍必须醒目
    标记。这里把原因和类型带回 UI（编辑器树、主界面提示）。
    """

    wf_file: str   # 相对 workflows 根的 posix 路径
    message: str
    code: str = ""


#: 最近一次 ``discover_scripts()`` 记录的问题；UI 在刷新列表后读取。
_last_problems: list[DiscoveryProblem] = []


def last_discovery_problems() -> list[DiscoveryProblem]:
    """最近一次脚本发现中存在问题的文件及原因（按路径排序）。"""
    return sorted(_last_problems, key=lambda item: item.wf_file)


def _merge_candidate(
    bucket: dict[str, dict], candidate: dict,
    problems: list[DiscoveryProblem] | None = None,
) -> None:
    """把候选并入 ``{id: config}``；同 id 时按来源优先级仲裁。

    必须有一个稳定的优先级而不是"先扫到先赢"：扫描顺序取决于目录遍历，
    同一个 id 在本地和远程各有一份时，靠遍历顺序决定谁生效意味着行为会
    随着加一个无关文件而翻转。

    **同层**同 id 没有更高优先级可仲裁时稳定保留先注册者。所有冲突均写
    ERROR 日志；涉及的 ``.wf`` 全部记为问题，供编辑器显示红色冲突标记。
    """
    script_id = candidate["id"]
    existing = bucket.get(script_id)
    if existing is None:
        bucket[script_id] = candidate
        return

    new_rank = Policy.rank(candidate["source_layer"])
    old_rank = Policy.rank(existing["source_layer"])
    if new_rank < old_rank:
        selected, ignored = candidate, existing
        bucket[script_id] = candidate
    else:
        # 同层冲突稳定保留先注册者；低优先级候选同样不能覆盖高优先级项。
        selected, ignored = existing, candidate

    selected_name = selected["wf_file"] or selected["class"]
    ignored_name = ignored["wf_file"] or ignored["class"]
    logger.error(
        f"脚本 id 已注册，冲突项已忽略: {script_id!r}；"
        f"生效={selected_name}（{selected['source_layer']}），"
        f"忽略={ignored_name}（{ignored['source_layer']}）")
    if problems is not None:
        message = (
            f"脚本 id {script_id!r} 冲突；日常等入口只保留一份，"
            f"请在 #% id 中声明不同的 id")
        known = {(item.wf_file, item.code) for item in problems}
        for config in (existing, candidate):
            wf_file = config.get("wf_file")
            if wf_file and (wf_file, "duplicate_id") not in known:
                problems.append(DiscoveryProblem(
                    wf_file, message, code="duplicate_id"))
                known.add((wf_file, "duplicate_id"))


def _discover_wf_scripts(
    problems: list[DiscoveryProblem] | None = None,
) -> dict[str, dict]:
    """递归扫描 workflows 全树，返回已注册脚本的 {id: config}。

    被忽略的文件连同原因追加到 ``problems``（传 None 时只写日志）。
    """
    result: dict[str, dict] = {}
    if problems is None:
        problems = []
    resolver = get_resolver()
    for rel in resolver.enumerate_entity_tree(WORKFLOWS_DIR, "*.wf"):
        if Policy.is_internal(rel):
            continue
        p = resolver.resolve_read(f"{WORKFLOWS_DIR}/{rel}")
        if p is None:
            continue
        try:
            meta, warning = metadata_for_script_config(p)
        except Exception as exc:  # noqa: BLE001 — 逐文件故障隔离的最后一道防线
            logger.error(
                f"解析工作流元数据失败，已忽略该文件: {rel} "
                f"({type(exc).__name__}: {exc})")
            problems.append(DiscoveryProblem(rel, f"{type(exc).__name__}: {exc}"))
            continue
        if warning:
            # metadata_for_script_config 已把具体错误写进日志；这里只需要
            # 让 UI 知道"这个文件因元数据错误没注册"。
            problems.append(DiscoveryProblem(rel, warning))
            continue
        traits = script_traits(meta)
        if not traits["runnable"]:
            continue
        script_id = meta.get("id") or Policy.default_id_for(rel)
        if SCRIPT_ID_RE.fullmatch(str(script_id)) is None:
            message = (
                f"脚本 id 不合法: {script_id!r}；"
                "只允许 Unicode 字母、数字和下划线，且以字母开头")
            logger.error(f"{message}，已忽略 {rel}")
            problems.append(DiscoveryProblem(rel, message))
            continue
        origin = resolver.describe_entity(f"{WORKFLOWS_DIR}/{rel}")
        source_layer = origin.layer or "system"
        _merge_candidate(result, {
            "id": script_id,
            "name": meta.get("name") or Policy.default_id_for(rel),
            "note": warning or meta.get("note") or "",
            "wf_file": rel,
            "class": "",
            "parameters": meta.get("parameters") or [],
            "env": meta.get("env") or [],
            "runnable": True,
            "batchable": traits["batchable"],
            "scope": traits["scope"],
            "hidden": traits["hidden"],
            "source_layer": source_layer,
            "is_remote": source_layer == "remote",
        }, problems)
    return result


def _discover_class_scripts() -> dict[str, dict]:
    """遍历已注册内置类实现，返回 {id: config}。"""
    result: dict[str, dict] = {}
    for name in implementations.list_workflows():
        try:
            cls = implementations.get_workflow_class(name)
        except Exception as e:  # 注册指向的类无法导入时跳过，不影响其他脚本
            logger.warning(f"加载内置脚本类失败: {name} ({e})")
            continue
        result[name] = {
            "id": name,
            "name": getattr(cls, "DISPLAY_NAME", None) or name,
            "note": getattr(cls, "NOTE", None) or "",
            "wf_file": "",
            "class": name,
            "parameters": list(getattr(cls, "PARAMETERS", []) or []),
            "env": list(getattr(cls, "ENV", []) or []),
            # 脚本性质由实现自己声明（如自动调律天然是专用脚本），
            # 由实现声明而非系统配置表达，用户偏好另存 session。
            "runnable": True,
            "batchable": True,
            "scope": getattr(cls, "SCOPE", None) or "daily",
            "hidden": bool(getattr(cls, Policy.HIDDEN_CLASS_ATTR, False)),
            "source_layer": "class",
            "is_remote": False,
        }
    return result


def script_display_name(config: dict) -> str:
    """返回面向用户的脚本名；远程来源标记不可被显示名偏好移除。"""
    name = str(config.get("name") or config.get("id") or "")
    return f"[远程] {name}" if config.get("is_remote") else name


def discover_scripts() -> list[dict]:
    """自动发现全部可独立启动的脚本（.wf + 内置类）。

    Returns:
        脚本配置列表，每项 shape 见模块文档。按 id 排序，保证结果稳定
        （展示顺序由 list_exposed_scripts 决定）。
    """
    problems: list[DiscoveryProblem] = []
    merged = _discover_wf_scripts(problems)
    for cfg in _discover_class_scripts().values():
        # 同 id 时 class 与 .wf 之间同样走优先级仲裁，不再无条件覆盖
        _merge_candidate(merged, cfg, problems)
    _last_problems[:] = problems
    return [merged[k] for k in sorted(merged)]


def script_supports_env(config: dict, run_env: str | None) -> bool:
    """脚本 ``env`` 声明的静态判定，语义与 ``check_env`` 一致。

    未声明/空列表表示不限制；调用方没有可用环境快照时也不做
    过滤，真正启动前仍应再校验一次。
    """
    allowed = config.get("env") or []
    return not allowed or not run_env or run_env in allowed


def list_exposed_scripts(run_env: str | None = None) -> list[dict]:
    """通用入口展示的脚本：全集 → 作者声明的默认可见性 → 用户偏好覆盖。

    三层来源各司其职：

    - **全集**由 front-matter 的 ``runnable`` 决定，不可配置；
    - **默认是否展示**由作者声明的 ``hidden`` 和 ``scope`` 决定：隐藏脚本及
      ``dedicated`` 专用脚本不进入通用入口；
    - **顺序、启停、显示名**是用户偏好，存 session 的 ``daily.scripts``。

    因此系统新增的日常脚本会自动出现在列表里，不需要用户做任何事，也不会
    因为用户存过偏好就被冻住；新增专用脚本仍保持隐藏。桌面下拉与设备端
    悬浮面板共用本函数。

    Returns:
        脚本配置列表，shape 同 ``discover_scripts()``，``name`` 已套用用户
        自定义显示名。
    """
    discovered = {cfg["id"]: cfg for cfg in discover_scripts()}
    migrate_legacy_workflows_yaml()   # 一次性搬运，下个版本可删
    prefs = load_preferences()

    def shown(sid: str) -> bool:
        if sid in prefs.visible:
            return prefs.visible[sid]
        cfg = discovered[sid]
        scope = prefs.scopes.get(sid) or cfg.get("scope") or "daily"
        return Policy.visible_by_default(
            hidden=bool(cfg.get("hidden", False)), scope=scope)

    ordered = [sid for sid in prefs.order if sid in discovered]
    ordered += [sid for sid in sorted(discovered) if sid not in ordered]

    result: list[dict] = []
    for sid in ordered:
        if not shown(sid):
            continue
        cfg = dict(discovered[sid])
        if not script_supports_env(cfg, run_env):
            continue
        if prefs.names.get(sid):
            cfg["name"] = prefs.names[sid]
        cfg["scope"] = prefs.scopes.get(sid) or cfg.get("scope") or "daily"
        result.append(cfg)
    return result


def resolve_workflow_path(
    wf_file: str, script_id: str = "",
) -> tuple[Path | None, str]:
    """解析 ``.wf`` 实际路径，返回 ``(路径, 生效的 wf_file)``。

    缓存的 ``wf_file`` 失效时按 ``script_id`` 重新发现一次：升级期间调用方
    可能还攥着迁移前的路径，重新发现能把同一脚本 ID 校正到新目录，同时不
    掩盖文件确实缺失的情况（两次都找不到就返回 ``None``）。
    """
    resolver = get_resolver()

    def _resolve(candidate: str) -> Path | None:
        if not candidate:
            return None
        path = Path(candidate)
        if path.is_absolute():
            return path if path.is_file() else None
        found = resolver.resolve_read(f"workflows/{candidate}")
        return (Path(found)
                if found is not None and Path(found).is_file() else None)

    path = _resolve(wf_file)
    if path is not None:
        return path, wf_file
    if not script_id:
        return None, wf_file

    fresh = next(
        (cfg for cfg in discover_scripts()
         if cfg.get("id") == script_id and cfg.get("wf_file")),
        None,
    )
    if fresh is None:
        return None, wf_file
    fresh_file = str(fresh["wf_file"])
    path = _resolve(fresh_file)
    if path is None:
        return None, wf_file
    if fresh_file != wf_file:
        logger.info(f"工作流路径已刷新: {wf_file or '<空>'} -> {fresh_file}")
    return path, fresh_file
