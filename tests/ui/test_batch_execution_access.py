"""Batch user leases cover preparation, execution, persistence and cleanup."""
import pytest

from lvjiang.core.access import AccessDeniedError, acquire_user
from lvjiang.core.batch_config import BatchConfigItem
from lvjiang.core.config.users import SessionManager
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import (
    BatchContext,
    BatchScript,
    BatchStageResult,
    BatchWorker,
)


def make_worker(tmp_path, monkeypatch):
    import lvjiang.core.daily_history as history
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["alice", "bob"],
        [BatchScript("test", "test")],
        BatchConfigItem(name="test", usernames=["alice", "bob"]),
        BatchContext(None, None, None, None), SessionManager(tmp_path), lambda: False,
    )
    monkeypatch.setattr(worker, "_load_script_params", lambda _: {})
    monkeypatch.setattr(worker, "_save_result", lambda *a: None)
    return worker


def test_batch_user_lock_covers_each_stage_and_saved_session(tmp_path, monkeypatch, qapp):
    worker = make_worker(tmp_path, monkeypatch)
    visits = []

    def stage(stage_name, wf, index=-1, username="", *args):
        if stage_name in ("prepare_item", "finish_item"):
            user = username
            with pytest.raises(AccessDeniedError):
                acquire_user(user, tmp_path)
            other = "bob" if user == "alice" else "alice"
            lease = acquire_user(other, tmp_path)
            lease.release()
            visits.append((stage_name, user))
        return BatchStageResult(state={})

    def run_script(script, session, username):
        with pytest.raises(AccessDeniedError):
            acquire_user(username, tmp_path)
        session["done"] = True
        return {}

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(worker, "_run_script", run_script)
    worker.run()
    assert visits == [(stage, user) for user in ("alice", "bob")
                      for stage in ("prepare_item", "finish_item")]
    for user in ("alice", "bob"):
        assert worker._session_manager.load(user)["done"] is True
        lease = acquire_user(user, tmp_path)
        lease.release()


def test_busy_user_never_reaches_prepare_and_reports_failure(tmp_path, monkeypatch, qapp):
    worker = make_worker(tmp_path, monkeypatch)
    stages = []
    monkeypatch.setattr(worker, "_run_stage", lambda stage, *args: (
        stages.append(stage) or BatchStageResult(state={})))
    result = []
    worker.finished_all.connect(result.append)
    lease = acquire_user("alice", tmp_path)
    try:
        worker.run()
        assert stages == ["batch_setup"]
        assert "error" in result[-1]
    finally:
        lease.release()
