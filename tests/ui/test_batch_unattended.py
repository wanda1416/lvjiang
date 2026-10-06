"""无人值守批量：弹窗改道、任务记失败、随后由恢复 wf 收回初始页。

这些用例保护：pause 异常中断，正常确认/输入/选择仍等待用户，当前异常任务
不重试，以及恢复 wf 拿到正确的阶段
信号（本条目还有没有没跑的任务）。

「本次是否无人值守」属于运行草稿（今晚没人看），而「异常恢复 wf」属于配置组
定义（恢复能力本身）——有效值由执行快照把两者取与得出。
"""
from unittest.mock import MagicMock

import pytest

from lvjiang.core.batch_config import BatchConfigItem, BatchWorkflows
from lvjiang.core.batch_run import BatchRunDraft
from lvjiang.core.config.users import SessionManager
from lvjiang.core.user_config import User, save_user_metadata
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import (
    ST_FAILED,
    ST_SUCCESS,
    BatchContext,
    BatchRunSpec,
    BatchScript,
    BatchStageResult,
    BatchWorker,
    UnattendedInterrupt,
)

_RECOVER_WF = "batch/recover_to_login.wf"


def _spec(**kwargs) -> BatchRunSpec:
    """按"定义 + 草稿 → 快照"的真实路径构造，有效值不手写。"""
    recover = kwargs.pop("recover_wf", _RECOVER_WF)
    unattended = kwargs.pop("unattended", True)
    scripts = kwargs.pop("scripts", [])
    entries = kwargs.pop("entries", ["u1"])
    item = BatchConfigItem(
        name="group", usernames=list(entries),
        workflows=BatchWorkflows(recover_unattended=recover), **kwargs)
    return BatchRunSpec.build(
        item, BatchRunDraft(unattended=unattended),
        entries=entries, scripts=scripts)


def test_effective_unattended_requires_a_recovery_workflow():
    """没配恢复 wf 就不成立：撞上弹窗后没人把游戏收回初始页，整批只会连环失败。

    这个与运算只在构造执行快照时做一次，调度器不必再判断前提。
    """
    assert _spec(unattended=True, recover_wf="").unattended is False
    assert _spec(unattended=True).unattended is True
    assert _spec(unattended=False).unattended is False


def _worker(tmp_path, spec) -> BatchWorker:
    save_user_metadata(User("u1"), tmp_path)
    return BatchWorker(
        spec, BatchContext(None, None, None, None),
        SessionManager(tmp_path), lambda: False)


def test_only_pause_interrupts_and_other_interactions_keep_host_results(tmp_path, qapp):
    """用户决策等待并原样返回；只有 pause 触发异常恢复。"""
    seen = []
    worker = _worker(tmp_path, _spec())
    results = {"notify": None, "confirm": False, "input": "12", "choose": "kept"}
    worker._ctx = BatchContext(
        None, None, None, None,
        ui_callback=lambda kind, **kw: seen.append((kind, kw)) or results[kind])

    assert worker._unattended_active() is True
    for kind, kwargs in (("notify", {"message": "提示"}),
                         ("confirm", {"message": "要继续吗"}),
                         ("input", {"prompt": "输入数量"}),
                         ("choose", {"message": "选择结果", "cancel_value": "end"})):
        assert worker._unattended_ui_callback(kind, **kwargs) == results[kind]
        assert seen[-1] == (kind, kwargs)
        assert not worker._unattended_hit

    with pytest.raises(UnattendedInterrupt):
        worker._unattended_ui_callback("pause", message="请手动处理")
    assert worker._unattended_hit == "请手动处理"
    assert len(seen) == 4


def test_unattended_off_keeps_waiting_for_a_human(tmp_path, qapp):
    """没勾选时行为完全不变：弹窗还是弹窗，不能被这个特性悄悄改掉。"""
    assert _worker(tmp_path, _spec(unattended=False))._unattended_active() is False


def _run_with_pause_on(tmp_path, monkeypatch, *, pause_index: int,
                       script_count: int, recover_wf: str = _RECOVER_WF):
    """让第 pause_index 个任务撞上 pause，回放整条批量。"""
    import lvjiang.core.daily_history as history
    monkeypatch.setattr(history, "try_create_batch_run", lambda **_kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **_kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)

    scripts = [BatchScript(f"task{i}", f"任务{i}") for i in range(script_count)]
    worker = _worker(
        tmp_path, _spec(scripts=scripts, recover_wf=recover_wf))
    executed: list[str] = []
    stages: list[tuple[str, dict]] = []
    statuses: list[tuple[str, str]] = []
    worker.progress.connect(
        lambda _idx, _label, script_id, status: statuses.append(
            (script_id, status)))

    def run_script(script, _session, _username, **_kwargs):
        executed.append(script.id)
        if script.id == f"task{pause_index}":
            # 真实路径：引擎的 pause 走 _ui_callback，无人值守下由它抛中止
            worker._unattended_ui_callback("pause", message="未进入菜单页")
        return {}

    def run_stage(phase, _wf, _idx, _username, _state, *_args, **kwargs):
        stages.append((phase, kwargs.get("extra_variables") or {}))
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_script", run_script)
    monkeypatch.setattr(worker, "_run_stage", run_stage)
    monkeypatch.setattr(worker, "_save_result", lambda *_args: None)
    worker.run()
    return executed, stages, statuses


def test_pause_fails_the_task_without_retry_and_recovers_for_the_rest(
    tmp_path, monkeypatch, qapp,
):
    """撞弹窗的任务按异常记失败、不重试，后面的任务照常继续。

    「还有没跑的任务」要传给恢复 wf：这时它不能只停在登录主页，否则下一项
    任务会在登录页上必然失败，白跑一轮。
    """
    executed, stages, statuses = _run_with_pause_on(
        tmp_path, monkeypatch, pause_index=0, script_count=2)

    assert executed == ["task0", "task1"]
    assert ("task0", ST_FAILED) in statuses
    assert ("task1", ST_SUCCESS) in statuses

    recoveries = [vars_ for phase, vars_ in stages
                  if phase == "recover_unattended"]
    assert len(recoveries) == 1
    assert recoveries[0]["batch_recover_pending"] is True
    assert recoveries[0]["batch_recover_username"] == "u1"


def test_recovery_only_returns_to_login_when_nothing_is_left(
    tmp_path, monkeypatch, qapp,
):
    """最后一个任务撞弹窗时不必再登回游戏主页，停在登录主页等下一个用户。"""
    _executed, stages, _statuses = _run_with_pause_on(
        tmp_path, monkeypatch, pause_index=1, script_count=2)

    recoveries = [vars_ for phase, vars_ in stages
                  if phase == "recover_unattended"]
    assert len(recoveries) == 1
    assert recoveries[0]["batch_recover_pending"] is False


def test_task_failing_for_other_reasons_does_not_restart_the_client(
    tmp_path, monkeypatch, qapp,
):
    """只有确实撞了弹窗才恢复：普通失败时画面通常还可用，没必要重启客户端。"""
    import lvjiang.core.daily_history as history
    monkeypatch.setattr(history, "try_create_batch_run", lambda **_kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **_kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)

    worker = _worker(
        tmp_path, _spec(scripts=[BatchScript("task0", "任务0")]))
    stages: list[str] = []
    monkeypatch.setattr(
        worker, "_run_script",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("识别失败")))
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda phase, *_a, **_kw: stages.append(phase) or BatchStageResult(
            state={}))
    monkeypatch.setattr(worker, "_save_result", lambda *_args: None)
    worker.run()

    assert "recover_unattended" not in stages


def test_stage_abort_is_recorded_as_a_redirect_not_a_stage_error(
    tmp_path, monkeypatch, qapp,
):
    """阶段 wf 撞弹窗时按失败记账，但日志级别与文案都不是「阶段失败」。

    中止是无人值守的预期分支。记成阶段异常会在排障时把一次正常改道混进真正
    的 wf 故障里，而这批任务跑一夜可能撞上几十次。
    """
    import lvjiang.ui.batch.batch_runner as runner_mod

    worker = _worker(tmp_path, _spec())
    engine = MagicMock()
    engine.execute.side_effect = UnattendedInterrupt("无法进入选择角色页面")
    monkeypatch.setattr(worker, "_create_engine", lambda: engine)
    errors: list = []
    infos: list = []
    monkeypatch.setattr(runner_mod.logger, "error", errors.append)
    monkeypatch.setattr(runner_mod.logger, "info", infos.append)

    result = worker._run_stage(
        "prepare_item", _RECOVER_WF, 0, "u1", {})

    assert result.status != ST_SUCCESS
    assert result.message.startswith("无人值守中止")
    assert errors == []
    assert any("无人值守中止" in line for line in infos)
