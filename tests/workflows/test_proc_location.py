"""脚本编辑器的过程跳转必须跟着 import 图，而不是按文本搜索同名 def。"""

from pathlib import Path

import pytest

from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.grammar.ast_nodes import CallProc
from lvjiang.workflows.proc_location import index_proc_calls


@pytest.fixture
def workflows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    system = tmp_path / "system"
    resolver = ConfigResolver(system_dir=system, local_dir=tmp_path / "local", dev_mode=True)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    return system / "workflows"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_imported_call_resolves_to_defining_file(workflows: Path) -> None:
    target = _write(
        workflows, "subcall/navigation.wf",
        "def navigate($target)\n    return $target\nend\n",
    )
    source = _write(
        workflows, "main.wf",
        'import "subcall/navigation.wf"\ncall navigate("home")\n',
    )
    site = index_proc_calls(source.read_text(encoding="utf-8"), source)[1]
    assert site.start == source.read_text(encoding="utf-8").splitlines()[1].index("navigate")
    assert site.definition.path == target.resolve()
    assert site.definition.rel_path == "subcall/navigation.wf"
    assert site.definition.line == 1


def test_same_file_definition_uses_unsaved_buffer(workflows: Path) -> None:
    source = _write(workflows, "main.wf", "def old()\n    return 1\nend\n")
    edited = "def greet($name)\n    return $name\nend\ncall greet(\"a\")\n"
    site = index_proc_calls(edited, source)[3]
    assert site.definition.path == source.resolve()
    assert site.definition.line == 1
    assert site.definition.rel_path == "main.wf"


def test_comment_and_builtin_are_not_calls(workflows: Path) -> None:
    source = _write(
        workflows, "main.wf",
        'def greet()\n    return 1\nend\n# call greet()\neval $x = sub(2, 1)\n',
    )
    assert index_proc_calls(source.read_text(encoding="utf-8"), source) == {}


def test_assigned_call_uses_procedure_name(workflows: Path) -> None:
    source = _write(
        workflows, "main.wf",
        "def greet()\n    return 1\nend\ncall $out = greet()\n",
    )
    text = source.read_text(encoding="utf-8")
    program = parse_text(text, source=str(source))
    call = next(stmt for stmt in program.body if isinstance(stmt, CallProc))
    assert (call.name_line, call.name_col, call.name_end_col) == (4, 13, 18)
    assert (program.procs["greet"].line_no, program.procs["greet"].name_col) == (1, 5)
    site = index_proc_calls(text, source)[3]
    assert text.splitlines()[site.line][site.start:site.end] == "greet"
    assert site.definition.line == 1


def test_backslash_continuation_uses_source_line_of_name(workflows: Path) -> None:
    source = _write(
        workflows, "main.wf",
        "def \\\ngreet()\n    return 1\nend\ncall \\\ngreet()\n",
    )
    text = source.read_text(encoding="utf-8")
    program = parse_text(text, source=str(source))
    call = next(stmt for stmt in program.body if isinstance(stmt, CallProc))
    assert (call.name_line, call.name_col, call.name_end_col) == (6, 1, 6)
    assert program.procs["greet"].line_no == 2
    site = index_proc_calls(text, source)[5]
    assert text.splitlines()[site.line][site.start:site.end] == "greet"
    assert site.definition.line == 2


def test_call_inside_conditional_resolves(workflows: Path) -> None:
    source = _write(
        workflows, "main.wf",
        "def greet()\n    return 1\nend\nif true\n    call greet()\nend\n",
    )
    site = index_proc_calls(source.read_text(encoding="utf-8"), source)[4]
    assert site.definition.line == 1
    assert site.start == 9


def test_local_definition_wins_over_import(workflows: Path) -> None:
    _write(workflows, "helpers.wf", "def greet()\n    return 1\nend\n")
    source = _write(
        workflows, "main.wf",
        'import "helpers.wf"\ndef greet()\n    return 2\nend\ncall greet()\n',
    )
    site = index_proc_calls(source.read_text(encoding="utf-8"), source)[4]
    assert site.definition.path == source.resolve()
    assert site.definition.line == 2
