"""流程位置元数据；独立于运行时坐标依赖和页面状态。"""
from dataclasses import fields, is_dataclass

from .grammar.ast_nodes import SceneDeclaration


def collect_scene_declarations(program):
    """保留每个声明及行号，遍历所有分支和过程；不推断控制流状态。"""
    def walk(value):
        if isinstance(value, SceneDeclaration):
            yield value
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from walk(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif is_dataclass(value):
            for field in fields(value):
                yield from walk(getattr(value, field.name))
    return list(walk(program))


def validate_scene_declarations(program, scenes):
    problems = []
    for declaration in collect_scene_declarations(program):
        if declaration.scene is None:
            continue
        scene = scenes.get(declaration.scene)
        if scene is None:
            problems.append((declaration.line_no, f"声明的场景不存在: {declaration.scene}"))
        elif scene.is_subscene:
            # 子场景是父场景画布里的一块局部，不是可跳转的页面：脚本按 `scene`
            # 声明它会得到一个永远匹配不上的流程位置。
            problems.append((
                declaration.line_no,
                f"声明的场景是子场景，不能作为流程位置: {declaration.scene}"
                f"（请声明引用它的父场景）"))
        elif declaration.view is not None and not (
            any(v.key == declaration.view for v in scene.views)
            or declaration.view == "base" and not scene.views
        ):
            problems.append((declaration.line_no, f"声明的视图不存在: {declaration.scene}/{declaration.view}"))
    return problems
