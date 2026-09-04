"""系统布局 × Python 类工作流的静态坐标引用门禁。

DSL 工作流由 ``test_system_wf_refs_gate.py`` 覆盖；本文件补齐类工作流中
``self.click_region(...)`` 等调用。只抽取可以静态求值的字符串字面量、
``self.CONST`` 和字符串列表，动态场景或动态 key 仍需运行时验证。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from lvjiang.apps import load_app
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR
from lvjiang.core.layout_manager import load_layout_by_name
from tests.case_matrix import case_matrix

# 方法名 → (场景参数位, region/key 参数位, kind)
_CALLS = {
    "click_region": (0, 1, "region"),
    "ocr_scene": (0, 1, "region_list"),
    "ocr_scene_by": (0, 1, "region_list"),
    "click_panel": (0, 1, "panel"),
}


def _literal(node: ast.AST, cls):
    """求值字符串字面量、self.CONST 和字符串列表。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        return getattr(cls, node.attr, None)
    if isinstance(node, ast.List):
        values = [_literal(element, cls) for element in node.elts]
        return [value for value in values if isinstance(value, str)]
    return None


def _collect(path: Path, cls) -> list[tuple[str, str, str, int]]:
    """返回 ``(scene, key, kind, lineno)``；动态参数调用跳过。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    refs: list[tuple[str, str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (
            isinstance(fn, ast.Attribute)
            and isinstance(fn.value, ast.Name)
            and fn.value.id == "self"
            and fn.attr in _CALLS
        ):
            continue
        scene_index, key_index, kind = _CALLS[fn.attr]
        if len(node.args) <= scene_index:
            continue
        scene = _literal(node.args[scene_index], cls)
        if not isinstance(scene, str):
            continue
        keys = (
            _literal(node.args[key_index], cls)
            if len(node.args) > key_index
            else None
        )
        if kind == "region_list":
            for key in keys if isinstance(keys, list) else []:
                refs.append((scene, key, "region", node.lineno))
        elif isinstance(keys, str):
            refs.append((scene, keys, kind, node.lineno))
        else:
            refs.append((scene, "", "scene_only", node.lineno))
    return refs


def _class_workflows() -> list[tuple[str, type]]:
    """从设备端实际插件声明取类工作流，避免维护硬编码名册。"""
    workflows: list[tuple[str, type]] = []
    # 测试组合根显式声明当前 Android 构建包含的 app。
    for app_name in ("yysls",):
        hooks = load_app(app_name)
        for workflow_id, class_path in hooks.workflow_implementations.items():
            module_path, class_name = class_path.rsplit(".", 1)
            cls = getattr(importlib.import_module(module_path), class_name)
            workflows.append((workflow_id, cls))
    return workflows


def _system_layouts() -> list[str]:
    layouts_dir = SYSTEM_CONFIG_DIR / "layouts"
    if not layouts_dir.is_dir():
        return []
    return sorted(
        path.name
        for path in layouts_dir.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    )


def test_class_workflow_gate_has_inputs():
    """门禁自身不能因清单意外变空而静默通过。"""
    assert _class_workflows(), "设备插件没有声明任何 Python 类工作流"
    assert _system_layouts(), "config/system/layouts 下没有布局"


@case_matrix("layout_name", _system_layouts())
@case_matrix(
    ("workflow_id", "cls"),
    _class_workflows(),
    ids=lambda value: value if isinstance(value, str) else value.__name__,
)
def test_class_workflow_refs_all_bound(workflow_id, cls, layout_name):
    """所有可静态求值的类工作流坐标引用必须在系统布局中有绑定。"""
    module = importlib.import_module(cls.__module__)
    path = Path(module.__file__)
    refs = _collect(path, cls)
    assert refs, f"{workflow_id} 没有抽取到任何静态坐标引用，门禁可能已失效"

    layout = load_layout_by_name(layout_name)
    assert layout is not None, f"布局加载失败: {layout_name}"

    problems: list[str] = []
    for scene, key, kind, line in sorted(set(refs)):
        regions = {item.key for item in layout.get_scene_regions(scene)}
        points = {item.key for item in layout.get_scene_points(scene)}
        panels = {item.key for item in layout.get_scene_panels(scene)}
        if not (regions or points or panels):
            problems.append(f"{path.name}:{line} [{scene}] 场景未绑定任何坐标")
            continue
        if kind == "scene_only":
            continue
        if kind == "region":
            bound = key in regions or key in points
        else:
            bound = key in panels
        if not bound:
            problems.append(
                f"{path.name}:{line} [{scene}].[{key}] {kind} 未绑定"
            )

    assert not problems, f"{workflow_id} × {layout_name}:\n" + "\n".join(problems)
