"""Editor runs keep their sources private without changing import lookup."""

import pytest

from lvjiang.core import access
from lvjiang.core.config.resolver import get_resolver
from lvjiang.workflows.runtime_source import (
    EDITOR_RUN_REL,
    SCRATCH_DIR_REL,
    runtime_source,
    save_editor_snapshot,
)
from tests.workflows.conftest import make_engine


@pytest.mark.parametrize("readonly", [False, True])
def test_overlapping_editor_runs_have_independent_sources(wf_root, monkeypatch, readonly):
    """每次运行一份独立草稿；import 仍按 workflows 根解析，与草稿所在目录无关。"""
    monkeypatch.setattr(access, "_readonly", readonly)
    (wf_root / "shared.wf").write_text("def shared()\n    return 1\nend\n")
    local_root = wf_root.parent.parent / "local"
    scratch = local_root / SCRATCH_DIR_REL
    with runtime_source('import "shared.wf"\ncall shared()') as first:
        with runtime_source("log info 2") as second:
            assert first != second
            # 草稿收在 _scratch/ 里，根层不再出现临时 .wf
            assert first.parent == scratch
            assert first.read_text() == 'import "shared.wf"\ncall shared()\n'
            assert second.read_text() == "log info 2\n"
            engine = make_engine()
            engine.validate_only(first)
            assert "shared" in engine._procs
        assert first.exists() and not second.exists()
    assert not first.exists()


def test_save_editor_snapshot_updates_fixed_restore_source(wf_root):
    written = save_editor_snapshot("log info 42")

    assert written == wf_root.parent.parent / "local" / SCRATCH_DIR_REL / "editor_run.wf"
    assert written.read_text(encoding="utf-8") == "log info 42\n"
    assert get_resolver().resolve_read(EDITOR_RUN_REL) == written


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
    assert written == local / SCRATCH_DIR_REL / "editor_run.wf"
    assert written.read_text(encoding="utf-8") == "log info 7\n"
    assert not (system / SCRATCH_DIR_REL).exists()

    with runtime_source("log info 8") as source:
        assert source.parent == local / SCRATCH_DIR_REL
    assert not source.exists()
    assert list((system / "workflows").iterdir()) == []
