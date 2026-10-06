"""重复加载不重新解析，且缓存不泄漏 AST 修改或吞掉脚本热更新。"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from lvjiang.workflows.grammar.parser import api
from tests.workflows.conftest import make_engine


def test_concurrent_file_loads_reuse_parse_but_isolate_nested_ast(tmp_path, monkeypatch):
    """多个任务加载公共脚本只解析一次，修改返回值不影响其他执行。"""
    path = tmp_path / "shared.wf"
    path.write_text("def value()\n    return 1\nend\n", encoding="utf-8")
    calls = []
    original = api.parse_text

    def counted(text, source="<text>"):
        calls.append(source)
        return original(text, source)

    monkeypatch.setattr(api, "parse_text", counted)
    ready = Barrier(3)

    def load():
        ready.wait()
        return api.parse_file(path)

    with ThreadPoolExecutor(max_workers=3) as pool:
        programs = list(pool.map(lambda _: load(), range(3)))
    assert len(calls) == 1
    programs[0].procs["value"].body.clear()
    programs[1].procs.clear()
    assert len(programs[2].procs["value"].body) == 1
    assert len(api.parse_file(path).procs["value"].body) == 1
    assert len(calls) == 1


def test_file_cache_detects_same_stat_edits_and_deletion(tmp_path):
    """编辑器保留大小/mtime 的修改必须生效，删除不能回退到旧缓存。"""
    path = tmp_path / "edit.wf"
    path.write_text("wait 1\n", encoding="utf-8")
    first = api.parse_file(path)
    before = path.stat()
    path.write_text("wait 2\n", encoding="utf-8")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert api.parse_file(path).body != first.body
    path.unlink()
    with pytest.raises(FileNotFoundError):
        api.parse_file(path)


def test_engines_reuse_import_parse_and_observe_dependency_edits(wf_root, monkeypatch):
    """候选检查/执行反复加载共享 import 时复用，根文件没改也能热更新依赖。"""
    helper = wf_root / "helper.wf"
    helper.write_text("def value()\n    return 1\nend\n", encoding="utf-8")
    root = wf_root / "root.wf"
    root.write_text('import "helper.wf"\n', encoding="utf-8")
    calls = []
    original = api.parse_text

    def counted(text, source="<text>"):
        calls.append(source)
        return original(text, source)

    monkeypatch.setattr(api, "parse_text", counted)
    first, second = make_engine(), make_engine()
    first.load_subcalls(root)
    second.load_subcalls(root)
    assert len(calls) == 2
    assert first.call_subcall("value") == second.call_subcall("value") == 1
    helper.write_text("def value()\n    return 2\nend\n", encoding="utf-8")
    second.load_subcalls(root)
    assert len(calls) == 3
    assert second.call_subcall("value") == 2
    assert first.call_subcall("value") == 1
