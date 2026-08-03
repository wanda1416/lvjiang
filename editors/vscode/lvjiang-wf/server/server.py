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
from lark.exceptions import (  # noqa: E402
    LarkError,
    UnexpectedCharacters,
    UnexpectedToken,
)

server = LanguageServer("lvjiang-wf-server", "v0.1")


def _validate_and_publish(uri: str, source: str) -> None:
    """Parse *source* and publish diagnostics to the client."""
    diagnostics: list[Diagnostic] = []

    try:
        parse_text(source)
    except (UnexpectedCharacters, UnexpectedToken) as e:
        # Lark provides 1-based line/column; LSP uses 0-based.
        line = max(getattr(e, "line", 1) - 1, 0)
        col = max(getattr(e, "column", 1) - 1, 0)
        # Clamp end column to the actual line length.
        lines = source.splitlines()
        line_len = len(lines[line]) if line < len(lines) else col + 1
        end_col = min(col + 20, line_len)
        diagnostics.append(
            Diagnostic(
                range=Range(
                    start=Position(line=line, character=col),
                    end=Position(line=line, character=max(end_col, col + 1)),
                ),
                message=str(e),
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
