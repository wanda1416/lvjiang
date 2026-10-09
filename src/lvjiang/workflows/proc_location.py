"""把编辑器里的 call 过程名解析到 def 所在文件和行。

位置来自解析器写在 CallProc / ProcDef 上的过程名 token，不扫描源码文本。
当前文件的过程优先于 import。import 路径相对 workflows 根，并按配置层选择
生效文件。解析失败、未定义或光标不在过程名上时返回空。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.config.resolver import get_resolver
from .engine.core import WorkflowUserError, _normalize_import_path
from .file_tree import WORKFLOWS_DIR
from .grammar import parse_text
from .grammar.ast_nodes import (
    CallProc,
    For,
    ForRange,
    If,
    Loop,
    ProcDef,
    Try,
    UntilLoop,
    WhileLoop,
)


@dataclass(frozen=True)
class ProcDefinition:
    """一处过程定义。``path`` 为空表示定义就在正在编辑、尚未对应磁盘文件的文本里。"""

    name: str
    line: int
    path: Path | None = None
    rel_path: str = ""


@dataclass(frozen=True)
class ProcCallSite:
    """当前文本里一处可跳转的 call。列范围是该行内过程名的半开区间，从 0 开始。"""

    line: int
    start: int
    end: int
    definition: ProcDefinition


def index_proc_calls(source: str, origin: Path | None = None) -> dict[int, ProcCallSite]:
    """索引源文本中每一处 call 的过程名位置和定义。行号、列号都从 0 开始。"""
    try:
        program = parse_text(source, source=str(origin) if origin else "<editor>")
    except Exception:
        return {}
    calls = [
        stmt for stmt in _iter_calls(program.body)
        if isinstance(stmt, CallProc)
    ]
    for proc in program.procs.values():
        if isinstance(proc, ProcDef):
            calls.extend(stmt for stmt in _iter_calls(proc.body) if isinstance(stmt, CallProc))
    if not calls:
        return {}
    try:
        procs, sources = _load_definitions(program, origin)
    except Exception:
        return {}
    indexed: dict[int, ProcCallSite] = {}
    for stmt in calls:
        span = _name_span(stmt)
        if span is None:
            continue
        definition = _definition_at(stmt.name, procs, sources)
        if definition is None:
            continue
        line, start, end = span
        indexed[line] = ProcCallSite(
            line=line, start=start, end=end, definition=definition)
    return indexed


def _name_span(stmt: CallProc) -> tuple[int, int, int] | None:
    """返回 0-based 行号和半开列区间。没有 token 位置的节点不能标下划线。"""
    line_no = stmt.name_line or stmt.line_no
    if line_no <= 0 or stmt.name_col <= 0 or stmt.name_end_col <= stmt.name_col:
        return None
    return line_no - 1, stmt.name_col - 1, stmt.name_end_col - 1


def _definition_at(
    name: str,
    procs: dict[str, ProcDef],
    sources: dict[str, Path | None],
) -> ProcDefinition | None:
    proc = procs.get(name)
    if not isinstance(proc, ProcDef) or proc.line_no <= 0:
        return None
    path = sources.get(name)
    return ProcDefinition(
        name=name,
        line=proc.line_no,
        path=path,
        rel_path=_workflows_rel(path),
    )


def _workflows_rel(path: Path | None) -> str:
    if path is None:
        return ""
    resolved = path.resolve()
    resolver = get_resolver()
    for root in (resolver.local_dir, resolver.remote_dir, resolver.system_dir):
        base = (root / WORKFLOWS_DIR).resolve()
        try:
            return resolved.relative_to(base).as_posix()
        except (ValueError, OSError):
            continue
    return ""


def _load_definitions(program, origin: Path | None):
    """当前文件优先，随后按 import 图登记最先出现的过程。"""
    procs: dict[str, ProcDef] = {
        name: proc for name, proc in program.procs.items() if isinstance(proc, ProcDef)
    }
    origin_path = origin.resolve() if origin is not None else None
    sources: dict[str, Path | None] = {name: origin_path for name in procs}
    visited: set[Path] = set()
    if origin_path is not None:
        visited.add(origin_path)

    def visit(current, stack: tuple[Path, ...]) -> None:
        for imp in current.imports:
            try:
                rel = _normalize_import_path(imp.path)
                resolved = get_resolver().resolve_read(f"{WORKFLOWS_DIR}/{rel}")
                if resolved is None:
                    continue
                path = Path(resolved).resolve()
                if path in stack or path in visited:
                    continue
                text = path.read_text(encoding="utf-8-sig")
                imported = parse_text(text, source=str(path))
            except (OSError, UnicodeError, WorkflowUserError):
                continue
            except Exception:
                continue
            visited.add(path)
            for name, proc in imported.procs.items():
                if isinstance(proc, ProcDef) and name not in procs:
                    procs[name] = proc
                    sources[name] = path
            visit(imported, (*stack, path))

    visit(program, (origin_path,) if origin_path is not None else ())
    return procs, sources


def _iter_calls(stmts: list):
    for stmt in stmts or []:
        if isinstance(stmt, CallProc):
            yield stmt
        bodies: list[list] = []
        if isinstance(stmt, If):
            bodies = [stmt.then_body, stmt.else_body]
        elif isinstance(stmt, (For, ForRange, Loop, WhileLoop, UntilLoop, Try)):
            bodies = [stmt.body]
            if isinstance(stmt, Try):
                bodies.append(stmt.catch_body)
        for body in bodies:
            yield from _iter_calls(body)
