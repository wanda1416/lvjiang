"""与布局无关的脚本静态检查。

这里收的两类问题都能通过解析和加载，但运行时只会静默失效：

1. **过程内读取未声明名字**。过程的作用域是「形参 + 本过程赋值目标 +
   ``global`` 声明」，其余名字一律读到 null（见 ``engine/data_ops.py`` 的
   ``_run_proc``）。变量名写错、忘了 ``global``，条件会恒假、循环体一次都
   不进，脚本却照常跑完 —— ``daily_jianghu.wf`` 里 ``$min_yinjiu_confidence``
   的注释记录的就是这个坑。
2. **metadata 参数默认值与代码 ``default`` 不一致**。同一个默认值写两份：
   批量入口按 ``#% parameters[].default`` 注入，单跑与调试靠
   ``default $x = ...``。两份漂移后，同一脚本在两种入口下行为不同
   （``purchase_niaoniao.wf`` 的 ``max_scroll`` 曾出现 12 / 2 分裂）。

检查只做能静态确定的部分：字面量默认值、直接写出的变量名。通过函数调用或
运行时拼接得到的值不在范围内。
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from .grammar.ast_nodes import (
    CallProc,
    Eval,
    EvalFieldChainAssign,
    FieldAccess,
    Find,
    For,
    ForRange,
    Global,
    Literal,
    ProcDef,
    Recognize,
    Scan,
    Try,
    VarRef,
)
from .workflow_references import reachable_procs


@dataclass(frozen=True)
class ScopeProblem:
    """过程内读取了一个本作用域不存在的名字。"""

    source: str
    proc: str
    line_no: int
    variable: str

    def describe(self) -> str:
        return (
            f"{self.proc}() 读取了未声明的 ${self.variable}"
            f"（行 {self.line_no}）：它不是形参、没有在本过程赋值、"
            f"也不在 global 声明里，运行时读到的永远是 null"
        )


@dataclass(frozen=True)
class DefaultProblem:
    """metadata 参数默认值与代码 default 语句不一致。"""

    parameter: str
    line_no: int
    metadata_value: Any
    code_value: Any

    def describe(self) -> str:
        return (
            f"${self.parameter} 的默认值两份不一致："
            f"metadata 是 {self.metadata_value!r}，"
            f"代码 default 是 {self.code_value!r}（行 {self.line_no}）"
        )


# ─── 过程作用域 ─────────────────────────────────────────────


def check_proc_scope_reads(
    program,
    procs: dict,
    *,
    proc_sources: dict[str, str] | None = None,
    global_names: set[str] | frozenset[str] = frozenset(),
    reachable_only: bool = True,
    run_env: str = "",
) -> list[ScopeProblem]:
    """找出过程体内读取了作用域外名字的位置。

    ``reachable_only`` 为 True 时只查从顶层 ``call`` 能到达的过程。执行和
    ``validate_only`` 都走这一档：导入库里没被调用的过程，可选的 ``global``
    是调用方的约定，不能算成当前脚本的错误。传 False 只用于把同一文件里
    没人调用的过程也列出来。
    """
    names = (reachable_procs(program.body, procs, run_env)
             if reachable_only else list(procs))
    problems: list[ScopeProblem] = []
    for name in names:
        proc = procs.get(name)
        if not isinstance(proc, ProcDef):
            continue
        allowed = _bound_names(proc.body, proc.params) | set(global_names)
        reported: set[str] = set()
        for variable, line_no in _read_refs(proc.body):
            if variable in allowed or variable in reported:
                continue
            reported.add(variable)
            problems.append(ScopeProblem(
                source=(proc_sources or {}).get(name, getattr(program, "source", "") or ""),
                proc=name,
                line_no=line_no,
                variable=variable,
            ))
    return problems


def collect_global_names(*bodies: list) -> set[str]:
    """收集这些语句体里出现的全部 ``global`` 名字。

    引擎的 ``_global_variable_names`` 是进程级集合：任何文件声明过的名字对
    所有过程可见。静态检查必须按同样口径收集，否则会把合法读取误报成漏声明。
    """
    names: set[str] = set()
    for body in bodies:
        for node in _walk(body):
            if isinstance(node, Global):
                names.update(node.names)
    return names


def _bound_names(body: list, params: list[str]) -> set[str]:
    """过程作用域内能读到的名字：形参 + 一切写入位置 + 本文件的 global。"""
    names = set(params)
    for node in _walk(body):
        if isinstance(node, Eval):
            if node.target:
                names.add(node.target)
        elif isinstance(node, CallProc):
            if node.result_var:
                names.add(node.result_var)
            if node.output_var:
                names.add(node.output_var)
        elif isinstance(node, (For, ForRange)):
            names.add(node.var)
        elif isinstance(node, (Scan, Recognize)):
            if isinstance(node.target, VarRef):
                names.add(node.target.name)
        elif isinstance(node, Find):
            names.add(node.var_name)
        elif isinstance(node, Try):
            if node.err_var:
                names.add(node.err_var)
        elif isinstance(node, Global):
            names.update(node.names)
    return names


def _read_refs(body: list) -> list[tuple[str, int]]:
    """按出现顺序收集读取的变量名与所在语句行号（同名字只保留首次）。"""
    refs: list[tuple[str, int]] = []

    def visit(obj, line_no: int) -> None:
        if isinstance(obj, dict):
            for value in obj.values():
                visit(value, line_no)
            return
        if isinstance(obj, (list, tuple)):
            for item in obj:
                visit(item, line_no)
            return
        if isinstance(obj, VarRef):
            refs.append((obj.name, line_no))
            return
        if not is_dataclass(obj):
            return
        current_line = getattr(obj, "line_no", 0) or line_no
        if isinstance(obj, FieldAccess):
            visit(obj.root, current_line)
            if isinstance(obj.field_name, VarRef):
                visit(obj.field_name, current_line)
            return
        if isinstance(obj, EvalFieldChainAssign):
            # 链根是写入目标；动态键仍是读取。
            _visit_dynamic_keys(obj.target, current_line, visit)
            visit(obj.value, current_line)
            return
        for field in fields(obj):
            if isinstance(obj, (Scan, Recognize)) and field.name == "target":
                continue  # as $var：写入目标
            visit(getattr(obj, field.name), current_line)

    for node in body:
        visit(node, 0)
    seen: set[str] = set()
    unique: list[tuple[str, int]] = []
    for variable, line_no in refs:
        if variable in seen:
            continue
        seen.add(variable)
        unique.append((variable, line_no))
    return unique


def _visit_dynamic_keys(node, line_no: int, visit) -> None:
    while isinstance(node, FieldAccess):
        if isinstance(node.field_name, VarRef):
            visit(node.field_name, line_no)
        node = node.root


def _walk(obj):
    """递归产出所有 AST 节点（忽略动态键以外的引用细节）。"""
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            yield from _walk(item)
        return
    if not is_dataclass(obj):
        return
    yield obj
    for field in fields(obj):
        yield from _walk(getattr(obj, field.name))


# ─── metadata 默认值 ───────────────────────────────────────

_UNKNOWN = object()


def check_metadata_defaults(metadata: dict, program) -> list[DefaultProblem]:
    """比对 ``#% parameters[].default`` 与顶层 ``default $x = <字面量>``。"""
    declared: dict[str, Any] = {}
    for item in metadata.get("parameters") or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        if "default" in item:
            declared[str(item["name"])] = item["default"]

    problems: list[DefaultProblem] = []
    for stmt in program.body:
        if not isinstance(stmt, Eval) or stmt.func_name != "__default__":
            continue
        if not stmt.target or stmt.target not in declared:
            continue
        code_value = _literal_value(stmt.func_args[0] if stmt.func_args else None)
        if code_value is _UNKNOWN:
            continue
        meta_value = declared[stmt.target]
        if not _same_default(meta_value, code_value):
            problems.append(DefaultProblem(
                parameter=stmt.target,
                line_no=stmt.line_no,
                metadata_value=meta_value,
                code_value=code_value,
            ))
    return problems


def _looks_block(value: Any) -> bool:
    """metadata 用块写法（``|-`` 多行）时文本无法与代码单行默认值比较。"""
    return isinstance(value, str) and "\n" in value.strip("\r\n")


def _literal_value(node) -> Any:
    """把 default 右侧还原成 Python 字面量；非字面量返回 ``_UNKNOWN``。"""
    if node is None:
        return _UNKNOWN
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, (bool, int, float, str)):
        return node
    return _UNKNOWN


def _normalize_text(value: str) -> str:
    """去掉 YAML ``|-`` 与手写引号造成的书写差异。"""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text


def _same_default(meta_value: Any, code_value: Any) -> bool:
    if _looks_block(meta_value):
        return True  # 块写法交给人工核对，不做文本比对
    meta_is_bool = isinstance(meta_value, bool)
    code_is_bool = isinstance(code_value, bool)
    if meta_is_bool or code_is_bool:
        return meta_is_bool and code_is_bool and meta_value is code_value
    if isinstance(meta_value, (int, float)) and isinstance(code_value, (int, float)):
        return float(meta_value) == float(code_value)
    if isinstance(meta_value, str) and isinstance(code_value, str):
        return _normalize_text(meta_value) == _normalize_text(code_value)
    return False
