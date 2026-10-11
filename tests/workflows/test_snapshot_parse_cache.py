"""配置快照不能让未改动的工作流在每次启动时重新解析。"""
from lvjiang.workflows.grammar.parser import api


def test_same_text_across_snapshots_reuses_parse_but_keeps_ast_and_source_independent(tmp_path, monkeypatch):
    api._parse_cached_file.cache_clear()
    original = api.parse_text
    calls = []
    def parse(text, *args, **kwargs):
        calls.append(text)
        return original(text, *args, **kwargs)
    monkeypatch.setattr(api, "parse_text", parse)
    first = tmp_path / "snapshot-a.wf"
    second = tmp_path / "snapshot-b.wf"
    for path in (first, second):
        path.write_text('log "test"\n', encoding="utf-8")
    one = api.parse_file(first)
    one.body.clear()
    two = api.parse_file(second)
    assert len(calls) == 1
    assert two.body and two.source == str(second)
    second.write_text('log "changed"\n', encoding="utf-8")
    api.parse_file(second)
    assert len(calls) == 2
    api._parse_cached_file.cache_clear()
