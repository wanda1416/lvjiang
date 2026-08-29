"""LvJiang Workflow DSL Language Server

Provides real-time diagnostics for .wf files by reusing the project's
existing Lark-based parser.

Level 2: syntax error diagnostics + keyword typo detection
Level 3: semantic checks (scene/call/import validation, param count)
         + editor features (folding, symbols, goto, hover)
"""
from __future__ import annotations

import os
import re
import sys
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
        TEXT_DOCUMENT_DID_CHANGE,
        TEXT_DOCUMENT_DID_OPEN,
        TEXT_DOCUMENT_DID_SAVE,
        TEXT_DOCUMENT_DOCUMENT_SYMBOL,
        TEXT_DOCUMENT_FOLDING_RANGE,
        TEXT_DOCUMENT_HOVER,
        Diagnostic,
        DiagnosticSeverity,
        DidChangeTextDocumentParams,
        DidOpenTextDocumentParams,
        DidSaveTextDocumentParams,
        DocumentSymbol,
        DocumentSymbolParams,
        FoldingRange,
        FoldingRangeKind,
        FoldingRangeParams,
        Hover,
        HoverParams,
        MarkupContent,
        MarkupKind,
        Position,
        Range,
        SymbolKind,
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
    from lvjiang.workflows.grammar import parse_text  # noqa: E402
    from lvjiang.workflows.grammar.parser.api import _get_parser, _preprocess_line_continuation  # noqa: E402
    from lvjiang.workflows.grammar.ast_nodes import (  # noqa: E402
        CallProc,
        For,
        ForRange,
        If,
        Import,
        Loop,
        ProcDef,
        Try,
        UntilLoop,
        WhileLoop,
    )
    from lvjiang.workflows.workflow_references import collect_refs  # noqa: E402
    from lark.exceptions import (  # noqa: E402
        LarkError,
        UnexpectedCharacters,
        UnexpectedToken,
    )
    from lark import Tree, Token  # noqa: E402
except ImportError as e:
    _die(
        f"cannot import 'lvjiang' ({e}). Expected the project source at {_src_dir} "
        f"(resolved from {Path(__file__).resolve()}). If this extension was installed "
        f"as a standalone copy rather than the install.bat junction into the project "
        f"checkout, that path resolution will be wrong."
    )

server = LanguageServer("lvjiang-wf-server", "v0.1")

# ---------------------------------------------------------------------------
# DSL keywords for typo detection
# ---------------------------------------------------------------------------
DSL_KEYWORDS = {
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
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def _find_keyword_typos(source: str) -> list[tuple[int, int, str, str]]:
    """Find identifiers that look like typos of DSL keywords.
    
    Returns list of (line, col, typo_name, suggested_keyword) tuples.
    Line and column are 0-based.
    
    Only flags identifiers with edit distance 1 from a keyword to avoid
    false positives on legitimate variable names.
    """
    typos: list[tuple[int, int, str, str]] = []
    try:
        parser = _get_parser()
        text = _preprocess_line_continuation(source)
        if not text.endswith("\n"):
            text += "\n"
        tree = parser.parse(text)
    except Exception:
        # If parsing fails, skip typo detection
        return typos
    
    # Walk the tree to find all NAME tokens
    def walk(tree: Tree, line_offset: int = 0) -> None:
        for child in tree.children:
            if isinstance(child, Token) and child.type == "NAME":
                name = str(child)
                # Only flag if edit distance is exactly 1 (reduces false positives)
                for kw in DSL_KEYWORDS:
                    if name != kw and _levenshtein_distance(name, kw) == 1:
                        # Lark uses 1-based line/column
                        line = max(child.line - 1, 0)
                        col = max(child.column - 1, 0)
                        typos.append((line, col, name, kw))
                        break  # Only suggest the first match
            elif isinstance(child, Tree):
                walk(child, line_offset)
    
    walk(tree)
    return typos


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
    """Convert a file URI to a Path (handles Windows drive letters correctly)."""
    parsed = urlparse(uri)
    return Path(url2pathname(parsed.path))


def _line_range(line_no: int, end_line: int | None = None) -> Range:
    """Create a Range for the given 1-based line number (or 0 if unknown)."""
    line = max(line_no - 1, 0) if line_no else 0
    end = end_line if end_line is not None else line
    return Range(
        start=Position(line=line, character=0),
        end=Position(line=end, character=0),
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
_scenes_yaml_mtime: float = 0.0


def _get_scenes_yaml_mtime() -> float:
    """Get the mtime of scenes.yaml (or 0 if not found)."""
    scenes_yaml = _project_root / "config" / "system" / "scenes.yaml"
    try:
        return os.path.getmtime(scenes_yaml)
    except OSError:
        return 0.0


def _get_scene_registry():
    """Lazily load the scene registry, invalidating cache if scenes.yaml changed."""
    global _scene_registry, _scene_registry_error, _scenes_yaml_mtime
    current_mtime = _get_scenes_yaml_mtime()
    # Invalidate cache if scenes.yaml was modified
    if _scene_registry is not None and current_mtime != _scenes_yaml_mtime:
        _scene_registry = None
        _scene_registry_error = None
    if _scene_registry is not None or _scene_registry_error is not None:
        return _scene_registry
    try:
        from lvjiang.core.scene_registry import get_registry
        _scene_registry = get_registry()
        _scenes_yaml_mtime = current_mtime
    except Exception as e:
        _scene_registry_error = str(e)
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
    valid_scenes = set(registry.all_scene_keys())
    refs = collect_refs(program.body, program.procs, source=program.source)
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


def _all_call_stmts(program) -> list:
    """Collect all CallProc statements from main body and all proc bodies."""
    stmts = _walk_stmts(program.body)
    for proc in program.procs.values():
        if isinstance(proc, ProcDef):
            stmts.extend(_walk_stmts(proc.body))
    return stmts


def _check_call_exists(program) -> list[Diagnostic]:
    """Check that all call targets are defined procedures."""
    diagnostics = []
    defined = set(program.procs.keys())
    for stmt in _all_call_stmts(program):
        if isinstance(stmt, CallProc) and stmt.name not in defined:
            diagnostics.append(Diagnostic(
                range=_line_range(stmt.line_no),
                message=f"过程 '{stmt.name}' 未定义",
                severity=DiagnosticSeverity.Error,
            ))
    return diagnostics


def _check_import_exists(program, source_path: Path) -> list[Diagnostic]:
    """Check that all imported .wf files exist on disk."""
    diagnostics = []
    base_dir = source_path.parent if source_path.is_file() else _project_root
    for imp in program.imports:
        imp_path = base_dir / imp.path
        if not imp_path.exists():
            diagnostics.append(Diagnostic(
                range=_line_range(imp.line_no),
                message=f"导入文件不存在: {imp.path}",
                severity=DiagnosticSeverity.Error,
            ))
    return diagnostics


def _check_proc_param_count(program) -> list[Diagnostic]:
    """Check that call arguments match the procedure definition."""
    diagnostics = []
    for stmt in _all_call_stmts(program):
        if isinstance(stmt, CallProc):
            proc_def = program.procs.get(stmt.name)
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

    try:
        program = parse_text(source)
        # --- Level 2: keyword typo detection ---
        lines = source.splitlines()
        for line, col, typo, suggestion in _find_keyword_typos(source):
            line_len = len(lines[line]) if line < len(lines) else col + len(typo)
            end_col = min(col + len(typo), line_len)
            diagnostics.append(
                Diagnostic(
                    range=Range(
                        start=Position(line=line, character=col),
                        end=Position(line=line, character=max(end_col, col + 1)),
                    ),
                    message=f"'{typo}' 看起来像是关键字 '{suggestion}' 的拼写错误",
                    severity=DiagnosticSeverity.Warning,
                )
            )
        # --- Level 3: semantic checks ---
        diagnostics.extend(_check_scene_exists(program))
        diagnostics.extend(_check_call_exists(program))
        diagnostics.extend(_check_import_exists(program, source_path))
        diagnostics.extend(_check_proc_param_count(program))
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
    _validate_and_publish(params.text_document.uri, params.text_document.text)


@server.feature(TEXT_DOCUMENT_DID_SAVE)
def on_save(params: DidSaveTextDocumentParams) -> None:
    doc = server.workspace.get_text_document(params.text_document.uri)
    _validate_and_publish(params.text_document.uri, doc.source)


@server.feature(TEXT_DOCUMENT_DID_CHANGE)
def on_change(params: DidChangeTextDocumentParams) -> None:
    doc = server.workspace.get_text_document(params.text_document.uri)
    _validate_and_publish(params.text_document.uri, doc.source)


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
        program = parse_text(doc.source)
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
        program = parse_text(doc.source)
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
        program = parse_text(doc.source)
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
    if word in program.procs:
        proc = program.procs[word]
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
