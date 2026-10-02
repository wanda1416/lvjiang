"""无人值守批量：弹窗改道、任务记失败、随后由恢复 wf 收回初始页。

无人值守的全部价值在「没人看着的那几个小时里不卡死」，所以这些用例盯的是
三件事：弹窗不再等人、当前任务按异常收尾且不重试、恢复 wf 拿到正确的阶段
信号（本条目还有没有没跑的任务）。
"""
from unittest.mock import MagicMock

import pytest

from lvjiang.core.batch_config import BatchConfigItem, BatchWorkflows
from lvjiang.core.config.users import SessionManager
from lvjiang.core.user_config import User, save_user_metadata
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import (
    ST_FAILED,
    ST_SUCCESS,
    BatchContext,
    BatchScript,
    BatchStageResult,
    BatchWorker,
    UnattendedInterrupt,
)

_RECOVER_WF = "batch/recover_to_login.wf"


def _config(**kwargs) -> BatchConfigItem:
    kwargs.setdefault("name", "group")
    return BatchConfigItem(**kwargs)


def test_unattended_requires_a_recovery_workflow():
    """没配恢复 wf 就不成立：撞上弹窗后没人把游戏收回初始页，整批只会连环失败。

    UI 会禁用勾选框，这里守的是手改过的 batch.json 同样不能生效。
    """
    bare = BatchConfigItem.from_dict("bare", {"unattended": True})
    assert bare.unattended is False

    ready = BatchConfigItem.from_dict("ready", {
        "unattended": True,
        "workflows": {"recover_unattended": _RECOVER_WF},
    })
    assert ready.unattended is True
    assert ready.to_dict()["unattended"] is True


def test_clearing_the_recovery_workflow_also_clears_unattended():
    item = _config(
        unattended=True,
        workflows=BatchWorkflows(recover_unattended=_RECOVER_WF))
    item.workflows.recover_unattended = ""
    item.normalize()

    assert item.unattended is False


def _worker(tmp_path, scripts, **config_kwargs) -> BatchWorker:
    save_user_metadata(User("u1"), tmp_path)
    return BatchWorker(
        ["u1"], scripts, _config(usernames=["u1"], **config_kwargs),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False,
    )


def test_notify_still_reaches_the_host_but_pause_interrupts(tmp_path, qapp):
    """notify 是非阻塞的，无人值守下照旧送到宿主；pause/confirm/input 一律中止。

    放过 notify 才能在事后从告警面板看出这批跑过什么。
    """
    seen = []
    worker = _worker(
        tmp_path, [],
        unattended=True,
        workflows=BatchWorkflows(recover_unattended=_RECOVER_WF))
    worker._ctx = BatchContext(
        None, None, None, None,
        ui_callback=lambda kind, **kw: seen.append((kind, kw)) or "ok")

    assert worker._unattended_active() is True
    assert worker._unattended_ui_callback("notify", message="提示") == "ok"
    assert seen == [("notify", {"message": "提示"})]

    for kind, kwargs in (("pause", {"message": "请手动处理"}),
                         ("confirm", {"message": "要继续吗"}),
                         ("input", {"prompt": "输入名称"})):
        with pytest.raises(UnattendedInterrupt):
            worker._unattended_ui_callback(kind, **kwargs)


def test_unattended_off_keeps_waiting_for_a_human(tmp_path, qapp):
    """没勾选时行为完全不变：弹窗还是弹窗，不能被这个特性悄悄改掉。"""
    worker = _worker(tmp_path, [])

    assert worker._unattended_active() is False


def _run_with_pause_on(tmp_path, monkeypatch, qapp, *, pause_index: int,
                       script_count: int, recover_wf: str = _RECOVER_WF):
    """让第 pause_index 个任务撞上 pause，回放整条批量。"""
    import lvjiang.core.daily_history as history
    monkeypatch.setattr(history, "try_create_batch_run", lambda **_kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **_kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)

    scripts = [BatchScript(f"task{i}", f"任务{i}") for i in range(script_count)]
    worker = _worker(
        tmp_path, scripts,
        unattended=True,
        workflows=BatchWorkflows(recover_unattended=recover_wf))
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
        tmp_path, monkeypatch, qapp, pause_index=0, script_count=2)

    # 失败的任务只跑一次，后面的任务不受影响
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
        tmp_path, monkeypatch, qapp, pause_index=1, script_count=2)

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
        tmp_path, [BatchScript("task0", "任务0")],
        unattended=True,
        workflows=BatchWorkflows(recover_unattended=_RECOVER_WF))
    stages: list[str] = []
    monkeypatch.setattr(
        worker, "_run_script",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("识别失败")))
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda phase, *_a, **_kw: stages.append(phase) or BatchStageResult(state={}))
    monkeypatch.setattr(worker, "_save_result", lambda *_args: None)
    worker.run()

    assert "recover_unattended" not in stages


# ─── 配置界面：勾选框依赖恢复 wf ───────────────────────────

class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return ["用户A"]


def _dialog(monkeypatch, qtbot, item: BatchConfigItem):
    from lvjiang.core.batch_config import BatchConfig
    from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog

    cfg = BatchConfig(configs={item.name: item}, active_config=item.name)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config", lambda: cfg)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config",
        lambda _cfg: None)
    dialog = BatchConfigDialog(_Users())
    qtbot.addWidget(dialog)
    return dialog


def test_dialog_blocks_unattended_until_a_recovery_workflow_is_chosen(
    monkeypatch, qtbot,
):
    """没配恢复 wf 时勾选框禁用并说明原因——禁用而不是隐藏，能力一直都在。"""
    dialog = _dialog(monkeypatch, qtbot, _config(
        name="日常", usernames=["用户A"]))

    assert dialog._unattended.isEnabled() is False
    assert "异常恢复" in dialog._unattended.toolTip()

    dialog._selectors["recover_unattended"].setCurrentText(_RECOVER_WF)

    assert dialog._unattended.isEnabled() is True


def test_dialog_unchecks_unattended_when_the_recovery_workflow_is_removed(
    monkeypatch, qtbot,
):
    """清掉恢复 wf 不能留下「勾着但不生效」的状态。"""
    dialog = _dialog(monkeypatch, qtbot, _config(
        name="日常", usernames=["用户A"], unattended=True,
        workflows=BatchWorkflows(recover_unattended=_RECOVER_WF)))

    assert dialog._unattended.isChecked() is True

    dialog._selectors["recover_unattended"].setCurrentText("")

    assert dialog._unattended.isChecked() is False
    assert dialog._unattended.isEnabled() is False


def test_stage_abort_is_recorded_as_a_redirect_not_a_stage_error(
    tmp_path, monkeypatch, qapp,
):
    """阶段 wf 撞弹窗时按失败记账，但日志级别与文案都不是「阶段失败」。

    中止是无人值守的预期分支。记成阶段异常会在排障时把一次正常改道混进真正
    的 wf 故障里，而这批任务跑一夜可能撞上几十次。
    """
    import lvjiang.ui.batch.batch_runner as runner_mod

    worker = _worker(
        tmp_path, [],
        unattended=True,
        workflows=BatchWorkflows(recover_unattended=_RECOVER_WF))
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
