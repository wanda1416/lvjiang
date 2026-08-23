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
from .metadata import parse_metadata_file

# 可直接由用户启动的脚本目录。显式列举可避免把 subcall、batch 生命周期等
# 不能独立执行的实现文件误注册到脚本列表。
_DISCOVERABLE_WF_DIRS = ("", "standalone")


def _discover_wf_scripts() -> dict[str, dict]:
    """扫描可直接启动的 .wf（system ∪ local），返回 {id: config}。"""
    result: dict[str, dict] = {}
    resolver = get_resolver()
    for subdir in _DISCOVERABLE_WF_DIRS:
        rel_dir = f"workflows/{subdir}" if subdir else "workflows"
        for name in resolver.enumerate_entities(rel_dir, "*.wf"):
            wf_file = f"{subdir}/{name}" if subdir else name
            p = resolver.resolve_read(f"workflows/{wf_file}")
            if p is None:
                continue
            script_id = p.stem
            if script_id in result:
                logger.warning(
                    f"脚本 id 重复，忽略 {wf_file}: {script_id}")
                continue
            meta = parse_metadata_file(p)
            result[script_id] = {
                "id": script_id,
                "name": meta.get("name") or script_id,
                "note": meta.get("note") or "",
                "wf_file": wf_file,
                "class": "",
                "parameters": meta.get("parameters") or [],
                "env": meta.get("env") or [],
                "batchable": subdir != "standalone",
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
            "batchable": True,
        }
    return result


def discover_scripts() -> list[dict]:
    """自动发现全部可用脚本（.wf + 内置类），同 id 时 class 覆盖 .wf。

    Returns:
        脚本配置列表，每项 shape：``{id, name, wf_file, class, parameters}``。
        按 id 排序，保证结果稳定（暴露顺序由 workflows.yaml 的 exposed 决定）。
    """
    merged = _discover_wf_scripts()
    merged.update(_discover_class_scripts())  # class 覆盖同 id 的 .wf
    return [merged[k] for k in sorted(merged)]


def _load_exposure() -> tuple[list, dict]:
    """读取 workflows.yaml（合并视图）的 exposed 列表与 overrides 映射。"""
    try:
        data = get_resolver().load_merged("workflows.yaml")
    except Exception as e:
        logger.error(f"加载脚本暴露配置失败: {e}")
        return [], {}
    if not data:
        return [], {}
    return (data.get("exposed") or []), (data.get("overrides") or {})


def list_exposed_scripts() -> list[dict]:
    """发现全部脚本后，按 workflows.yaml 的 exposed/overrides 过滤+排序+改名。

    这是「暴露层」：``discover_scripts()`` 给全集，本函数决定日常展示哪些、
    顺序、以及显示名覆盖。语义与桌面「工具 → 脚本配置」一致：
    exposed 缺失/为空 → 展示全部（按 id 排序）；否则仅展示且按其顺序。
    桌面下拉与设备端悬浮面板共用本函数，避免两处各写一份暴露逻辑。

    Returns:
        脚本配置列表，shape 同 ``discover_scripts()``，name 已套用 overrides，
        额外含 ``scope`` 字段（"daily" 或 "dedicated"，默认 "daily"）。
    """
    discovered = {cfg["id"]: cfg for cfg in discover_scripts()}
    exposed, overrides = _load_exposure()
    order = [i for i in exposed if i in discovered] if exposed else sorted(discovered)
    result: list[dict] = []
    for sid in order:
        cfg = dict(discovered[sid])
        ov = overrides.get(sid) or {}
        if ov.get("name"):
            cfg["name"] = ov["name"]
        cfg["scope"] = ov.get("scope", "daily")
        result.append(cfg)
    return result
