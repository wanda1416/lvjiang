from lvjiang.core.batch_config import BatchConfigItem, BatchWorkflows
from lvjiang.ui.batch.batch_runner import (
    RESULT_FAILED,
    RESULT_SKIPPED,
    RESULT_SUCCESS,
    ST_FAILED,
    BatchContext,
    BatchScript,
    BatchStageResult,
    BatchWorker,
)
from lvjiang.workflows.base import BaseWorkflow


def test_stage_result_accepts_standard_dict_and_keeps_private_state():
    private_state = {"account": "A", "page_state": 1}

    result = BatchWorker._normalize_stage_result({
        "status": "skipped",
        "message": "业务自行跳过",
        "state": private_state,
        "extra": "控制层不解释",
    }, {})

    assert result.status == RESULT_SKIPPED
    assert result.message == "业务自行跳过"
    assert result.state is private_state


def test_stage_result_defaults_to_success_and_current_state():
    state = {"opaque": True}

    result = BatchWorker._normalize_stage_result(None, state)

    assert result.status == RESULT_SUCCESS
    assert result.state is state


def test_stage_result_rejects_unknown_status_without_accepting_state():
    current = {"safe": True}

    result = BatchWorker._normalize_stage_result({
        "status": "retry-later",
        "state": {"unsafe": True},
    }, current)

    assert result.status == RESULT_FAILED
    assert result.state is current


def test_stage_result_rejects_non_dict_state():
    result = BatchWorker._normalize_stage_result({
        "status": "success",
        "state": "leaked business value",
    }, {})

    assert result.status == RESULT_FAILED


def test_unknown_result_status_falls_back_to_failed_ui_status():
    assert BatchWorker._result_to_ui_status("future-status") == ST_FAILED


def test_stage_mutation_without_return_is_not_committed():
    class Engine:
        return_value = None
        session = {}

        def execute(self, _wf_path, initial_variables):
            initial_variables["batch_state"]["leaked"] = True

    class Worker(BatchWorker):
        def _create_engine(self):
            return Engine()

    class Sessions:
        pass

    worker = Worker(
        enabled_rows=[],
        scripts=[],
        config=BatchConfigItem(name="test"),
        ctx=BatchContext(object(), object(), object(), object()),
        session_manager=Sessions(),
        stop_check=lambda: False,
    )
    original = {"stable": True}

    result = worker._run_stage(
        "prepare_item", "batch/prepare_item.wf", 0, 0, {}, original)

    assert original == {"stable": True}
    assert result.state is original


def test_skipped_item_state_is_passed_to_next_item(monkeypatch):
    seen_states = []
    executed_rows = []

    class Worker(BatchWorker):
        def _run_stage(self, phase, wf_name, run_idx, source_idx, row_data,
                       batch_state, item_result=None):
            if phase == "prepare_item":
                seen_states.append(dict(batch_state))
                if run_idx == 0:
                    return BatchStageResult(
                        status=RESULT_SKIPPED,
                        message="skip first",
                        state={"opaque_cursor": "after-first"},
                    )
            return BatchStageResult(state=batch_state)

        def _run_script(self, script, session, username):
            executed_rows.append(username)
            return {}

        def _save_result(self, username, script, result):
            pass

    class Sessions:
        def load(self, username):
            return {}

        def save(self, username, session):
            pass

    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_runner.BatchReport.write", lambda self: None)
    worker = Worker(
        enabled_rows=[(3, {"user": "first"}), (8, {"user": "second"})],
        scripts=[BatchScript(id="task", name="Task")],
        config=BatchConfigItem(
            name="test",
            columns=["user"],
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
            user_column="user",
        ),
        ctx=BatchContext(object(), object(), object(), object()),
        session_manager=Sessions(),
        stop_check=lambda: False,
    )

    worker.run()

    assert seen_states == [{}, {"opaque_cursor": "after-first"}]
    assert executed_rows == ["second"]


def test_run_script_class_branch_propagates_pause_event(monkeypatch):
    """Python 类批量脚本必须收到 pause_event，否则 F8 暂停对它完全失效
    （回归 batch_runner.py 历史 bug：曾只传 stop_check，漏传 pause_event）。"""
    import lvjiang.workflows.implementations as impl_module

    captured = {}

    class FakeWorkflow(BaseWorkflow):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            captured["pause_event"] = kwargs.get("pause_event")

        def run(self):
            return {}

    monkeypatch.setattr(impl_module, "get_workflow_class", lambda name: FakeWorkflow)
    monkeypatch.setattr(
        "lvjiang.core.config.wf_configs.get_wf_config", lambda script_id: {})

    sentinel_pause_event = object()
    ctx = BatchContext(object(), object(), object(), object(),
                       pause_event=sentinel_pause_event)

    class Sessions:
        pass

    worker = BatchWorker(
        enabled_rows=[],
        scripts=[],
        config=BatchConfigItem(name="test"),
        ctx=ctx,
        session_manager=Sessions(),
        stop_check=lambda: False,
    )

    worker._run_script(
        BatchScript(id="task", name="Task", class_name="Fake"), {}, None)

    assert captured["pause_event"] is sentinel_pause_event


def test_failed_batch_setup_starts_no_later_stage(monkeypatch):
    phases = []

    class Worker(BatchWorker):
        def _run_stage(self, phase, wf_name, run_idx, source_idx, row_data,
                       batch_state, item_result=None):
            phases.append(phase)
            return BatchStageResult(
                status=RESULT_FAILED,
                message="site unavailable",
                state=batch_state,
            )

    class Sessions:
        pass

    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_runner.BatchReport.write", lambda self: None)
    worker = Worker(
        enabled_rows=[(0, {"user": "first"})],
        scripts=[BatchScript(id="task", name="Task")],
        config=BatchConfigItem(
            name="test",
            columns=["user"],
            workflows=BatchWorkflows(
                batch_setup="setup.wf",
                prepare_item="prepare.wf",
                batch_teardown="teardown.wf",
            ),
            user_column="user",
        ),
        ctx=BatchContext(object(), object(), object(), object()),
        session_manager=Sessions(),
        stop_check=lambda: False,
    )

    worker.run()

    assert phases == ["batch_setup"]
