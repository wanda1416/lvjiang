"""脚本发现层

统一从两个来源自动发现「脚本」（对外称谓，内部仍为 workflow/wf）：

1. **.wf 来源**：workflows 目录顶层及 ``standalone/`` 下的 ``*.wf`` 文件
   （system ∪ local 合并视图），跳过 ``_`` 前缀（如 ``_editor_run.wf`` /
   ``_recorded.wf``）。``subcall/``、``batch/``、``archived/`` 等内部目录不参与发现。
   name/note/parameters 取自文件顶部的 ``#%`` front-matter，id = 文件名 stem。
2. **class 来源**：``implementations.list_workflows()`` 中已注册的内置类实现，
   name/parameters 取自类属性 ``DISPLAY_NAME`` / ``PARAMETERS``，id = 注册名。

同 id 时 class 覆盖 .wf。
每项统一 shape：``{id, name, note, wf_file|class, parameters, batchable}``，
不再含 ``required_scenes``
（场景校验改由 engine 执行时按 AST 静态搜集）。
"""
from __future__ import annotations

from loguru import logger

from ..core.config.resolver import get_resolver
from . import implementations
from .metadata import metadata_for_script_config
from .policy import WorkflowDiscoveryPolicy as Policy
from .preferences import load_preferences, migrate_legacy_workflows_yaml


def _discover_wf_scripts() -> dict[str, dict]:
    """扫描可直接启动的 .wf（system ∪ local），返回 {id: config}。"""
    result: dict[str, dict] = {}
    resolver = get_resolver()
    for subdir in Policy.SCAN_DIRS:
        rel_dir = f"workflows/{subdir}" if subdir else "workflows"
        for name in resolver.enumerate_entities(rel_dir, "*.wf"):
            wf_file = f"{subdir}/{name}" if subdir else name
            p = resolver.resolve_read(f"workflows/{wf_file}")
            if p is None:
                continue
            script_id = p.stem
            if Policy.is_internal(script_id):
                continue
            if script_id in result:
                logger.warning(
                    f"脚本 id 重复，忽略 {wf_file}: {script_id}")
                continue
            meta, warning = metadata_for_script_config(p)
            result[script_id] = {
                "id": script_id,
                "name": meta.get("name") or script_id,
                "note": warning or meta.get("note") or "",
                "wf_file": wf_file,
                "class": "",
                "parameters": meta.get("parameters") or [],
                "env": meta.get("env") or [],
                "batchable": Policy.is_batchable(subdir),
                "scope": meta.get("scope") or "daily",
                "hidden": Policy.hidden_by_default(meta),
            }
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
            # 由实现声明而非出厂配置表达，用户偏好另存 session。
            "scope": getattr(cls, "SCOPE", None) or "daily",
            "hidden": bool(getattr(cls, Policy.HIDDEN_CLASS_ATTR, False)),
            "batchable": True,
        }
    return result


def discover_scripts() -> list[dict]:
    """自动发现全部可用脚本（.wf + 内置类），同 id 时 class 覆盖 .wf。

    Returns:
        脚本配置列表，每项 shape：``{id, name, wf_file, class, parameters}``。
        按 id 排序，保证结果稳定（展示顺序由 list_exposed_scripts 决定）。
    """
    merged = _discover_wf_scripts()
    merged.update(_discover_class_scripts())  # class 覆盖同 id 的 .wf
    return [merged[k] for k in sorted(merged)]


def list_exposed_scripts() -> list[dict]:
    """通用入口展示的脚本：全集 → 作者声明的默认可见性 → 用户偏好覆盖。

    三层来源各司其职：

    - **全集**由目录约定决定（见 :class:`WorkflowDiscoveryPolicy`），不可配置；
    - **默认是否展示**由作者声明的 ``hidden`` 和 ``scope`` 决定：隐藏脚本及
      ``dedicated`` 专用脚本不进入通用入口；
    - **顺序、启停、显示名**是用户偏好，存 session 的 ``daily.scripts``。

    因此出厂新增的日常脚本会自动出现在列表里，不需要用户做任何事，也不会
    因为用户存过偏好就被冻住；新增专用脚本仍保持隐藏。桌面下拉与设备端
    悬浮面板共用本函数。

    Returns:
        脚本配置列表，shape 同 ``discover_scripts()``，``name`` 已套用用户
        自定义显示名，额外含 ``scope``（"daily" / "dedicated"）。
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
        if prefs.names.get(sid):
            cfg["name"] = prefs.names[sid]
        cfg["scope"] = prefs.scopes.get(sid) or cfg.get("scope") or "daily"
        result.append(cfg)
    return result
