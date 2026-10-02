"""跨层保证：执行快照冻结、配置保存不碰运行草稿。

这两条是分层之后最容易被悄悄破坏的契约，也是最难在界面上看出来的：
一旦某处又去读了一次盘，表现就是"跑着的这批和我看到的不一样"。
"""
import copy

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.batch_run import BatchRunDraft, BatchSelection, load_draft
from lvjiang.ui.batch.batch_runner import BatchRunSpec, BatchScript


def _item(**kwargs) -> BatchConfigItem:
    kwargs.setdefault("name", "日常")
    return BatchConfigItem(**kwargs)


def test_spec_is_frozen_against_later_definition_and_draft_changes():
    """点开始之后改配置组、改草稿，都不该影响已经生成的这一批。"""
    item = _item(
        task_ids=["a", "b"], usernames=["u1", "u2"],
        workflows=BatchWorkflows(prepare_item="batch/prepare_item.wf"),
        workflow_params={"prepare_item": {"max_roll_account": 50}})
    draft = BatchRunDraft(
        users=BatchSelection(order=["u1", "u2"], checked=["u1"]),
        rounds=2)

    spec = BatchRunSpec.build(
        item, draft, entries=["u1"],
        scripts=[BatchScript("a", "任务 A")])
    snapshot = copy.deepcopy(spec)

    # 启动后用户继续折腾配置与草稿
    item.name = "改名了"
    item.task_ids.append("c")
    item.workflow_params["prepare_item"]["max_roll_account"] = 8
    item.workflows.prepare_item = "别的.wf"
    draft.rounds = 99
    draft.users.checked.append("u2")

    assert spec == snapshot, "快照必须是深拷贝，不能跟着源对象变"
    assert spec.rounds == 2
    assert spec.entries == ("u1",)
    assert spec.workflow_params == {"prepare_item": {"max_roll_account": 50}}
    assert spec.workflows.prepare_item == "batch/prepare_item.wf"


def test_worker_reads_the_spec_not_the_live_config(tmp_path, monkeypatch, qapp):
    """调度器运行期不再读盘：改了 batch.json 也不影响正在跑的这一批。"""
    import lvjiang.core.daily_history as history
    from lvjiang.core.config.users import SessionManager
    from lvjiang.core.user_config import User, save_user_metadata
    from lvjiang.ui.batch.batch_report import BatchReport
    from lvjiang.ui.batch.batch_runner import (
        BatchContext,
        BatchStageResult,
        BatchWorker,
    )

    monkeypatch.setattr(history, "try_create_batch_run", lambda **_kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **_kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    save_user_metadata(User("u1"), tmp_path)

    item = _item(usernames=["u1"], skip_lifecycle_for_single_item=True)
    worker = BatchWorker(
        BatchRunSpec.build(
            item, BatchRunDraft(rounds=2), entries=["u1"],
            scripts=[BatchScript("task", "任务")]),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False)
    runs = []
    monkeypatch.setattr(
        worker, "_run_script",
        lambda _s, _session, username, **_kw: runs.append(username) or {})
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda *_a, **_kw: BatchStageResult(state={}))
    monkeypatch.setattr(worker, "_save_result", lambda *_a: None)

    # 启动后把定义改成一轮：已冻结的计划仍按两轮跑完
    item.skip_lifecycle_for_single_item = False
    worker.run()

    assert runs == ["u1", "u1"]


def test_saving_the_config_dialog_keeps_the_run_draft(monkeypatch, qtbot):
    """配置窗口保存不该覆盖用户刚在主页面调好的本次选择。

    旧版的保存路径会读盘再把主页面的字段搬回来，正是这类覆盖的来源；
    现在两层各自持久化，结构上就不会互相踩。
    """
    from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog

    item = _item(
        usernames=["u1", "u2"], default_usernames=["u1", "u2"],
        task_ids=[], default_task_ids=[])
    config = BatchConfig({item.id: item})
    # 主页面已经把本次执行调成只跑 u2
    from lvjiang.core.batch_run import save_draft
    save_draft(item.id, BatchRunDraft(
        users=BatchSelection(order=["u2", "u1"], checked=["u2"]), rounds=4))

    class _Users:
        @staticmethod
        def list_users():
            return ["u1", "u2"]

        @staticmethod
        def get_user(name):
            from lvjiang.core.user_config import User
            return User(name)

    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config",
        lambda _cfg: None)
    dialog = BatchConfigDialog(_Users())
    qtbot.addWidget(dialog)

    dialog._on_save()

    draft = load_draft(item.id)
    assert draft.users.order == ["u2", "u1"]
    assert draft.users.checked == ["u2"]
    assert draft.rounds == 4
