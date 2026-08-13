"""脚本发现层

统一从两个来源自动发现「脚本」（对外称谓，内部仍为 workflow/wf）：

1. **.wf 来源**：workflows 目录顶层 ``*.wf`` 文件（system ∪ local 合并视图），
   跳过 ``_`` 前缀（如 ``_editor_run.wf`` / ``_recorded.wf``）与 ``subcall/`` /
   ``testwf/`` 等子目录。
   name/parameters 取自文件顶部的 ``#%`` front-matter，id = 文件名 stem。
2. **class 来源**：``implementations.list_workflows()`` 中已注册的内置类实现，
   name/parameters 取自类属性 ``DISPLAY_NAME`` / ``PARAMETERS``，id = 注册名。

同 id 时 class 覆盖 .wf（如 ``single_tuning`` 同时存在 .wf 与类实现，以类为准）。
每项统一 shape：``{id, name, wf_file|class, parameters}``，不再含 ``required_scenes``
（场景校验改由 engine 执行时按 AST 静态搜集）。
"""
from __future__ import annotations

from loguru import logger

from ..core.config_resolver import get_resolver
from . import implementations
from .metadata import parse_metadata_file


def _discover_wf_scripts() -> dict[str, dict]:
    """扫描 workflows/ 顶层 .wf（system ∪ local），返回 {id: config}。"""
    result: dict[str, dict] = {}
    resolver = get_resolver()
    for name in resolver.enumerate_entities("workflows", "*.wf"):
        p = resolver.resolve_read(f"workflows/{name}")
        if p is None:
            continue
        meta = parse_metadata_file(p)
        result[p.stem] = {
            "id": p.stem,
            "name": meta.get("name") or p.stem,
            "wf_file": p.name,
            "class": "",
            "parameters": meta.get("parameters") or [],
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
            "wf_file": "",
            "class": name,
            "parameters": list(getattr(cls, "PARAMETERS", []) or []),
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
