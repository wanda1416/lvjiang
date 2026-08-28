"""wf 路径自愈：缓存路径失效时按脚本 ID 重新发现一次。

日常页与批量页共用这一份——批量页过去没有自愈，同样的陈旧路径在日常能跑、
批量却直接 FileNotFound。
"""

from __future__ import annotations

from pathlib import Path

from lvjiang.workflows.discovery import resolve_workflow_path


class _Resolver:
    def __init__(self, paths: dict[str, Path]):
        self.paths = paths

    def resolve_read(self, rel_path: str):
        return self.paths.get(rel_path)


def _setup(tmp_path, monkeypatch, *, discovered):
    wf_path = tmp_path / "workflows" / "standalone" / "demo.wf"
    wf_path.parent.mkdir(parents=True)
    wf_path.write_text("log 'ok'\n", encoding="utf-8")
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.get_resolver",
        lambda: _Resolver({"workflows/standalone/demo.wf": wf_path}),
    )
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.discover_scripts", lambda: discovered
    )
    return wf_path


def test_stale_path_is_healed_by_script_id(tmp_path, monkeypatch):
    wf_path = _setup(
        tmp_path, monkeypatch,
        discovered=[{"id": "demo", "wf_file": "standalone/demo.wf"}],
    )

    path, resolved = resolve_workflow_path("demo.wf", "demo")

    assert path == wf_path
    assert resolved == "standalone/demo.wf"


def test_current_path_is_returned_unchanged(tmp_path, monkeypatch):
    wf_path = _setup(tmp_path, monkeypatch, discovered=[])

    path, resolved = resolve_workflow_path("standalone/demo.wf", "demo")

    assert path == wf_path
    assert resolved == "standalone/demo.wf"


def test_genuinely_missing_file_is_not_masked(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, discovered=[])

    assert resolve_workflow_path("gone.wf", "gone") == (None, "gone.wf")


def test_without_script_id_there_is_no_rediscovery(tmp_path, monkeypatch):
    _setup(
        tmp_path, monkeypatch,
        discovered=[{"id": "demo", "wf_file": "standalone/demo.wf"}],
    )

    assert resolve_workflow_path("demo.wf") == (None, "demo.wf")
