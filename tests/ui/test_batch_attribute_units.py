"""属性单元保留旧配置，并把实际角色身份贯穿批量执行。"""
from contextlib import nullcontext

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.batch_units import group_users
from lvjiang.core.config.users import SessionManager
from lvjiang.core.user_config import User, save_user_metadata
from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import (
    ST_FAILED,
    ST_RUNNING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchCheckResult,
    BatchContext,
    BatchScript,
    BatchStageResult,
    BatchWorker,
)
from lvjiang.ui.batch.batch_tab import BatchTab


def test_old_batch_config_stays_in_user_mode():
    item = BatchConfigItem.from_dict("old", {
        "usernames": ["u1", "u2"],
        "selected_usernames": ["u2"],
    })
    assert item.execution_unit_key == "user"
    assert item.usernames == ["u1", "u2"]
    assert item.selected_usernames == ["u2"]


def test_attribute_units_include_all_registered_members(tmp_path):
    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    save_user_metadata(User("u2", attributes={"account": "a"}), tmp_path)
    save_user_metadata(User("u3", attributes={"account": "b"}), tmp_path)
    assert group_users(["u1", "u2", "u3"], "account", tmp_path) == {
        "a": ["u1", "u2"], "b": ["u3"],
    }


def test_config_dialog_switches_unit_without_replacing_user_selection(
    monkeypatch, qtbot,
):
    users = {
        "u1": User("u1", attributes={"account": "a"}),
        "u2": User("u2", attributes={"account": "a"}),
    }

    class Manager:
        def list_users(self):
            return list(users)

        def get_user(self, name):
            return users[name]

    config = BatchConfig(configs={
        "group": BatchConfigItem(
            name="group", usernames=["u1", "u2"],
            selected_usernames=["u1"],
        ),
    }, active_config="group")
    saved = []
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config", saved.append)
    dialog = BatchConfigDialog(Manager())
    qtbot.addWidget(dialog)
    dialog._unit_combo.setCurrentIndex(dialog._unit_combo.findData("account"))
    assert dialog._user_list.count() == 1
    dialog._on_save()

    group = saved[0].configs["group"]
    assert group.execution_unit_key == "account"
    assert group.visible_units["account"] == ["a"]
    assert group.usernames == ["u1", "u2"]
    assert group.selected_usernames == ["u1"]


def test_main_tab_filters_attribute_units_without_changing_user_mode(
    monkeypatch, qtbot,
):
    class Host(QObject):
        automation_state_changed = pyqtSignal(str)
        is_running = False

        class _Config:
            class hotkeys:
                start = "F9"
                pause = "F10"

        _user_config = _Config()

        @staticmethod
        def _selected_run_env():
            return None

    config = BatchConfig(configs={
        "group": BatchConfigItem(
            name="group", usernames=["u1", "u2"],
            selected_usernames=["u1"], execution_unit_key="account",
            visible_units={"account": ["a", "b"]},
            selected_units={"account": ["b"]},
        ),
    }, active_config="group")
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", lambda _cfg: None)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _env: [])
    tab = BatchTab(Host())
    qtbot.addWidget(tab)

    assert tab._unit_label.text() == "<b>选择执行单元</b>"
    assert tab._user_list.headerItem().text(0) == "单元候选（account）"
    assert tab._get_enabled_usernames() == ["b"]
    tab._user_list.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    assert config.configs["group"].selected_units["account"] == ["a", "b"]
    assert config.configs["group"].selected_usernames == ["u1"]


def test_attribute_prepare_selects_real_user_for_task_and_session(
    tmp_path, monkeypatch, qapp,
):
    import lvjiang.core.daily_history as history

    for username in ("u1", "u2"):
        save_user_metadata(User(
            username, attributes={"account": "a", "role": username,
                                  "role_index": "1" if username == "u1" else "2"}),
            tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    config = BatchConfigItem(
        name="attribute", execution_unit_key="account",
        visible_units={"account": ["a"]},
        selected_units={"account": ["a"]},
        workflows=BatchWorkflows(prepare_item="batch/prepare_item_by_attr.wf"),
    )
    worker = BatchWorker(
        ["a"], [BatchScript("task", "task")], config,
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1", "u2"],
    )
    seen = []

    def stage(phase, _wf, _index, username, _state, *args, **kwargs):
        if phase == "prepare_item":
            assert username == ""
            assert kwargs["unit_members"] == ["u1", "u2"]
            return BatchStageResult(username="u2", state={})
        if phase == "finish_item":
            assert username == "u2"
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(worker, "_run_script", lambda _script, _session, username,
                        **_kw: seen.append(username) or {})
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    worker.run()
    assert seen == ["u2"]


def test_online_deferral_does_not_consume_unit_round(tmp_path, monkeypatch, qapp):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["a"], [BatchScript("task", "task")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account", rounds=1,
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    prepares = []
    executed = []

    def stage(phase, _wf, _index, _username, _state, *args, **kwargs):
        if phase == "prepare_item":
            prepares.append(kwargs["round_number"])
            if len(prepares) == 1:
                return BatchStageResult(
                    status="skipped", state={}, retry_after=0.001)
            return BatchStageResult(username="u1", state={})
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(worker, "_run_script", lambda _s, _session, username,
                        **_kw: executed.append(username) or {})
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    worker.run()

    assert prepares == [1, 1]
    assert executed == ["u1"]


def test_attribute_unit_runs_only_selected_members_successful_checks(
    tmp_path, monkeypatch, qapp,
):
    import lvjiang.core.daily_history as history

    for username in ("u1", "u2"):
        save_user_metadata(
            User(username, attributes={"account": "a"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    task_runs = []

    class TaskRun:
        def __init__(self, task_id):
            self.task_id = task_id
            self.finishes = []
            task_runs.append(self)

        def capture_logs(self):
            return nullcontext()

        def finish(self, **kwargs):
            self.finishes.append(kwargs)

    monkeypatch.setattr(
        history, "try_create_task_run",
        lambda **kw: TaskRun(kw["task_id"]),
    )
    reports = []
    monkeypatch.setattr(BatchReport, "write", lambda self: reports.append(self) or None)
    worker = BatchWorker(
        ["a"], [BatchScript(key, key) for key in ("A", "B", "C")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account",
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1", "u2"],
    )
    checks = {
        "u1": {"A": "success", "B": "skipped", "C": "failed"},
        "u2": {"A": "skipped", "B": "success", "C": "failed"},
    }
    seen_checks = []
    executed = []
    progress = []
    finished = []
    worker.progress.connect(lambda _i, _label, key, status: progress.append((key, status)))
    worker.finished_all.connect(finished.append)

    def check(script, username, **_kwargs):
        seen_checks.append((username, script.id))
        return BatchCheckResult(
            status=checks[username][script.id], message="检查结果")

    def stage(phase, _wf, _index, _username, _state, *args, **kwargs):
        if phase == "prepare_item":
            assert kwargs["unit_members"] == ["u1", "u2"]
            assert seen_checks == [
                (name, key) for name in ("u1", "u2")
                for key in ("A", "B", "C")
            ]
            return BatchStageResult(username="u2", state={})
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_check_script", check)
    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(
        worker, "_run_script",
        lambda script, _session, username, **_kw:
            executed.append((username, script.id)) or {},
    )
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    worker.run()

    assert executed == [("u2", "B")]
    entry = next(iter(finished[0]["entries"].values()))
    assert entry["scripts"] == {"A": ST_SKIPPED, "B": ST_SUCCESS, "C": ST_FAILED}
    assert progress[-3:] == [
        ("B", ST_RUNNING), ("B", ST_SUCCESS), ("C", ST_FAILED),
    ]
    assert [(run.task_id, run.finishes[0]["status"]) for run in task_runs] == [
        ("A", "skipped"), ("B", "completed"), ("C", "failed"),
    ]
    assert [record.status for record in reports[0]._entries[0].scripts] == [
        ST_SKIPPED, ST_SUCCESS, ST_FAILED,
    ]


@pytest.mark.parametrize("failure", ["result", "history"])
def test_attribute_persistence_failure_does_not_change_business_result(
    tmp_path, monkeypatch, qapp, failure,
):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    task_runs = []

    class TaskRun:
        def __init__(self):
            self.finishes = []
            task_runs.append(self)

        def capture_logs(self):
            return nullcontext()

        def finish(self, **kwargs):
            self.finishes.append(kwargs)
            if failure == "history" and kwargs["status"] == "completed":
                raise OSError("history write failed")

    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: TaskRun())
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["a"], [BatchScript("A", "A"), BatchScript("B", "B")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account",
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda phase, *_args, **_kwargs: BatchStageResult(
            username="u1" if phase == "prepare_item" else "", state={}),
    )
    executed = []
    finished = []
    worker.finished_all.connect(finished.append)
    monkeypatch.setattr(
        worker, "_run_script",
        lambda script, _session, _username, **_kw:
            executed.append(script.id) or {},
    )

    def save(_username, script, _result):
        if failure == "result" and script.id == "A":
            raise OSError("result write failed")
        return None

    monkeypatch.setattr(worker, "_save_result", save)
    worker.run()

    assert executed == ["A", "B"]
    entry = next(iter(finished[0]["entries"].values()))
    assert entry["scripts"] == {"A": ST_SUCCESS, "B": ST_SUCCESS}
    assert [run.finishes for run in task_runs] == [
        [{"status": "completed", "result_path": None}],
        [{"status": "completed", "result_path": None}],
    ]


def test_attribute_failed_task_history_failure_does_not_stop_next_task(
    tmp_path, monkeypatch, qapp,
):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    finishes = []

    class TaskRun:
        def capture_logs(self):
            return nullcontext()

        def finish(self, **kwargs):
            finishes.append(kwargs["status"])
            if kwargs["status"] == "failed":
                raise OSError("history write failed")

    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: TaskRun())
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["a"], [BatchScript("A", "A"), BatchScript("B", "B")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account",
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda phase, *_args, **_kwargs: BatchStageResult(
            username="u1" if phase == "prepare_item" else "", state={}),
    )
    executed = []
    finished = []
    worker.finished_all.connect(finished.append)

    def run_script(script, _session, _username, **_kwargs):
        executed.append(script.id)
        if script.id == "A":
            raise RuntimeError("business failed")
        return {}

    monkeypatch.setattr(worker, "_run_script", run_script)
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    worker.run()

    assert executed == ["A", "B"]
    assert finishes == ["failed", "completed"]
    entry = next(iter(finished[0]["entries"].values()))
    assert entry["scripts"] == {"A": ST_FAILED, "B": ST_SUCCESS}


def test_attribute_batch_output_failure_still_emits_summary(
    tmp_path, monkeypatch, qapp,
):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)

    class BatchRun:
        batch_run_id = "batch"
        repository = None

        def finish(self, **_kwargs):
            raise OSError("batch history write failed")

    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: BatchRun())
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(
        BatchReport, "write",
        lambda self: (_ for _ in ()).throw(OSError("report write failed")),
    )
    worker = BatchWorker(
        ["a"], [BatchScript("A", "A")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account",
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    monkeypatch.setattr(
        worker, "_run_stage",
        lambda phase, *_args, **_kwargs: BatchStageResult(
            username="u1" if phase == "prepare_item" else "", state={}),
    )
    monkeypatch.setattr(worker, "_run_script", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    finished = []
    worker.finished_all.connect(finished.append)
    worker.run()

    assert next(iter(finished[0]["entries"].values()))["scripts"] == {
        "A": ST_SUCCESS,
    }
