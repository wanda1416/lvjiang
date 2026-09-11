"""Batch user leases cover preparation, execution, persistence and cleanup."""
import pytest

from lvjiang.core.access import AccessDeniedError, acquire_user
from lvjiang.core.batch_config import BatchConfigItem, BatchWorkflows
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.config.users import SessionManager
from lvjiang.core.config.wf_configs import set_wf_config
from lvjiang.core.user_config import User, save_user_metadata, set_user_workflow_params
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import (
    ST_PENDING,
    BatchContext,
    BatchScript,
    BatchStageResult,
    BatchWorker,
)


def make_worker(tmp_path, monkeypatch, *, rounds=1):
    import lvjiang.core.daily_history as history
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["alice", "bob"],
        [BatchScript("test", "test")],
        BatchConfigItem(name="test", usernames=["alice", "bob"], rounds=rounds),
        BatchContext(None, None, None, None), SessionManager(tmp_path), lambda: False,
    )
    monkeypatch.setattr(worker, "_save_result", lambda *a: None)
    return worker


def test_batch_repeats_complete_user_sequence_by_round(tmp_path, monkeypatch, qapp):
    import lvjiang.core.daily_history as history

    worker = make_worker(tmp_path, monkeypatch, rounds=2)
    visits = []
    task_history = []
    progress = []
    monkeypatch.setattr(
        history, "try_create_task_run",
        lambda **kwargs: task_history.append(kwargs) or None,
    )
    worker.progress.connect(
        lambda index, user, script, status: progress.append(
            (index, user, script, status)))

    def stage(stage_name, wf, index=-1, username="", *args, **kwargs):
        if stage_name in ("prepare_item", "finish_item"):
            visits.append((kwargs["round_number"], stage_name, username))
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(worker, "_run_script", lambda *args, **kwargs: {})
    worker.run()

    assert visits == [
        (round_number, stage, username)
        for round_number in (1, 2)
        for username in ("alice", "bob")
        for stage in ("prepare_item", "finish_item")
    ]
    assert len(task_history) == 4
    assert [item["username"] for item in task_history] == [
        "alice", "bob", "alice", "bob",
    ]
    assert [item for item in progress if item[3] == ST_PENDING] == [
        (0, "alice", "test", ST_PENDING),
        (1, "bob", "test", ST_PENDING),
    ]


def test_batch_user_lock_covers_each_stage_and_saved_session(tmp_path, monkeypatch, qapp):
    worker = make_worker(tmp_path, monkeypatch)
    visits = []

    def stage(stage_name, wf, index=-1, username="", *args, **kwargs):
        if stage_name in ("prepare_item", "finish_item"):
            user = username
            with pytest.raises(AccessDeniedError):
                acquire_user(user, tmp_path)
            other = "bob" if user == "alice" else "alice"
            lease = acquire_user(other, tmp_path)
            lease.release()
            visits.append((stage_name, user))
        return BatchStageResult(state={})

    def run_script(script, session, username, *, params=None):
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
    monkeypatch.setattr(worker, "_run_stage", lambda stage, *args, **kwargs: (
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


def test_skipped_prepare_does_not_run_scripts_or_finish(tmp_path, monkeypatch, qapp):
    worker = make_worker(tmp_path, monkeypatch)
    stages = []
    scripts = []

    def stage(stage_name, wf, index=-1, username="", *args, **kwargs):
        stages.append((stage_name, username))
        if stage_name == "prepare_item" and username == "alice":
            return BatchStageResult(status="skipped", state={})
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(
        worker, "_run_script",
        lambda script, session, username, **kwargs: scripts.append(username) or {},
    )
    worker.run()

    assert ("finish_item", "alice") not in stages
    assert ("finish_item", "bob") in stages
    assert scripts == ["bob"]


def test_task_engine_uses_session_manager_users_dir(tmp_path, monkeypatch, qapp):
    worker = make_worker(tmp_path, monkeypatch)

    class Engine:
        users_dir = None
        session = None
        run_username = ""
        _save_callback = None

        def execute(self, workflow, initial_variables=None):
            assert self.users_dir == tmp_path
            assert initial_variables == {}
            return {}

    monkeypatch.setattr(worker, "_create_engine", Engine)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.resolve_workflow_path",
        lambda wf_file, script_id: (tmp_path / "test.wf", True),
    )

    worker._run_script(
        BatchScript("test", "test", wf_file="test.wf"),
        {},
        "alice",
        params={},
    )


def test_lifecycle_stage_receives_saved_workflow_parameters(
    tmp_path, monkeypatch, qapp,
):
    import lvjiang.core.daily_history as history

    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    config = BatchConfigItem(
        name="test",
        usernames=["alice"],
        workflows=BatchWorkflows(prepare_item="batch/prepare_item.wf"),
        workflow_params={"prepare_item": {
            "skip_online_role": False,
            "online_role_max_wait": 120,
        }},
    )
    worker = BatchWorker(
        ["alice"], [BatchScript("test", "test")], config,
        BatchContext(None, None, None, None), SessionManager(tmp_path), lambda: False,
    )
    received = {}

    class Engine:
        return_value = {"status": "success", "state": {}}

        def execute(self, workflow, initial_variables=None):
            received.update(initial_variables)

    monkeypatch.setattr(worker, "_create_engine", Engine)
    result = worker._run_stage(
        "prepare_item", "batch/prepare_item.wf", 0, "alice", {},
        round_number=1,
    )

    assert result.status == "success"
    assert received["skip_online_role"] is False
    assert received["online_role_max_wait"] == 120
    assert received["batch_round"] == 1


def test_batch_freezes_all_user_task_params_before_start(tmp_path, monkeypatch, qapp):
    import lvjiang.core.daily_history as history
    from lvjiang import constants

    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    reset_session_store()
    users_dir = tmp_path / "users"
    save_user_metadata(User("alice", attributes={"account": "A"}), users_dir)
    save_user_metadata(User("bob", attributes={"account": "B"}), users_dir)
    set_wf_config("test", {"count": "5"})
    set_user_workflow_params("alice", "test", {"count": "2"}, users_dir)
    worker = BatchWorker(
        ["alice", "bob"],
        [BatchScript(
            "test", "test",
            parameters=[{"name": "count", "type": "number", "default": 1}],
        )],
        BatchConfigItem(name="test", usernames=["alice", "bob"]),
        BatchContext(None, None, None, None), SessionManager(users_dir), lambda: False,
    )

    set_user_workflow_params("alice", "test", {"count": "88"}, users_dir)
    plan = worker.task_plan_snapshot()

    assert plan[(0, "test")].params == {"count": "2"}
    assert plan[(0, "test")].parameter_source == "user"
    assert plan[(1, "test")].params == {"count": "5"}
    assert plan[(1, "test")].parameter_source == "global"

    logs = []
    worker.log.connect(logs.append)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    monkeypatch.setattr(
        worker, "_run_stage", lambda *args, **kwargs: BatchStageResult(state={}))
    monkeypatch.setattr(worker, "_run_script", lambda *args, **kwargs: {})
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    worker.run()

    assert "[批量] 执行用户：alice、bob" in logs
    assert "[批量] 执行任务：test" in logs
    assert any(
        "用户=alice 任务=test 参数来源=用户独立 输入参数={\"count\": \"2\"}"
        in message for message in logs
    )
    assert any(
        "用户=bob 任务=test 参数来源=全局任务 输入参数={\"count\": \"5\"}"
        in message for message in logs
    )
    reset_session_store()
