"""DSL 静态场景引用搜集

遍历已解析的 AST，收集所有静态场景名（`[scene].[...]` 中的 scene）。
供 engine 在执行 .wf 前做「场景是否已绑定坐标」的校验，取代手写的
required_scenes 声明。

场景名恒为静态字符串（region 可为 $var 动态引用，但 scene 不会），
因此无需运行时求值即可完整搜集。
"""
from __future__ import annotations

from .grammar.ast_nodes import (
    Align, Click, Drag, ForRange, If, For, Loop, PanelGridDrag,
    PanelRef, ProcDef, Recognize, Scan, SceneRef,
)


def _scene_of(node) -> str | None:
    """从单个引用节点取静态场景名，非场景引用返回 None"""
    if isinstance(node, SceneRef):
        return node.scene
    if isinstance(node, PanelRef):
        return node.scene
    return None


def _collect_from_stmt(stmt, acc: set[str]) -> None:
    """从单条语句（含其携带的引用与嵌套体）收集场景名"""
    # 携带场景引用的语句
    if isinstance(stmt, Click):
        s = _scene_of(stmt.target)
        if s:
            acc.add(s)
    elif isinstance(stmt, Drag):
        for ref in (stmt.scene, stmt.arrow):
            s = _scene_of(ref)
            if s:
                acc.add(s)
    elif isinstance(stmt, (Scan, Recognize)):
        s = _scene_of(stmt.scene)
        if s:
            acc.add(s)
    elif isinstance(stmt, (Align, PanelGridDrag)):
        # scene 为裸字符串
        if stmt.scene:
            acc.add(stmt.scene)

    # 嵌套体递归
    if isinstance(stmt, If):
        _collect_from_body(stmt.then_body, acc)
        _collect_from_body(stmt.else_body, acc)
    elif isinstance(stmt, (For, ForRange, Loop)):
        _collect_from_body(stmt.body, acc)


def _collect_from_body(body, acc: set[str]) -> None:
    for stmt in body or []:
        _collect_from_stmt(stmt, acc)


def collect_scene_keys(body: list, procs: dict) -> set[str]:
    """收集程序主体与所有过程（含 import 平铺进来的）引用的静态场景名。

    Args:
        body: 程序顶层语句列表（Program.body）
        procs: 过程名 -> ProcDef（已合并 import 后的全部过程）

    Returns:
        去重后的场景 key 集合
    """
    acc: set[str] = set()
    _collect_from_body(body, acc)
    for proc in (procs or {}).values():
        if isinstance(proc, ProcDef):
            _collect_from_body(proc.body, acc)
    return acc
