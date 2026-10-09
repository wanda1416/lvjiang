"""LvJiang Workflow DSL Language Server

Provides real-time diagnostics for .wf files by reusing the project's
existing Lark-based parser.

Syntax diagnostics and suggestions at actual parse errors; project-aware import,
call, metadata, scene and optional layout checks; navigation and completion.
"""
from __future__ import annotations

import os
import re
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlparse
from urllib.request import url2pathname


def _die(msg: str) -> NoReturn:
    """Print a diagnosable message to stderr (visible in the editor's Output panel
    for this server) and exit, instead of letting a bare ImportError produce an
    opaque crash that the editor reports as a generic "server exited" error."""
    sys.stderr.write(f"[lvjiang-wf-server] {msg}\n")
    sys.stderr.flush()
    sys.exit(1)


try:
    from lsprotocol.types import (
        TEXT_DOCUMENT_COMPLETION,
        TEXT_DOCUMENT_DEFINITION,
        TEXT_DOCUMENT_DID_CHANGE,
        TEXT_DOCUMENT_DID_CLOSE,
        TEXT_DOCUMENT_DID_OPEN,
        TEXT_DOCUMENT_DID_SAVE,
        TEXT_DOCUMENT_DOCUMENT_SYMBOL,
        TEXT_DOCUMENT_FOLDING_RANGE,
        TEXT_DOCUMENT_HOVER,
        TEXT_DOCUMENT_REFERENCES,
        TEXT_DOCUMENT_RENAME,
        CompletionItem,
        CompletionItemKind,
        CompletionParams,
        DefinitionParams,
        Diagnostic,
        DiagnosticSeverity,
        DidChangeTextDocumentParams,
        DidCloseTextDocumentParams,
        DidOpenTextDocumentParams,
        DidSaveTextDocumentParams,
        DocumentSymbol,
        DocumentSymbolParams,
        FoldingRange,
        FoldingRangeKind,
        FoldingRangeParams,
        Hover,
        HoverParams,
        Location,
        MarkupContent,
        MarkupKind,
        Position,
        Range,
        ReferenceContext,
        ReferenceParams,
        RenameParams,
        SymbolKind,
        TextEdit,
        WorkspaceEdit,
    )
    from pygls.server import LanguageServer
except ImportError as e:
    _die(
        f"missing dependency ({e}). This interpreter ({sys.executable}) doesn't have "
        f"'pygls'/'lsprotocol' installed. Run `pip install -e \".[dev]\"` in the project's "
        f".venv, or point the \"lvjiangWf.pythonPath\" setting at that .venv's interpreter."
    )

# ---------------------------------------------------------------------------
# Bootstrap: make project source importable
# ---------------------------------------------------------------------------
# server/ is at editors/vscode/lvjiang-wf/server/
# project root is 5 levels up from this file.
_project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
_src_dir = _project_root / "src"
if _src_dir.is_dir() and str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

try:
    from lark.exceptions import (  # noqa: E402
        LarkError,
        UnexpectedCharacters,
        UnexpectedToken,
    )

    from lvjiang.core.config.resolver import get_resolver  # noqa: E402
    from lvjiang.workflows.engine.core import _normalize_import_path  # noqa: E402
    from lvjiang.workflows.grammar import parse_text  # noqa: E402
    from lvjiang.workflows.grammar.ast_nodes import (  # noqa: E402
        CallProc,
        For,
        ForRange,
        If,
        Loop,
        ProcDef,
        Program,
        Try,
        UntilLoop,
        WhileLoop,
    )
    from lvjiang.workflows.metadata import metadata_error  # noqa: E402
    from lvjiang.workflows.workflow_references import collect_refs  # noqa: E402
except ImportError as e:
    _die(
        f"cannot import 'lvjiang' ({e}). Expected the project source at {_src_dir} "
        f"(resolved from {Path(__file__).resolve()}). If this extension was installed "
        f"as a standalone copy rather than the install.bat junction into the project "
        f"checkout, that path resolution will be wrong."
    )

server = LanguageServer("lvjiang-wf-server", "v0.1")

# ---------------------------------------------------------------------------
# Buffer parse cache
# ---------------------------------------------------------------------------

# 同一份缓冲区文本会被诊断、跳转、补全和查找引用分别解析；DSL 解析成本很高
# （1300 行脚本单次约 7s），必须复用结果。缓存只保存语法树，对外返回深拷贝，
# 避免调用方共享其中的可变列表或字典（与 lvjiang.workflows.grammar.parser
# 的 parse_file 采用同一约定）。
_PARSE_CACHE_SIZE = 4


@lru_cache(maxsize=_PARSE_CACHE_SIZE)
def _parse_snapshot(source: str) -> Program:
    return parse_text(source)


def _parse_cached(source: str) -> Program:
    """Parse *source* once per distinct buffer text."""
    return deepcopy(_parse_snapshot(source))


# ---------------------------------------------------------------------------
# DSL keywords for typo detection
# ---------------------------------------------------------------------------
DSL_KEYWORDS = {
    "scene", "view", "unknown",
    # Control flow
    "main", "def", "return", "call", "try", "catch",
    # Actions
    "tap", "wait", "drag", "ocr", "find", "screenshot", "scan", "recognize", "collect", "log", "align",
    # Control structures
    "env", "if", "elif", "else", "while", "for", "in", "break", "continue", "end",
    # Boolean/null
    "true", "false", "null",
    # Logical operators
    "and", "or", "not",
    # Timing
    "before", "after", "around",
    # wait stable
    "stable", "threshold", "interval", "duration", "least",
    # Match patterns
    "equals", "contains", "equals_any", "contains_any",
    # Clause keywords
    "where", "on", "group", "hold", "session", "context", "as", "by",
    # Special
    "this", "error", "import", "loop", "until",
}


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _find_closest_keyword(name: str) -> str | None:
    """Find the closest keyword to the given name, if within edit distance 2."""
    best_match = None
    best_distance = 3
    for kw in DSL_KEYWORDS:
        dist = _levenshtein_distance(name, kw)
        if dist < best_distance:
            best_distance = dist
            best_match = kw
    return best_match if best_distance <= 2 else None


# ---------------------------------------------------------------------------
# Utility: URI ↔ path, line range, AST walking
# ---------------------------------------------------------------------------

def _uri_to_path(uri: str) -> Path:
    """Convert a file URI to a Path (handles Windows drive letters correctly).

    The VS Code client percent-encodes the drive colon (``file:///c%3A/...``).
    ``url2pathname`` maps that form to the rooted-but-driveless ``\\c:\\...``;
    its ``resolve()`` result is drive-relative, so ``Path.as_uri()`` raises
    ``ValueError: relative path can't be expressed as a file URI``. Restore the
    drive letter so client URIs keep resolving to absolute local paths.
    """
    path = url2pathname(urlparse(uri).path)
    if os.name == "nt":
        drive = re.match(r"^[\\/]([A-Za-z]):", path)
        if drive:
            path = f"{drive.group(1)}:{path[drive.end():]}"
    return Path(path)


def _line_range(line_no: int, end_line: int | None = None) -> Range:
    """Create a Range for the given 1-based line number (or 0 if unknown)."""
    line = max(line_no - 1, 0) if line_no else 0
    end = end_line if end_line is not None else line
    return Range(
        start=Position(line=line, character=0),
        end=Position(line=end, character=0 if end != line else 1),
    )


def _walk_stmts(body: list) -> list:
    """Recursively collect all statements from a body, including nested blocks."""
    result = []
    for stmt in body or []:
        result.append(stmt)
        if isinstance(stmt, If):
            result.extend(_walk_stmts(stmt.then_body))
            result.extend(_walk_stmts(stmt.else_body))
        elif isinstance(stmt, (For, ForRange, Loop, WhileLoop, UntilLoop)):
            result.extend(_walk_stmts(stmt.body))
        elif isinstance(stmt, Try):
            result.extend(_walk_stmts(stmt.body))
            result.extend(_walk_stmts(stmt.catch_body))
    return result


# ---------------------------------------------------------------------------
# Scene registry (lazy, cached with mtime invalidation)
# ---------------------------------------------------------------------------

_scene_registry = None
_scene_registry_error: str | None = None
_scene_stamp: tuple[tuple[str, int], ...] = ()


def _scene_files_stamp() -> tuple[tuple[str, int], ...]:
    """Detect edits made outside the app's config change notification path."""
    resolver = get_resolver()
    files = []
    for root in (resolver.system_dir, resolver.remote_dir, resolver.local_dir):
        files.extend(root.glob("scenes.yaml"))
        files.extend((root / "scenes").glob("*.yaml"))
    return tuple(sorted((str(path), path.stat().st_mtime_ns) for path in files))


def _get_scene_registry():
    """Load the scene registry and refresh it after external YAML edits."""
    global _scene_registry, _scene_registry_error, _scene_stamp
    current_stamp = _scene_files_stamp()
    if _scene_stamp and current_stamp != _scene_stamp:
        _scene_registry = None
        _scene_registry_error = None
    if _scene_registry is not None or _scene_registry_error is not None:
        return _scene_registry
    try:
        from lvjiang.core.scene_registry import get_registry, reload_scene_registry
        if _scene_stamp and current_stamp != _scene_stamp:
            reload_scene_registry()
        _scene_registry = get_registry()
        _scene_stamp = current_stamp
    except Exception as e:
        _scene_registry_error = str(e)
        _scene_stamp = current_stamp
    return _scene_registry


# ---------------------------------------------------------------------------
# Level 3: Semantic checks
# ---------------------------------------------------------------------------

def _check_scene_exists(program) -> list[Diagnostic]:
    """Check that all scene references point to existing scenes."""
    registry = _get_scene_registry()
    if registry is None:
        return []
    diagnostics = []
    from lvjiang.workflows.scene_declarations import validate_scene_declarations
    for line, message in validate_scene_declarations(program, registry.all_scenes()):
        diagnostics.append(Diagnostic(range=_line_range(line), message=message,
                                      severity=DiagnosticSeverity.Warning))
    valid_scenes = set(registry.all_scene_keys())
    refs = collect_refs(program.body, program.procs,
                        source=program.source, reachable_only=False)
    seen_scenes: set[str] = set()
    for ref in refs:
        if ref.scene in seen_scenes:
            continue
        if ref.scene not in valid_scenes:
            seen_scenes.add(ref.scene)
            diagnostics.append(Diagnostic(
                range=_line_range(ref.line_no),
                message=f"场景 '{ref.scene}' 不存在于 scenes.yaml 中",
                severity=DiagnosticSeverity.Error,
            ))
    return diagnostics


def _check_layout_refs(program) -> list[Diagnostic]:
    """Validate only when the editor explicitly knows the intended layout."""
    layout_key = os.environ.get("LVJIANG_WF_LAYOUT_KEY", "").strip()
    if not layout_key:
        return []
    from lvjiang.core.layout_manager import load_layout_by_key
    from lvjiang.workflows.static_check import check_refs

    layout = load_layout_by_key(layout_key)
    if layout is None:
        return [Diagnostic(
            range=_line_range(1), message=f"布局不存在: {layout_key}",
            severity=DiagnosticSeverity.Warning)]
    refs = collect_refs(program.body, program.procs, source=program.source,
                        reachable_only=False)
    return [Diagnostic(range=_line_range(problem.ref.line_no),
                       message=f"[{problem.ref.scene}].[{problem.ref.key}]: {problem.reason}",
                       severity=DiagnosticSeverity.Warning)
            for problem in check_refs(refs, layout)]


def _all_call_stmts(program) -> list:
    """Collect all CallProc statements from main body and all proc bodies."""
    stmts = _walk_stmts(program.body)
    for proc in program.procs.values():
        if isinstance(proc, ProcDef):
            stmts.extend(_walk_stmts(proc.body))
    return stmts


def _check_call_exists(program, procs: dict | None = None) -> list[Diagnostic]:
    """Check that all call targets are defined procedures."""
    diagnostics = []
    defined = set(procs if procs is not None else program.procs)
    for stmt in _all_call_stmts(program):
        if isinstance(stmt, CallProc) and stmt.name not in defined:
            diagnostics.append(Diagnostic(
                range=_line_range(stmt.line_no),
                message=f"过程 '{stmt.name}' 未定义",
                severity=DiagnosticSeverity.Error,
            ))
    return diagnostics


_import_cache: dict[Path, tuple[int, int, object]] = {}
_open_documents: dict[Path, tuple[str, str]] = {}


def _parse_imported(path: Path):
    """Avoid reparsing an unchanged import graph on every keystroke."""
    from lvjiang.workflows.grammar import parse_file

    open_doc = _open_documents.get(path)
    if open_doc is not None:
        text = open_doc[1]
        signature = (len(text), hash(text))
        cached = _import_cache.get(path)
        if cached and cached[:2] == signature:
            return cached[2]
        parsed = parse_text(text, source=str(path))
        _import_cache[path] = (*signature, parsed)
        return parsed
    stat = path.stat()
    cached = _import_cache.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    parsed = parse_file(path)
    _import_cache[path] = (stat.st_mtime_ns, stat.st_size, parsed)
    return parsed


def _load_import_graph(program, source_path: Path) -> tuple[dict, dict, list[Diagnostic]]:
    """Resolve the import graph with the engine's path and layer rules."""
    diagnostics: list[Diagnostic] = []
    procs = dict(program.procs)
    sources = {name: source_path for name in procs}
    visited: set[Path] = set()
    resolver = get_resolver()

    def visit(current, stack: tuple[Path, ...], root_line: int = 0) -> None:
        for imp in current.imports:
            diagnostic_line = root_line or imp.line_no
            try:
                rel = _normalize_import_path(imp.path)
                resolved = resolver.resolve_read(f"workflows/{rel}")
                if resolved is None:
                    raise ValueError(f"导入文件不存在: {imp.path}")
                path = resolved.resolve()
                if path in stack:
                    raise ValueError(f"循环 import: {imp.path}")
                if path in visited:
                    continue
                imported = _parse_imported(path)
            except Exception as exc:
                diagnostics.append(Diagnostic(
                    range=_line_range(diagnostic_line),
                    message=f"{stack[-1].name}:{imp.line_no}: {exc}",
                    severity=DiagnosticSeverity.Error))
                continue
            visited.add(path)
            for name, proc in imported.procs.items():
                if name in procs:
                    diagnostics.append(Diagnostic(
                        range=_line_range(diagnostic_line),
                        message=f"过程 '{name}' 与 {sources[name]} 中的定义重复",
                        severity=DiagnosticSeverity.Error))
                else:
                    procs[name] = proc
                    sources[name] = path
            visit(imported, (*stack, path), diagnostic_line)

    visit(program, (source_path.resolve(),))
    return procs, sources, diagnostics


def _check_proc_param_count(program, procs: dict | None = None) -> list[Diagnostic]:
    """Check that call arguments match the procedure definition."""
    diagnostics = []
    for stmt in _all_call_stmts(program):
        if isinstance(stmt, CallProc):
            proc_def = (procs if procs is not None else program.procs).get(stmt.name)
            if proc_def and len(stmt.args) != len(proc_def.params):
                expected = len(proc_def.params)
                actual = len(stmt.args)
                diagnostics.append(Diagnostic(
                    range=_line_range(stmt.line_no),
                    message=f"过程 '{stmt.name}' 期望 {expected} 个参数，实际传入 {actual} 个",
                    severity=DiagnosticSeverity.Error,
                ))
    return diagnostics


def _validate_and_publish(uri: str, source: str) -> None:
    """Parse *source* and publish diagnostics to the client."""
    diagnostics: list[Diagnostic] = []
    source_path = _uri_to_path(uri)

    meta_problem = metadata_error(source)
    if meta_problem:
        diagnostics.append(Diagnostic(
            range=_line_range(1), message=meta_problem,
            severity=DiagnosticSeverity.Error))

    try:
        program = _parse_cached(source)
        # Semantic checks use the parsed program; valid identifiers are never
        # treated as keyword typos merely because their spelling is similar.
        procs, _, import_problems = _load_import_graph(program, source_path)
        diagnostics.extend(import_problems)
        diagnostics.extend(_check_scene_exists(program))
        diagnostics.extend(_check_layout_refs(program))
        diagnostics.extend(_check_call_exists(program, procs))
        diagnostics.extend(_check_proc_param_count(program, procs))
    except (UnexpectedCharacters, UnexpectedToken) as e:
        # Lark provides 1-based line/column; LSP uses 0-based.
        line = max(getattr(e, "line", 1) - 1, 0)
        col = max(getattr(e, "column", 1) - 1, 0)
        # Clamp end column to the actual line length.
        lines = source.splitlines()
        line_len = len(lines[line]) if line < len(lines) else col + 1
        end_col = min(col + 20, line_len)

        # Try to extract the problematic token and check for typos
        error_msg = str(e)
        # Look for the actual text at the error position
        if line < len(lines):
            line_text = lines[line]
            # Extract word at error position
            word_start = col
            word_end = col
            while word_end < len(line_text) and (line_text[word_end].isalnum() or line_text[word_end] == '_'):
                word_end += 1
            if word_end > word_start:
                token = line_text[word_start:word_end]
                closest = _find_closest_keyword(token)
                if closest:
                    error_msg = f"'{token}' 未识别。你是不是想写 '{closest}'？"

        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=line, character=col),
                    end=Position(line=line, character=max(end_col, col + 1)),
                ),
                message=error_msg,
                severity=DiagnosticSeverity.Error,
            )
        )
    except LarkError as e:
        # Fallback for other Lark errors without precise location.
        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=0, character=0),
                    end=Position(line=0, character=1),
                ),
                message=str(e),
                severity=DiagnosticSeverity.Error,
            )
        )
    except Exception as e:
        # Catch-all for Transformer errors or other unexpected failures.
        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=0, character=0),
                    end=Position(line=0, character=1),
                ),
                message=f"Internal parser error: {e}",
                severity=DiagnosticSeverity.Warning,
            )
        )

    server.publish_diagnostics(uri, diagnostics)


# ---------------------------------------------------------------------------
# Document lifecycle hooks
# ---------------------------------------------------------------------------

@server.feature(TEXT_DOCUMENT_DID_OPEN)
def on_open(params: DidOpenTextDocumentParams) -> None:
    uri = params.text_document.uri
    _open_documents[_uri_to_path(uri).resolve()] = (uri, params.text_document.text)
    _validate_open_documents()


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def on_save(params: DidSaveTextDocumentParams) -> None:
    doc = server.workspace.get_text_document(params.text_document.uri)
    _open_documents[_uri_to_path(params.text_document.uri).resolve()] = (
        params.text_document.uri, doc.source)
    _validate_open_documents()


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def on_change(params: DidChangeTextDocumentParams) -> None:
    doc = server.workspace.get_text_document(params.text_document.uri)
    _open_documents[_uri_to_path(params.text_document.uri).resolve()] = (
        params.text_document.uri, doc.source)
    _validate_open_documents()


@server.feature(TEXT_DOCUMENT_DID_CLOSE)
def on_close(params: DidCloseTextDocumentParams) -> None:
    path = _uri_to_path(params.text_document.uri).resolve()
    _open_documents.pop(path, None)
    _import_cache.pop(path, None)
    server.publish_diagnostics(params.text_document.uri, [])
    _validate_open_documents()


def _validate_open_documents() -> None:
    """Refresh importers when an unsaved dependency changes."""
    for uri, source in _open_documents.values():
        _validate_and_publish(uri, source)


# ---------------------------------------------------------------------------
# Level 3: Editor features — folding, symbols, hover
# ---------------------------------------------------------------------------

def _collect_block_ranges(body: list, result: list[FoldingRange],
                          source_lines: list[str] | None = None) -> None:
    """Recursively collect folding ranges from block statements."""
    for stmt in body or []:
        children_bodies: list[list] = []
        if isinstance(stmt, If):
            children_bodies = [stmt.then_body, stmt.else_body]
        elif isinstance(stmt, (For, ForRange, Loop, WhileLoop, UntilLoop)):
            children_bodies = [stmt.body]
        elif isinstance(stmt, Try):
            children_bodies = [stmt.body, stmt.catch_body]
        elif isinstance(stmt, ProcDef):
            children_bodies = [stmt.body]

        if not children_bodies:
            continue

        # Determine start line
        if isinstance(stmt, ProcDef):
            # ProcDef has no line_no; find 'def' line from source text
            start_line = _find_def_line(stmt.name, source_lines or [])
            if start_line < 0:
                continue
            start_line += 1  # Convert to 1-based
        else:
            start_line = getattr(stmt, "line_no", 0)
            if start_line <= 0:
                continue

        # env:"..." -> statement 会复用 If AST，但源文本没有 end，不是可折叠块。
        if (isinstance(stmt, If) and source_lines
                and re.match(r"\s*env\s*:", source_lines[start_line - 1], re.I)):
            continue

        # Find the max line_no in all nested statements, then +1 for 'end'
        all_stmts = []
        for cb in children_bodies:
            all_stmts.extend(_walk_stmts(cb))
        end_line = start_line
        if all_stmts:
            end_line = max(getattr(s, "line_no", 0) for s in all_stmts)
        # Try to find the actual 'end' keyword line
        if source_lines and end_line > 0:
            end_keyword = _find_end_line(end_line, source_lines)
            if end_keyword > end_line:
                end_line = end_keyword
        if end_line > start_line:
            result.append(FoldingRange(
                start_line=start_line - 1,  # 0-based
                end_line=end_line - 1,      # 0-based
                kind=FoldingRangeKind.Region,
            ))
        for cb in children_bodies:
            _collect_block_ranges(cb, result, source_lines)


def _find_def_line(name: str, lines: list[str]) -> int:
    """Find the line index of 'def name(' in source lines. Returns 0-based or -1."""
    pattern = re.compile(rf"^def\s+{re.escape(name)}\s*\(")
    for i, line_text in enumerate(lines):
        if pattern.match(line_text.strip()):
            return i
    return -1


def _find_end_line(after_1based: int, lines: list[str]) -> int:
    """Find the 'end' keyword line after the given 1-based line. Returns 1-based."""
    for i in range(after_1based, len(lines)):
        if lines[i].strip() == "end":
            return i + 1  # Convert to 1-based
    return after_1based


@server.feature(TEXT_DOCUMENT_FOLDING_RANGE)
def on_folding(params: FoldingRangeParams) -> list[FoldingRange]:
    """Provide folding ranges for def/end, if/end, loop/end, try/end blocks."""
    doc = server.workspace.get_text_document(params.text_document.uri)
    try:
        program = _parse_cached(doc.source)
    except Exception:
        return []
    lines = doc.source.splitlines()
    ranges: list[FoldingRange] = []
    # Collect from main body
    _collect_block_ranges(program.body, ranges, lines)
    # Collect from proc definitions
    for proc in program.procs.values():
        if isinstance(proc, ProcDef):
            _collect_block_ranges([proc], ranges, lines)
    return ranges


# Block-opening keywords for depth tracking in document symbols
_BLOCK_OPENERS = ("def ", "def(", "if ", "while ", "for ", "loop ", "loop\t",
                  "try ", "try\t", "elif ", "elif\t")


@server.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)
def on_document_symbol(params: DocumentSymbolParams) -> list[DocumentSymbol]:
    """Provide document symbols for Outline panel (all def definitions)."""
    doc = server.workspace.get_text_document(params.text_document.uri)
    try:
        program = _parse_cached(doc.source)
    except Exception:
        return []
    symbols: list[DocumentSymbol] = []
    lines = doc.source.splitlines()
    for name, proc in program.procs.items():
        if not isinstance(proc, ProcDef):
            continue
        # Find the 'def' line using regex for exact match
        def_line = _find_def_line(name, lines)
        if def_line < 0:
            def_line = 0
        # Find the matching 'end' line using depth tracking
        end_line = def_line
        depth = 1
        for i in range(def_line + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped == "end":
                depth -= 1
                if depth == 0:
                    end_line = i
                    break
            elif (any(stripped == kw.rstrip() for kw in _BLOCK_OPENERS) or
                  any(stripped.startswith(kw) for kw in _BLOCK_OPENERS)):
                depth += 1
        symbols.append(DocumentSymbol(
            name=name,
            kind=SymbolKind.Function,
            range=Range(
                start=Position(line=def_line, character=0),
                end=Position(line=end_line, character=0),
            ),
            selection_range=Range(
                start=Position(line=def_line, character=0),
                end=Position(line=def_line, character=len(lines[def_line]) if def_line < len(lines) else 0),
            ),
        ))
    return symbols


@server.feature(TEXT_DOCUMENT_HOVER)
def on_hover(params: HoverParams) -> Hover | None:
    """Show hover information for procedure names and scene references."""
    doc = server.workspace.get_text_document(params.text_document.uri)
    try:
        program = _parse_cached(doc.source)
    except Exception:
        return None
    line = params.position.line
    col = params.position.character
    lines = doc.source.splitlines()
    if line >= len(lines):
        return None
    line_text = lines[line]
    # Extract word at cursor position
    word_start = col
    word_end = col
    while word_start > 0 and (line_text[word_start - 1].isalnum() or line_text[word_start - 1] == '_'):
        word_start -= 1
    while word_end < len(line_text) and (line_text[word_end].isalnum() or line_text[word_end] == '_'):
        word_end += 1
    word = line_text[word_start:word_end]
    if not word:
        return None
    # Check if it's a procedure name
    procs, _, _ = _load_import_graph(program, _uri_to_path(params.text_document.uri))
    if word in procs:
        proc = procs[word]
        if isinstance(proc, ProcDef):
            params_str = ", ".join(f"${p}" for p in proc.params) if proc.params else ""
            return Hover(
                contents=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=f"**def** `{word}({params_str})`\n\n过程定义，共 {len(proc.body)} 条语句",
                ),
            )
    # Check if it's a DSL keyword
    if word in DSL_KEYWORDS:
        return Hover(
            contents=MarkupContent(
                kind=MarkupKind.Markdown,
                value=f"**关键字** `{word}`",
            ),
        )
    return None


@server.feature(TEXT_DOCUMENT_COMPLETION)
def on_completion(params: CompletionParams) -> list[CompletionItem]:
    """Offer keys only where the current statement gives them a clear meaning."""
    doc = server.workspace.get_text_document(params.text_document.uri)
    lines = doc.source.splitlines()
    if params.position.line >= len(lines):
        return []
    prefix = lines[params.position.line][:params.position.character]
    import_match = re.search(r'\bimport\s+"([^"\n]*)$', prefix)
    if import_match:
        from lvjiang.workflows.file_tree import list_workflow_files
        edit_range = Range(
            start=Position(line=params.position.line,
                           character=import_match.start(1)),
            end=Position(line=params.position.line,
                         character=params.position.character))
        return [CompletionItem(label=item.rel_path, kind=CompletionItemKind.File,
                               text_edit=TextEdit(range=edit_range,
                                                  new_text=item.rel_path))
                for item in list_workflow_files()]
    registry = _get_scene_registry()
    entity = re.search(r'\[([A-Za-z_][A-Za-z_0-9]*)\]\.\[[A-Za-z_0-9]*$', prefix)
    if entity and registry is not None:
        scene = registry.get_scene(entity.group(1))
        if scene is None:
            return []
        keys = {item.key for group in (scene.regions, scene.points,
                                       scene.arrows, scene.panels)
                for item in group}
        return [CompletionItem(label=key, kind=CompletionItemKind.Field)
                for key in sorted(keys)]
    if re.search(r'\[[A-Za-z_0-9]*$', prefix) and registry is not None:
        return [CompletionItem(label=key, kind=CompletionItemKind.Class,
                               detail="场景 key")
                for key in registry.all_scene_keys()]
    if re.search(r'\b(?:eval\s+\$\w+\s*=|\$\w+\s*=)\s*\w*$', prefix):
        from lvjiang.workflows.builtins import list_functions
        return [CompletionItem(label=name, kind=CompletionItemKind.Function,
                               detail="内置函数")
                for name in sorted(list_functions())]
    if not re.search(r'\bcall\s+(?:\$\w+\s*=\s*)?\w*$', prefix):
        return []
    try:
        program = _parse_for_editing(doc.source, params.position.line)
        procs, _, _ = _load_import_graph(program, _uri_to_path(params.text_document.uri))
    except Exception:
        return []
    return [CompletionItem(label=name, kind=CompletionItemKind.Function,
                            detail=f"def {name}({', '.join('$' + p for p in proc.params)})")
             for name, proc in sorted(procs.items())]


def _parse_for_editing(source: str, active_line: int):
    """Keep completions available while the current statement is incomplete."""
    try:
        return _parse_cached(source)
    except Exception:
        lines = source.splitlines(keepends=True)
        if active_line >= len(lines):
            raise
        lines[active_line] = "# editing\n"
        return _parse_cached("".join(lines))


@server.feature(TEXT_DOCUMENT_DEFINITION)
def on_definition(params: DefinitionParams) -> Location | None:
    """Navigate imports and procedure calls to their effective source files."""
    doc = server.workspace.get_text_document(params.text_document.uri)
    lines = doc.source.splitlines()
    line_no = params.position.line
    if line_no >= len(lines):
        return None
    line = lines[line_no]
    col = params.position.character
    imported = re.search(r'\bimport\s+"([^"\n]+)"', line)
    if imported and imported.start(1) <= col <= imported.end(1):
        try:
            rel = _normalize_import_path(imported.group(1))
            path = get_resolver().resolve_read(f"workflows/{rel}")
        except Exception:
            return None
        if path is None:
            return None
        pos = Position(line=0, character=0)
        return Location(uri=path.resolve().as_uri(), range=Range(start=pos, end=pos))
    scene_ref = next((m for m in re.finditer(
        r'\[([A-Za-z_][A-Za-z_0-9]*)\](?:\.\[([A-Za-z_][A-Za-z_0-9]*)\])?', line)
        if m.start() <= col <= m.end()), None)
    if scene_ref is not None:
        scene_key = scene_ref.group(1)
        target = get_resolver().resolve_read(f"scenes/{scene_key}.yaml")
        if target is None:
            return None
        key = scene_ref.group(2) if scene_ref.start(2) <= col <= scene_ref.end(2) else scene_key
        target_lines = target.read_text(encoding="utf-8-sig").splitlines()
        found = next((i for i, text in enumerate(target_lines)
                      if re.match(rf'\s*-?\s*key:\s*{re.escape(key)}\s*$', text)), 0)
        pos = Position(line=found, character=0)
        return Location(uri=target.resolve().as_uri(), range=Range(start=pos, end=pos))
    selected = _procedure_under_cursor(
        params.text_document.uri, doc.source, line_no, col)
    if selected is None:
        return None
    name, target = selected
    source = _open_documents.get(target, ("", ""))[1]
    if not source:
        source = (doc.source if target == _uri_to_path(params.text_document.uri).resolve()
                  else target.read_text(encoding="utf-8-sig"))
    target_lines = source.splitlines()
    definition_line = _find_def_line(name, target_lines)
    if definition_line < 0:
        return None
    pos = Position(line=definition_line, character=0)
    return Location(uri=target.as_uri(), range=Range(start=pos, end=pos))


_PROC_NAME = r"[A-Za-z_\u4e00-\u9fff][A-Za-z_0-9\u4e00-\u9fff]*"


def _procedure_under_cursor(uri: str, source: str, line_no: int,
                            character: int) -> tuple[str, Path] | None:
    lines = source.splitlines()
    if line_no >= len(lines):
        return None
    line = lines[line_no]
    match = re.search(rf"\b(?:call\s+(?:\${_PROC_NAME}\s*=\s*)?|def\s+)({_PROC_NAME})", line)
    if match is None or not match.start(1) <= character <= match.end(1):
        return None
    try:
        program = _parse_cached(source)
        procs, sources, _ = _load_import_graph(program, _uri_to_path(uri))
    except Exception:
        return None
    name = match.group(1)
    if name not in procs:
        return None
    is_definition = (re.match(r"\s*def\b", line) is not None
                     and _find_def_line(name, lines) == line_no)
    is_call = any(isinstance(stmt, CallProc) and stmt.name == name
                  and stmt.line_no == line_no + 1
                  for stmt in _all_call_stmts(program))
    if not is_definition and not is_call:
        return None
    return name, sources[name].resolve()


def _proc_locations(path: Path, program, name: str,
                    *, include_definition: bool) -> list[Location]:
    """Locate AST-backed calls; never search arbitrary text or comments."""
    source = _open_documents.get(path, ("", ""))[1]
    if not source:
        source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()
    locations: list[Location] = []
    if include_definition and name in program.procs:
        index = _find_def_line(name, lines)
        if index >= 0:
            match = re.search(rf"\bdef\s+({re.escape(name)})\b", lines[index])
            if match:
                locations.append(Location(uri=path.as_uri(), range=Range(
                    start=Position(line=index, character=match.start(1)),
                    end=Position(line=index, character=match.end(1)))))
    for stmt in _all_call_stmts(program):
        if not isinstance(stmt, CallProc) or stmt.name != name:
            continue
        index = stmt.line_no - 1
        if not 0 <= index < len(lines):
            continue
        match = re.search(rf"\bcall\s+(?:\${_PROC_NAME}\s*=\s*)?({re.escape(name)})\s*\(",
                          lines[index])
        if match:
            locations.append(Location(uri=path.as_uri(), range=Range(
                start=Position(line=index, character=match.start(1)),
                end=Position(line=index, character=match.end(1)))))
    return locations


@server.feature(TEXT_DOCUMENT_REFERENCES)
def on_references(params: ReferenceParams) -> list[Location]:
    doc = server.workspace.get_text_document(params.text_document.uri)
    selected = _procedure_under_cursor(
        params.text_document.uri, doc.source,
        params.position.line, params.position.character)
    if selected is None:
        return []
    name, definition = selected
    from lvjiang.workflows.file_tree import list_workflow_files

    resolver = get_resolver()
    files = {resolver.resolve_read(f"workflows/{item.rel_path}")
             for item in list_workflow_files()}
    files.discard(None)
    files.add(_uri_to_path(params.text_document.uri))
    locations: list[Location] = []
    for candidate in sorted(path.resolve() for path in files):
        try:
            program = _parse_imported(candidate)
            _, sources, _ = _load_import_graph(program, candidate)
        except Exception:
            continue
        if sources.get(name) != definition:
            continue
        locations.extend(_proc_locations(
            candidate, program, name,
            include_definition=params.context.include_declaration))
    return locations


@server.feature(TEXT_DOCUMENT_RENAME)
def on_rename(params: RenameParams) -> WorkspaceEdit | None:
    """Rename one procedure only when all effective callers can be edited safely."""
    doc = server.workspace.get_text_document(params.text_document.uri)
    selected = _procedure_under_cursor(
        params.text_document.uri, doc.source,
        params.position.line, params.position.character)
    if selected is None:
        return None
    old_name, _ = selected
    new_name = params.new_name
    if new_name == old_name:
        return WorkspaceEdit(changes={})
    if re.fullmatch(_PROC_NAME, new_name) is None:
        return None
    # The parser is authoritative for keywords and other reserved names.
    try:
        parse_text(f"def {new_name}()\n    return 1\nend\n")
    except Exception:
        return None
    from lvjiang.workflows.file_tree import list_workflow_files

    resolver = get_resolver()
    for item in list_workflow_files():
        candidate = resolver.resolve_read(f"workflows/{item.rel_path}")
        if candidate is None:
            continue
        candidate = candidate.resolve()
        source = _open_documents.get(candidate, ("", ""))[1]
        if not source:
            source = candidate.read_text(encoding="utf-8-sig")
        if re.search(rf"\b{re.escape(old_name)}\b", source):
            try:
                _parse_imported(candidate)
            except Exception:
                return None
    references = on_references(ReferenceParams(
        text_document=params.text_document, position=params.position,
        context=ReferenceContext(include_declaration=True)))
    if not references:
        return None
    changes: dict[str, list[TextEdit]] = {}
    for location in references:
        path = _uri_to_path(location.uri).resolve()
        if not path.is_file() or not os.access(path, os.W_OK):
            return None
        source = _open_documents.get(path, ("", ""))[1]
        if not source:
            source = path.read_text(encoding="utf-8-sig")
        lines = source.splitlines()
        line = location.range.start.line
        start = location.range.start.character
        end = location.range.end.character
        if line >= len(lines) or lines[line][start:end] != old_name:
            return None
        try:
            program = _parse_imported(path)
            procs, _, problems = _load_import_graph(program, path)
        except Exception:
            return None
        if problems or new_name in procs:
            return None
        changes.setdefault(location.uri, []).append(TextEdit(
            range=location.range, new_text=new_name))
    return WorkspaceEdit(changes=changes)
