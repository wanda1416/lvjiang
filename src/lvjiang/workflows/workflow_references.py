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
    ArithOp,
    CallProc,
    Click,
    Drag,
    EntityRef,
    Eval,
    EvalFieldChainAssign,
    Find,
    For,
    ForRange,
    If,
    Literal,
    Loop,
    Move,
    PanelGridDrag,
    PanelRef,
    ProcDef,
    Recognize,
    Scan,
    Scroll,
    SubsceneEntityRef,
    Try,
    UntilLoop,
    WaitStable,
    WhileLoop,
)

# 引用类别 → 需要在布局中查找的对象类型说明（错误提示用）
KIND_LABELS = {
    "click_target": tr("区域/坐标点/面板"),
    "move_target": tr("区域/坐标点/面板"),
    "scroll_target": tr("区域/坐标点/面板"),
    "drag_target": tr("方向/区域"),
    "drag_grid_target": tr("面板/区域"),
    "arrow": tr("方向"),
    "panel": tr("面板"),
    "region": tr("区域"),
    "scan": tr("区域/面板"),
    "point": tr("坐标点"),
    "stable_area": tr("区域"),
    "expr_ref": tr("区域/坐标点/面板"),
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
    reference: str | None = None
    is_subscene: bool = False


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


def _add_subscene(acc: list[RefUse], node: SubsceneEntityRef,
                  kind: str, line: int) -> None:
    reference = _static_key(node.reference)
    entity = _static_key(node.entity)
    if isinstance(node.scene, str) and node.scene:
        acc.append(RefUse(node.scene, entity, kind, line,
                          reference=reference, is_subscene=True))


def _collect_from_expr(node, acc: list[RefUse], line: int) -> None:
    """递归遍历表达式节点，收集其中的 EntityRef（用于赋值与算术上下文）"""
    if isinstance(node, EntityRef):
        _add(acc, node.scene, node.entity, "expr_ref", line)
    elif isinstance(node, SubsceneEntityRef):
        _add_subscene(acc, node, "expr_ref", line)
    elif isinstance(node, ArithOp):
        _collect_from_expr(node.left, acc, line)
        _collect_from_expr(node.right, acc, line)
    # 其他节点类型（VarRef/Literal/FieldAccess/FuncCall）不含 EntityRef，无需递归


def _collect_from_stmt(stmt, acc: list[RefUse]) -> None:
    """从单条语句（含其携带的引用与嵌套体）收集引用"""
    line = getattr(stmt, "line_no", 0)

    if isinstance(stmt, Click):
        target = stmt.target
        if isinstance(target, EntityRef):
            # click_any 先查 region 再查 point 再查 panel
            _add(acc, target.scene, target.entity, "click_target", line)
        elif isinstance(target, SubsceneEntityRef):
            _add_subscene(acc, target, "click_target", line)
        elif isinstance(target, PanelRef):
            _add(acc, target.scene, target.panel, "panel", line)
    elif isinstance(stmt, Move):
        target = stmt.target
        if isinstance(target, EntityRef):
            _add(acc, target.scene, target.entity, "move_target", line)
        elif isinstance(target, SubsceneEntityRef):
            _add_subscene(acc, target, "move_target", line)
        elif isinstance(target, PanelRef):
            _add(acc, target.scene, target.panel, "panel", line)
    elif isinstance(stmt, Scroll):
        target = stmt.target
        if isinstance(target, EntityRef):
            _add(acc, target.scene, target.entity, "scroll_target", line)
        elif isinstance(target, SubsceneEntityRef):
            _add_subscene(acc, target, "scroll_target", line)
        elif isinstance(target, PanelRef):
            _add(acc, target.scene, target.panel, "panel", line)
    elif isinstance(stmt, Drag):
        # scene / arrow 对场景与 panel 目标是同一节点，去重后只留一条
        for ref in (stmt.scene, stmt.arrow):
            if isinstance(ref, EntityRef):
                # drag 的 key 查的是布局 arrows 或 regions
                _add(acc, ref.scene, ref.entity, "drag_target", line)
            elif isinstance(ref, PanelRef):
                _add(acc, ref.scene, ref.panel, "panel", line)
            elif isinstance(ref, PanelGridDrag):
                # panel grid 拖拽也支持 region
                _add(acc, ref.scene, ref.panel, "drag_grid_target", line)
        # 点对模式：drag [scene1].[point1] [scene2].[point2]
        for ref in (stmt.from_scene_ref, stmt.to_scene_ref):
            if isinstance(ref, EntityRef):
                _add(acc, ref.scene, ref.entity, "point", line)
    elif isinstance(stmt, (Scan, Recognize)):
        scene_ref = stmt.scene
        if isinstance(scene_ref, PanelRef):
            _add(acc, scene_ref.scene, scene_ref.panel, "panel", line)
        elif isinstance(scene_ref, EntityRef):
            # fields 为识别的区域 key 列表；无 fields（或动态 region）时为整场景识别
            if stmt.fields:
                # 单一 key 运行时可能分派为整面板识别，放宽为 区域/面板
                kind = "scan" if len(stmt.fields) == 1 else "region"
                for field in stmt.fields:
                    _add(acc, scene_ref.scene, field, kind, line)
            else:
                _add(acc, scene_ref.scene, None, "region", line)
        elif isinstance(scene_ref, SubsceneEntityRef):
            _add_subscene(acc, scene_ref, "scan", line)
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
    elif isinstance(stmt, WaitStable):
        # wait stable on [scene].[entity] — 校验区域绑定
        if stmt.area is not None and isinstance(stmt.area, EntityRef):
            _add(acc, stmt.area.scene, stmt.area.entity, "stable_area", line)

    # 表达式内 EntityRef 收集（$a = [scene].[region] 或 $diff = $b - $a 等）
    if isinstance(stmt, Eval):
        _collect_from_expr(getattr(stmt, 'value', None), acc, line)
        for arg in getattr(stmt, 'func_args', []):
            _collect_from_expr(arg, acc, line)
    elif isinstance(stmt, EvalFieldChainAssign):
        _collect_from_expr(getattr(stmt, 'value', None), acc, line)

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


def _called_procs(body, acc: set[str]) -> None:
    """收集语句体里直接 ``call`` 到的过程名（含嵌套体）。

    ``CallProc.name`` 恒为静态字符串，DSL 没有按变量名调用过程的语法，
    因此调用图可以完全静态求解。
    """
    for stmt in body or []:
        if isinstance(stmt, CallProc):
            acc.add(stmt.name)
        if isinstance(stmt, If):
            _called_procs(stmt.then_body, acc)
            _called_procs(stmt.else_body, acc)
        elif isinstance(stmt, (For, ForRange, Loop, WhileLoop, UntilLoop)):
            _called_procs(stmt.body, acc)
        elif isinstance(stmt, Try):
            _called_procs(stmt.body, acc)
            _called_procs(stmt.catch_body, acc)


def reachable_procs(body: list, procs: dict) -> set[str]:
    """从顶层语句出发，沿 ``call`` 传递闭包求出**会被执行到**的过程名。

    import 是整文件平铺：``import "subcall/navigation.wf"`` 会把该文件（及它
    自己 import 的文件）的全部过程都并进 ``procs``。而这些文件常是函数库
    ——``page_detection.wf`` 就为游戏每个页面各备了一个 ``is_in_*_page()``。
    若把 procs 全量拿去校验，扫装备的脚本会因为「江湖号令页的区域没绑定」
    而被拒绝执行，可那个过程它根本不会调用。

    递归/互相调用靠 visited 收敛；调用了不存在的过程名在此静默跳过，
    那属于另一类错误，有单独的检查负责报。
    """
    pending: set[str] = set()
    _called_procs(body, pending)
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        proc = (procs or {}).get(name)
        if isinstance(proc, ProcDef):
            nested: set[str] = set()
            _called_procs(proc.body, nested)
            pending |= nested - seen
    return seen


def collect_refs(body: list, procs: dict, proc_sources: dict | None = None,
                 source: str = "", reachable_only: bool = True) -> list[RefUse]:
    """收集程序主体与过程体的静态配置引用。

    ``reachable_only`` 决定范围，两种用途要的不是同一个东西：

    - **True（默认，执行前闸门用）**：只走从顶层语句沿 ``call`` 能到达的过程。
      import 按整文件平铺，``page_detection.wf`` 这类函数库会把游戏每个页面
      的判断过程都并进来；全量校验意味着「扫描备战装备」会因为江湖号令活动
      页的区域没绑定而被拒绝执行——而那个过程它根本不会调用。校验失败是
      raise 不是 warning，所以这不只是噪音，是把用户挡在门外。
    - **False（CI 门禁 / 编写期检查用）**：连没被调用的过程一起查，库函数里
      的 key 拼错、布局漏绑才有人发现，不必等到某天真有脚本调用它。

    预检（``validate_only``）用 False、执行用 True，方向是安全的：预检更严
    只会「报了但其实不会炸」，不会出现「预检放过、上机仍炸」。

    Args:
        body: 程序顶层语句列表（Program.body）
        procs: 过程名 -> ProcDef（已合并 import 后的全部过程）
        proc_sources: 过程名 -> 所在文件，缺失时回退到 source
        source: 顶层语句所在文件
        reachable_only: 见上

    Returns:
        引用列表，按出现顺序、同 (scene, key, kind, 行号, 文件) 去重
    """
    acc: list[RefUse] = []
    main: list[RefUse] = []
    _collect_from_body(body, main)
    acc.extend(replace(ref, source=source) for ref in main)
    names = (reachable_procs(body, procs) if reachable_only
             else list((procs or {}).keys()))
    for name in names:
        proc = (procs or {}).get(name)
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
