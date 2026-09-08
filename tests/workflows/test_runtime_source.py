"""Editor runs keep their sources private without changing import lookup."""
import pytest

from lvjiang.core import access
from lvjiang.workflows.runtime_source import runtime_source
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
