"""用户声明跳过：任务列照旧生成，执行时直接记「跳过」。"""
import pytest

from lvjiang.core.batch.config import BatchWorkflows
from lvjiang.core.config.users import SessionManager
from lvjiang.core.users.document import User, save_user_metadata
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import (
    ST_SKIPPED,
    BatchCheckResult,
    BatchContext,
    BatchRunSpec,
    BatchScript,
    BatchStageResult,
    BatchWorker,
)


def _make_worker(tmp_path, monkeypatch, spec, *, check=None):
    import lvjiang.core.history.daily as history

    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    monkeypatch.setattr(BatchWorker, "_save_result", lambda *args: None)
    worker = BatchWorker(
        spec, BatchContext(None, None, None, None),
        SessionManager(tmp_path), lambda: False,
    )
    if check is not None:
        monkeypatch.setattr(worker, "_check_script", check)
    return worker


def _user(tmp_path, name, **attributes):
    save_user_metadata(
        User(name=name, attributes={k: str(v) for k, v in attributes.items()}),
        tmp_path)


@pytest.fixture
def users(tmp_path):
    for name in ("alice", "bob"):
        _user(tmp_path, name, team="red")
    return tmp_path


def test_declared_skip_keeps_task_row_and_records_skipped(
    users, monkeypatch, qapp,
):
    """被跳过的组合仍在计划与进度里，只是不执行、记「跳过」。"""
    skipped_checks = []

    def check(script, username, params=None):
        skipped_checks.append(script.id)
        return BatchCheckResult()

    spec = BatchRunSpec(
        name="test",
        entries=("alice",),
        candidate_usernames=("alice",),
        scripts=(BatchScript("a", "任务甲"), BatchScript("b", "任务乙")),
        excluded={"alice": ("b",)},
        skip_lifecycle_for_single_item=False,
    )
    worker = _make_worker(users, monkeypatch, spec, check=check)

    # 计划仍为两个组合生成（任务列不因跳过而减少）。
    assert (0, "b") in worker.task_plan_snapshot()
    progress = []
    executed = []
    summaries = []
    worker.progress.connect(
        lambda round_number, index, user, script, status: progress.append(
            (script, status)))
    def stage(stage_name, wf, index=-1, username="", state=None,
              item_result=None, **kwargs):
        if stage_name == "finish_item" and item_result is not None:
            summaries.append(item_result)
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(
        worker, "_run_script",
        lambda script, session, username, **kwargs: executed.append(script.id) or {})

    worker.run()

    # 声明跳过的组合不执行，也不跑可执行性检查。
    assert executed == ["a"]
    assert skipped_checks == ["a"]
    # 进度表里这一格是「跳过」，不是没跑到。
    assert ("b", ST_SKIPPED) in progress
    assert ("a", ST_SKIPPED) not in progress
    # 条目收尾拿到的摘要里，跳过项仍是 skipped（协议不变）。
    assert summaries and summaries[0]["scripts"]["b"] == "skipped"


def test_user_with_all_tasks_declared_skipped_is_ignored_without_login(
    users, monkeypatch, qapp,
):
    """全部声明跳过时不登录该用户，只跑剩下的条目。"""
    spec = BatchRunSpec(
        name="test",
        entries=("alice", "bob"),
        candidate_usernames=("alice", "bob"),
        scripts=(BatchScript("a", "任务甲"),),
        excluded={"alice": ("a",)},
        skip_lifecycle_for_single_item=False,
    )
    worker = _make_worker(users, monkeypatch, spec)
    prepared = []
    executed = []
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda stage, *args, **kwargs: (
            prepared.append(kwargs.get("username"))
            if stage == "prepare_item" else None) or BatchStageResult(state={}),
    )
    monkeypatch.setattr(
        worker, "_run_script",
        lambda script, session, username, **kwargs: executed.append(
            username) or {})

    worker.run()

    # 声明跳过的用户不进入条目准备；另一个用户照常执行。
    assert "alice" not in prepared
    assert executed == ["bob"]


def test_attr_unit_member_skipped_for_every_task_is_not_eligible(
    users, monkeypatch, qapp,
):
    """属性单元里任务被全部跳过的成员不会被选为执行角色。"""
    spec = BatchRunSpec(
        name="test",
        entries=("red",),
        candidate_usernames=("alice", "bob"),
        scripts=(BatchScript("a", "任务甲"),),
        execution_unit_key="team",
        workflows=BatchWorkflows(prepare_item="batch/prepare_item_by_attr.wf"),
        excluded={"alice": ("a",)},
        skip_lifecycle_for_single_item=False,
    )
    worker = _make_worker(users, monkeypatch, spec)
    prepared = []
    executed = []

    def stage(stage_name, wf, index=-1, username="", *args, **kwargs):
        if stage_name == "prepare_item":
            prepared.append(kwargs.get("unit_members"))
            return BatchStageResult(state={}, username="bob")
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(
        worker, "_run_script",
        lambda script, session, username, **kwargs: executed.append(
            username) or {})

    worker.run()

    # alice 的全部任务都被跳过，所以单元准备只在 bob 上调用。
    assert prepared == [["bob"]]
    assert executed == ["bob"]
