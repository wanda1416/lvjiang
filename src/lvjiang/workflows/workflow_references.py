"""DSL 静态配置引用搜集

遍历已解析的 AST，收集所有静态引用（`[scene].[key]` 的 scene 与 key）。
供 engine 在执行 .wf 前做「引用是否已在当前布局绑定坐标」的校验，取代
手写的 required_scenes 声明。

场景名恒为静态字符串（key 可为 $var 动态引用，但 scene 不会），因此无需
运行时求值即可完整搜集；key 为 $var 时只记场景、不记 key（运行时才知道）。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..i18n import tr
from .grammar.ast_nodes import (
    Align,
    Click,
    Drag,
    Find,
    For,
    ForRange,
    If,
    Literal,
    Loop,
    PanelGridDrag,
    PanelRef,
    ProcDef,
    Recognize,
    Scan,
    SceneRef,
    Try,
    UntilLoop,
    WhileLoop,
)

# 引用类别 → 需要在布局中查找的对象类型说明（错误提示用）
KIND_LABELS = {
    "click_target": tr("区域/坐标点/面板"),
    "drag_target": tr("方向/区域"),
    "drag_grid_target": tr("面板/区域"),
    "arrow": tr("方向"),
    "panel": tr("面板"),
    "region": tr("区域"),
    "scan": tr("区域/面板"),
    "point": tr("坐标点"),
}


@dataclass(frozen=True)
class RefUse:
    """脚本中一处静态配置引用

    scene: 场景 key（恒为静态字符串）
    key:   region / arrow / panel 的 key；None 表示只引用了场景本身
    kind:  KIND_LABELS 的键，决定该 key 该在布局的哪类对象里查
    line_no: 语句行号
    source: 该语句所在文件（import 进来的 def 体行号相对它自己的文件）
    """
    scene: str
    key: str | None
    kind: str
    line_no: int
    source: str = ""


def _static_key(node) -> str | None:
    """取静态 key 字符串；VarRef / None / 非字符串一律返回 None"""
    if isinstance(node, str):
        return node
    if isinstance(node, Literal) and isinstance(node.value, str):
        return node.value
    return None


def _add(acc: list[RefUse], scene, key, kind: str, line: int) -> None:
    """记一条引用；scene 为 $var 动态引用时静态无从校验，直接丢弃"""
    if isinstance(scene, str) and scene:
        acc.append(RefUse(scene, _static_key(key), kind, line))


def _collect_from_stmt(stmt, acc: list[RefUse]) -> None:
    """从单条语句（含其携带的引用与嵌套体）收集引用"""
    line = getattr(stmt, "line_no", 0)

    if isinstance(stmt, Click):
        target = stmt.target
        if isinstance(target, SceneRef):
            # click_any 先查 region 再查 point 再查 panel
            _add(acc, target.scene, target.region, "click_target", line)
        elif isinstance(target, PanelRef):
            _add(acc, target.scene, target.panel, "panel", line)
    elif isinstance(stmt, Drag):
        # scene / arrow 对场景与 panel 目标是同一节点，去重后只留一条
        for ref in (stmt.scene, stmt.arrow):
            if isinstance(ref, SceneRef):
                # drag 的 key 查的是布局 arrows 或 regions
                _add(acc, ref.scene, ref.region, "drag_target", line)
            elif isinstance(ref, PanelRef):
                _add(acc, ref.scene, ref.panel, "panel", line)
            elif isinstance(ref, PanelGridDrag):
                # panel grid 拖拽也支持 region
                _add(acc, ref.scene, ref.panel, "drag_grid_target", line)
        # 点对模式：drag [scene1].[point1] [scene2].[point2]
        for ref in (stmt.from_scene_ref, stmt.to_scene_ref):
            if isinstance(ref, SceneRef):
                _add(acc, ref.scene, ref.region, "point", line)
    elif isinstance(stmt, (Scan, Recognize)):
        scene_ref = stmt.scene
        if isinstance(scene_ref, PanelRef):
            _add(acc, scene_ref.scene, scene_ref.panel, "panel", line)
        elif isinstance(scene_ref, SceneRef):
            # fields 为识别的区域 key 列表；无 fields（或动态 region）时为整场景识别
            if stmt.fields:
                # 单一 key 运行时可能分派为整面板识别，放宽为 区域/面板
                kind = "scan" if len(stmt.fields) == 1 else "region"
                for field in stmt.fields:
                    _add(acc, scene_ref.scene, field, kind, line)
            else:
                _add(acc, scene_ref.scene, None, "region", line)
    elif isinstance(stmt, Find):
        # find 指令的搜索区域（若有）需要校验绑定
        # 支持 region 和 panel（两者对 find 等价，都提供矩形裁剪区域）
        if stmt.search_scene is not None and stmt.search_region is not None:
            # 只有静态场景名和区域名才能校验
            if isinstance(stmt.search_scene, str) and isinstance(stmt.search_region, str):
                _add(acc, stmt.search_scene, stmt.search_region, "scan", line)
            elif isinstance(stmt.search_scene, str):
                # 静态场景 + 动态区域：只校验场景
                _add(acc, stmt.search_scene, None, "scan", line)
    elif isinstance(stmt, (Align, PanelGridDrag)):
        # scene / panel 均为裸字符串
        _add(acc, stmt.scene, stmt.panel, "panel", line)

    # 嵌套体递归
    if isinstance(stmt, If):
        _collect_from_body(stmt.then_body, acc)
        _collect_from_body(stmt.else_body, acc)
    elif isinstance(stmt, (For, ForRange, Loop, WhileLoop, UntilLoop)):
        _collect_from_body(stmt.body, acc)
    elif isinstance(stmt, Try):
        _collect_from_body(stmt.body, acc)
        _collect_from_body(stmt.catch_body, acc)


def _collect_from_body(body, acc: list[RefUse]) -> None:
    for stmt in body or []:
        _collect_from_stmt(stmt, acc)


def collect_refs(body: list, procs: dict, proc_sources: dict | None = None,
                 source: str = "") -> list[RefUse]:
    """收集程序主体与所有过程（含 import 平铺进来的）的静态配置引用。

    Args:
        body: 程序顶层语句列表（Program.body）
        procs: 过程名 -> ProcDef（已合并 import 后的全部过程）
        proc_sources: 过程名 -> 所在文件，缺失时回退到 source
        source: 顶层语句所在文件

    Returns:
        引用列表，按出现顺序、同 (scene, key, kind, 行号, 文件) 去重
    """
    acc: list[RefUse] = []
    main: list[RefUse] = []
    _collect_from_body(body, main)
    acc.extend(replace(ref, source=source) for ref in main)
    for name, proc in (procs or {}).items():
        if not isinstance(proc, ProcDef):
            continue
        sub: list[RefUse] = []
        _collect_from_body(proc.body, sub)
        proc_source = (proc_sources or {}).get(name, source)
        acc.extend(replace(ref, source=proc_source) for ref in sub)

    seen: set[RefUse] = set()
    uniq: list[RefUse] = []
    for ref in acc:
        if ref not in seen:
            seen.add(ref)
            uniq.append(ref)
    return uniq


def collect_scene_keys(body: list, procs: dict) -> set[str]:
    """收集脚本引用的静态场景名（去重）"""
    return {ref.scene for ref in collect_refs(body, procs)}
