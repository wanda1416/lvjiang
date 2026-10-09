"""The VS Code server must agree with runtime workflow import semantics."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvjiang.workflows.grammar import parse_text


@pytest.fixture(scope="module")
def wf_server():
    root = Path(__file__).resolve().parents[2]
    source = root / "editors/vscode/lvjiang-wf/server/server.py"
    spec = importlib.util.spec_from_file_location("wf_language_server_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def parse_cache_server(wf_server):
    wf_server._parse_snapshot.cache_clear()
    yield wf_server
    wf_server._parse_snapshot.cache_clear()


def test_buffer_parse_returns_independent_ast(parse_cache_server):
    """修改某次返回的过程和内部指令，不能污染其它编辑功能的解析结果。"""
    text = "def navigate()\n    return 1\nend\n"
    first = parse_cache_server._parse_cached(text)
    second = parse_cache_server._parse_cached(text)
    first.procs["navigate"].body.clear()
    first.procs.clear()
    third = parse_cache_server._parse_cached(text)
    assert set(second.procs) == set(third.procs) == {"navigate"}
    assert len(second.procs["navigate"].body) == 1
    assert len(third.procs["navigate"].body) == 1


def test_editing_same_line_reuses_fallback_parse(
    parse_cache_server, monkeypatch: pytest.MonkeyPatch,
):
    """连续输入不完整调用时，复用相同备用文本并保持过程补全可用。"""
    parsed_texts = []
    original_parse = parse_cache_server.parse_text

    def counted_parse(text):
        parsed_texts.append(text)
        return original_parse(text)

    monkeypatch.setattr(parse_cache_server, "parse_text", counted_parse)
    prefix = "def navigate()\n    return 1\nend\n"
    first = parse_cache_server._parse_for_editing(prefix + "call navigate(\n", 3)
    second = parse_cache_server._parse_for_editing(prefix + "call navigate(1\n", 3)
    assert set(first.procs) == set(second.procs) == {"navigate"}
    assert parsed_texts == [
        prefix + "call navigate(\n",
        prefix + "# editing\n",
        prefix + "call navigate(1\n",
    ]


def test_imports_use_workflows_root_and_resolve_calls(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "config/system/workflows"
    imported = root / "subcall/navigation.wf"
    imported.parent.mkdir(parents=True)
    imported.write_text("def navigate($target)\n    return $target\nend\n",
                        encoding="utf-8")
    source = root / "subcall/login.wf"
    source.write_text('import "subcall/navigation.wf"\ncall navigate("home")\n',
                      encoding="utf-8")

    class Resolver:
        def resolve_read(self, rel_path: str) -> Path | None:
            path = root.parent / rel_path
            return path if path.is_file() else None

    monkeypatch.setattr(wf_server, "get_resolver", lambda: Resolver())
    program = parse_text(source.read_text(encoding="utf-8"))
    procs, sources, problems = wf_server._load_import_graph(program, source)
    assert problems == []
    assert sources["navigate"] == imported
    assert wf_server._check_call_exists(program, procs) == []
    assert wf_server._check_proc_param_count(program, procs) == []


def test_import_graph_reports_missing_file_and_bad_argument_count(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workflows"
    root.mkdir()
    imported = root / "navigation.wf"
    imported.write_text("def navigate($target)\n    return $target\nend\n",
                        encoding="utf-8")

    class Resolver:
        def resolve_read(self, rel_path: str) -> Path | None:
            path = tmp_path / rel_path
            return path if path.is_file() else None

    monkeypatch.setattr(wf_server, "get_resolver", lambda: Resolver())
    program = parse_text('import "navigation.wf"\nimport "gone.wf"\ncall navigate()\n')
    procs, _, problems = wf_server._load_import_graph(program, root / "main.wf")
    assert len(problems) == 1
    assert "gone.wf" in problems[0].message
    assert wf_server._check_call_exists(program, procs) == []
    assert len(wf_server._check_proc_param_count(program, procs)) == 1


def test_completion_and_definition_follow_imported_proc(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workflows"
    root.mkdir()
    imported = root / "helpers.wf"
    imported.write_text("def navigate($target)\n    return $target\nend\n",
                        encoding="utf-8")
    source = root / "main.wf"
    content = 'import "helpers.wf"\ncall nav\n'

    class Resolver:
        def resolve_read(self, rel_path: str) -> Path | None:
            path = tmp_path / rel_path
            return path if path.is_file() else None

    monkeypatch.setattr(wf_server, "get_resolver", lambda: Resolver())
    monkeypatch.setattr(wf_server, "_get_scene_registry", lambda: None)
    monkeypatch.setattr(wf_server, "server", SimpleNamespace(
        workspace=SimpleNamespace(get_text_document=lambda uri: SimpleNamespace(source=content))))
    document = SimpleNamespace(uri=source.as_uri())
    completion = wf_server.on_completion(SimpleNamespace(
        text_document=document, position=SimpleNamespace(line=1, character=8)))
    assert "navigate" in [item.label for item in completion]

    monkeypatch.setattr(wf_server.server.workspace, "get_text_document",
                        lambda uri: SimpleNamespace(source=content.replace("call nav", "call navigate()")))
    location = wf_server.on_definition(SimpleNamespace(
        text_document=document, position=SimpleNamespace(line=1, character=8)))
    assert location is not None
    assert location.uri == imported.as_uri()
    assert location.range.start.line == 0


def test_invalid_metadata_is_reported_even_when_dsl_parses(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list = []
    monkeypatch.setattr(wf_server, "server", SimpleNamespace(
        publish_diagnostics=lambda uri, diagnostics: published.extend(diagnostics)))
    monkeypatch.setattr(wf_server, "_get_scene_registry", lambda: None)
    monkeypatch.setattr(wf_server, "_check_layout_refs", lambda program: [])
    wf_server._validate_and_publish(
        (tmp_path / "main.wf").as_uri(), "#% name: [\nwait 1\n")
    assert any("元数据" in item.message for item in published)


def test_unsaved_import_uses_open_buffer(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workflows"
    root.mkdir()
    imported = root / "helpers.wf"
    imported.write_text("def old()\n    return 1\nend\n", encoding="utf-8")

    class Resolver:
        def resolve_read(self, rel_path: str) -> Path | None:
            path = tmp_path / rel_path
            return path if path.is_file() else None

    monkeypatch.setattr(wf_server, "get_resolver", lambda: Resolver())
    wf_server._open_documents[imported] = (
        imported.as_uri(), "def fresh()\n    return 1\nend\n")
    try:
        program = parse_text('import "helpers.wf"\ncall fresh()\n')
        procs, _, problems = wf_server._load_import_graph(program, root / "main.wf")
        assert problems == []
        assert "fresh" in procs
        assert "old" not in procs
        assert wf_server._check_call_exists(program, procs) == []
    finally:
        wf_server._open_documents.pop(imported)
        wf_server._import_cache.pop(imported, None)


def test_find_references_uses_resolved_process_identity(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lvjiang.workflows import file_tree

    root = tmp_path / "workflows"
    root.mkdir()
    imported = root / "helpers.wf"
    imported.write_text("def navigate()\n    return 1\nend\n", encoding="utf-8")
    source = root / "main.wf"
    content = 'import "helpers.wf"\ncall navigate()\n'
    source.write_text(content, encoding="utf-8")

    class Resolver:
        def resolve_read(self, rel_path: str) -> Path | None:
            path = tmp_path / rel_path
            return path if path.is_file() else None

    monkeypatch.setattr(wf_server, "get_resolver", lambda: Resolver())
    monkeypatch.setattr(file_tree, "list_workflow_files", lambda: [
        SimpleNamespace(rel_path="helpers.wf"),
        SimpleNamespace(rel_path="main.wf"),
    ])
    monkeypatch.setattr(wf_server, "server", SimpleNamespace(
        workspace=SimpleNamespace(get_text_document=lambda uri: SimpleNamespace(source=content))))
    params = SimpleNamespace(
        text_document=SimpleNamespace(uri=source.as_uri()),
        position=SimpleNamespace(line=1, character=7),
        context=SimpleNamespace(include_declaration=True))
    locations = wf_server.on_references(params)
    assert {(item.uri, item.range.start.line) for item in locations} == {
        (source.as_uri(), 1), (imported.as_uri(), 0),
    }


def test_valid_identifier_similar_to_keyword_has_no_warning(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list = []
    monkeypatch.setattr(wf_server, "server", SimpleNamespace(
        publish_diagnostics=lambda uri, diagnostics: published.extend(diagnostics)))
    monkeypatch.setattr(wf_server, "_get_scene_registry", lambda: None)
    monkeypatch.setattr(wf_server, "_check_layout_refs", lambda program: [])
    wf_server._validate_and_publish(
        (tmp_path / "main.wf").as_uri(),
        "def logo()\n    return 1\nend\ncall logo()\n")
    assert published == []


def test_procedure_rename_checks_collisions_and_edits_only_symbols(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lvjiang.workflows import file_tree

    root = tmp_path / "workflows"
    root.mkdir()
    imported = root / "helpers.wf"
    imported.write_text("def navigate()\n    return 1\nend\n", encoding="utf-8")
    source = root / "main.wf"
    content = 'import "helpers.wf"\n# navigate is a comment\ncall navigate()\n'
    source.write_text(content, encoding="utf-8")
    unrelated = root / "unrelated.wf"
    unrelated.write_text("def navigate()\n    return 2\nend\ncall navigate()\n",
                         encoding="utf-8")

    class Resolver:
        def resolve_read(self, rel_path: str) -> Path | None:
            path = tmp_path / rel_path
            return path if path.is_file() else None

    monkeypatch.setattr(wf_server, "get_resolver", lambda: Resolver())
    monkeypatch.setattr(file_tree, "list_workflow_files", lambda: [
        SimpleNamespace(rel_path="helpers.wf"),
        SimpleNamespace(rel_path="main.wf"),
        SimpleNamespace(rel_path="unrelated.wf"),
    ])
    monkeypatch.setattr(wf_server, "server", SimpleNamespace(
        workspace=SimpleNamespace(get_text_document=lambda uri: SimpleNamespace(source=content))))
    params = SimpleNamespace(
        text_document=SimpleNamespace(uri=source.as_uri()),
        position=SimpleNamespace(line=2, character=7), new_name="travel")
    edit = wf_server.on_rename(params)
    assert edit is not None
    assert set(edit.changes) == {source.as_uri(), imported.as_uri()}
    assert len(edit.changes[source.as_uri()]) == 1
    assert edit.changes[source.as_uri()][0].range.start.line == 2
    assert source.read_text(encoding="utf-8") == content  # LSP proposes edits only
    params.position.line = 1
    assert wf_server.on_rename(params) is None  # text in a comment is not a symbol
    params.position.line = 2

    imported.write_text("def navigate()\n    return 1\nend\ndef travel()\n    return 2\nend\n",
                        encoding="utf-8")
    assert wf_server.on_rename(params) is None


def _client_uri(path: Path) -> str:
    """Reproduce the URI form the VS Code client sends (drive colon as %3A)."""
    head, _, tail = path.as_uri().partition(":///")
    if len(tail) > 1 and tail[1] == ":":
        tail = tail[0] + "%3A" + tail[2:]
    return head + ":///" + tail


def test_definition_in_same_file_survives_client_encoded_uri(
    wf_server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call defined in the current file must still yield a usable location.

    The VS Code client encodes the Windows drive colon as %3A, which used to
    convert into a drive-relative path; as_uri() then raised ValueError and
    F12 on a locally defined procedure failed instead of navigating.
    """
    source = tmp_path / "main.wf"
    content = "def navigate($target)\n    return $target\nend\ncall navigate(1)\n"
    source.write_text(content, encoding="utf-8")

    monkeypatch.setattr(wf_server, "get_resolver", lambda: SimpleNamespace(
        resolve_read=lambda rel_path: None))
    monkeypatch.setattr(wf_server, "server", SimpleNamespace(
        workspace=SimpleNamespace(
            get_text_document=lambda uri: SimpleNamespace(source=content))))
    params = SimpleNamespace(
        text_document=SimpleNamespace(uri=_client_uri(source)),
        position=SimpleNamespace(line=3, character=7))
    location = wf_server.on_definition(params)
    assert location is not None
    assert wf_server._uri_to_path(location.uri).resolve() == source.resolve()
    assert location.range.start.line == 0
