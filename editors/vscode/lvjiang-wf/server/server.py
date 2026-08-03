"""LvJiang Workflow DSL Language Server

Provides real-time diagnostics for .wf files by reusing the project's
existing Lark-based parser.
"""
from __future__ import annotations

import sys
from pathlib import Path

from lsprotocol.types import (
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    Diagnostic,
    DiagnosticSeverity,
    DidChangeTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    Position,
    Range,
)
from pygls.server import LanguageServer

# ---------------------------------------------------------------------------
# Bootstrap: make project source importable
# ---------------------------------------------------------------------------
# server/ is at editors/vscode/lvjiang-wf/server/
# project root is 4 levels up from this file's parent.
_project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
_src_dir = _project_root / "src"
if _src_dir.is_dir() and str(_src_dir) not in sys.path:
    sys.path.insert(0, str(_src_dir))

from lvjiang.workflows.grammar import parse_text  # noqa: E402
from lvjiang.workflows.grammar.parser.api import _get_parser, _preprocess_line_continuation  # noqa: E402
from lark.exceptions import (  # noqa: E402
    LarkError,
    UnexpectedCharacters,
    UnexpectedToken,
)
from lark import Tree, Token  # noqa: E402

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
    "if", "elif", "else", "while", "for", "in", "break", "continue", "end",
    # Boolean/null
    "true", "false", "null",
    # Logical operators
    "and", "or", "not",
    # Timing
    "before", "after", "around",
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
                # Check if this name is close to any keyword
                for kw in DSL_KEYWORDS:
                    if name != kw and _levenshtein_distance(name, kw) <= 2:
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


def _validate_and_publish(uri: str, source: str) -> None:
    """Parse *source* and publish diagnostics to the client."""
    diagnostics: list[Diagnostic] = []

    try:
        parse_text(source)
        # Syntax is valid - check for keyword typos in identifiers
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
