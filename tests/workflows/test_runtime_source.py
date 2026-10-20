"""Editor runs keep their sources private without changing import lookup."""
import pytest

from lvjiang.core import access
from lvjiang.core.config.resolver import get_resolver
from lvjiang.workflows.runtime_source import runtime_source, save_editor_snapshot
from tests.workflows.conftest import make_engine


@pytest.mark.parametrize("readonly", [False, True])
def test_overlapping_editor_runs_have_independent_sources(wf_root, monkeypatch, readonly):
    monkeypatch.setattr(access, "_readonly", readonly)
    (wf_root / "shared.wf").write_text("def shared()\n    return 1\nend\n")
    legacy = wf_root / "_editor_run.wf"
    legacy.write_text("# previous draft\n")
    with runtime_source('import "shared.wf"\ncall shared()') as first:
        with runtime_source("log info 2") as second:
            assert first != second
            assert first.read_text() == 'import "shared.wf"\ncall shared()\n'
            assert second.read_text() == "log info 2\n"
            engine = make_engine()
            engine.validate_only(first)
            assert "shared" in engine._procs
        assert first.exists() and not second.exists()
    assert not first.exists()
    assert legacy.read_text() == "# previous draft\n"


def test_save_editor_snapshot_updates_fixed_restore_source(wf_root):
    legacy = wf_root / "_editor_run.wf"
    legacy.write_text("# previous draft\n")

    written = save_editor_snapshot("log info 42")

    assert written == wf_root.parent.parent / "local/workflows/_editor_run.wf"
    assert written.read_text(encoding="utf-8") == "log info 42\n"
    assert get_resolver().resolve_read("workflows/_editor_run.wf") == written


def test_editor_artifacts_stay_local_in_dev_mode(tmp_path, monkeypatch):
    """开发模式普通保存写 system，但编辑器草稿不能污染预置输入。"""
    from lvjiang.core.config import resolver as resolver_mod

    system = tmp_path / "system"
    local = tmp_path / "local"
    (system / "workflows").mkdir(parents=True)
    local.mkdir()
    monkeypatch.setattr(
        resolver_mod, "_resolver",
        resolver_mod.ConfigResolver(system_dir=system, local_dir=local, dev_mode=True),
    )

    written = save_editor_snapshot("log info 7")
    assert written == local / "workflows/_editor_run.wf"
    assert written.read_text(encoding="utf-8") == "log info 7\n"
    assert not (system / "workflows/_editor_run.wf").exists()

    with runtime_source("log info 8") as source:
        assert source.parent == local / "workflows"
    assert not source.exists()
    assert list((system / "workflows").iterdir()) == []
