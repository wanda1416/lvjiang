"""属性单元保留旧配置，并把实际角色身份贯穿批量执行。"""
from contextlib import nullcontext

import pytest
from PyQt6.QtCore import QObject, Qt, pyqtSignal

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
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
    attribute_item = BatchConfigItem.from_dict("attribute", {
        "execution_unit_key": "account", "selected_units": {"account": ["a"]},
        "visible_units": {"account": ["stale"]},
    })
    assert attribute_item.selected_units == {"account": ["a"]}
    assert "visible_units" not in attribute_item.to_dict()


def test_attribute_worker_members_follow_visible_users(tmp_path, qapp):
    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    save_user_metadata(User("u2", attributes={"account": "a"}), tmp_path)
    worker = BatchWorker(
        ["a"], [], BatchConfigItem(
            name="group", usernames=["u1"], execution_unit_key="account"),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    assert worker._unit_members == {"a": ["u1"]}
    assert "u2" not in worker._user_attributes


def test_config_dialog_edits_only_visible_users(
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
    assert dialog._user_list.count() == 2
    dialog._user_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    dialog._on_save()

    group = saved[0].configs["group"]
    assert group.execution_unit_key == "user"
    assert group.usernames == ["u1"]
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
        _user_manager = None

        @staticmethod
        def _selected_run_env():
            return None

    users = {
        "u1": User("u1", attributes={"account": "a"}),
        "u2": User("u2", attributes={"account": "b"}),
        "u3": User("u3", attributes={"account": "a"}),
    }

    class Manager:
        def get_user(self, name):
            return users[name]

    host = Host()
    host._user_manager = Manager()
    config = BatchConfig(configs={
        "group": BatchConfigItem(
            name="group", usernames=["u1", "u2"],
            selected_usernames=["u1"], execution_unit_key="account",
            selected_units={"account": ["b"]},
        ),
    }, active_config="group")
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", lambda _cfg: None)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _env: [])
    tab = BatchTab(host)
    qtbot.addWidget(tab)

    assert tab._unit_label.text() == "<b>选择执行单元</b>"
    assert tab._unit_combo.currentData() == "account"
    assert tab._user_list.headerItem().text(0) == "单元候选（account）"
    assert tab._get_enabled_usernames() == ["b"]
    tab._user_list.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    assert config.configs["group"].selected_units["account"] == ["a", "b"]
    assert config.configs["group"].selected_usernames == ["u1"]
    tab._unit_combo.setCurrentIndex(tab._unit_combo.findData("user"))
    assert tab._user_list.headerItem().text(0) == "单元候选（用户名）"
    assert tab._get_enabled_usernames() == ["u1"]


def test_start_is_blocked_when_prepare_wf_cannot_select_a_unit_user(
    monkeypatch, qtbot,
):
    """按属性调度、准备 wf 不会回传 username 时，开始前就要拦住。

    以前要等跑起来才抛「未返回本单元可执行的用户名」，而且是每个单元各抛一次、
    全部被丢弃，整批空跑还动过客户端。
    """
    class Host(QObject):
        automation_state_changed = pyqtSignal(str)
        is_running = False

        class _Config:
            class hotkeys:
                start = "F9"
                pause = "F10"

        _user_config = _Config()
        _user_manager = None
        logs: list[str] = []

        @staticmethod
        def _selected_run_env():
            return None

        def append_log(self, text):
            self.logs.append(text)

        def run_batch(self, _usernames, _scripts):
            raise AssertionError("不该走到启动批量")

    users = {"u1": User("u1", attributes={"account": "a"})}

    class Manager:
        def get_user(self, name):
            return users[name]

    host = Host()
    host._user_manager = Manager()
    config = BatchConfig(configs={
        "group": BatchConfigItem(
            name="group", usernames=["u1"], selected_usernames=["u1"],
            execution_unit_key="account", selected_units={"account": ["a"]},
            task_ids=["t1"], selected_task_ids=["t1"],
            workflows=BatchWorkflows(prepare_item="batch/prepare_item.wf"),
        ),
    }, active_config="group")
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", lambda _cfg: None)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts",
        lambda _env=None: [{"id": "t1", "name": "任务一", "batchable": True,
                            "scope": "daily", "path": "t1.wf"}])
    tab = BatchTab(host)
    qtbot.addWidget(tab)

    warnings = []
    monkeypatch.setattr(
        "PyQt6.QtWidgets.QMessageBox.warning",
        lambda *args, **kwargs: warnings.append(args[2]))
    tab._start_batch()
    assert len(warnings) == 1
    assert "batch/prepare_item.wf" in warnings[0]
    assert "batch_unit_prepare" in warnings[0]

    # 换成会回传 username 的准备 wf 就不再拦。
    config.configs["group"].workflows = BatchWorkflows(
        prepare_item="batch/prepare_item_by_attr.wf")
    with pytest.raises(AssertionError, match="不该走到启动批量"):
        tab._start_batch()


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


def test_skipped_prepare_consumes_round_and_uses_normal_loop(
    tmp_path, monkeypatch, qapp,
):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    save_user_metadata(User("u2", attributes={"account": "b"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["a", "b"], [BatchScript("task", "task")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account", rounds=2,
            workflows=BatchWorkflows(prepare_item="prepare.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1", "u2"],
    )
    prepares = []
    executed = []

    def stage(phase, _wf, _index, _username, _state, *args, **kwargs):
        if phase == "prepare_item":
            selected = kwargs["unit_members"][0]
            prepares.append((selected, kwargs["round_number"]))
            if selected == "u1" and kwargs["round_number"] == 1:
                return BatchStageResult(status="skipped", state={})
            return BatchStageResult(username=selected, state={})
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(worker, "_run_script", lambda _s, _session, username,
                        **_kw: executed.append(username) or {})
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    worker.run()

    assert prepares == [("u1", 1), ("u2", 1), ("u1", 2), ("u2", 2)]
    assert executed == ["u2", "u1", "u2"]


@pytest.mark.parametrize("phase", ["prepare_item", "finish_item"])
def test_attribute_lifecycle_stop_ends_whole_batch(
    tmp_path, monkeypatch, qapp, phase,
):
    import lvjiang.core.daily_history as history

    for name, account in (("u1", "a"), ("u2", "b")):
        save_user_metadata(User(name, attributes={"account": account}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **_kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **_kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["a", "b"], [BatchScript("task", "task")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account", rounds=2,
            workflows=BatchWorkflows(prepare_item="prepare.wf", finish_item="finish.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1", "u2"],
    )
    prepared = []
    executed = []
    finished = []
    worker.finished_all.connect(finished.append)

    def stage(current, _wf, _index, _username, _state, *args, **kwargs):
        if current == "prepare_item":
            prepared.append(kwargs["unit_members"][0])
            if phase == current:
                return BatchStageResult(status="stopped", state={})
            return BatchStageResult(username="u1", state={})
        if current == phase:
            return BatchStageResult(status="stopped", state={})
        return BatchStageResult(state={})

    monkeypatch.setattr(worker, "_run_stage", stage)
    monkeypatch.setattr(worker, "_run_script", lambda *_args, **_kw:
                        executed.append("task") or {})
    monkeypatch.setattr(worker, "_save_result", lambda *_args: None)
    worker.run()

    assert prepared == ["u1"]
    assert executed == ([] if phase == "prepare_item" else ["task"])
    assert finished[0]["stopped"] is True
    assert len(finished[0]["entries"]) == 1


@pytest.mark.parametrize("setup_status", ["failed", "stopped"])
def test_attribute_setup_failure_marks_all_planned_rows(
    tmp_path, monkeypatch, qapp, setup_status,
):
    import lvjiang.core.daily_history as history

    for name, account in (("u1", "a"), ("u2", "b")):
        save_user_metadata(User(name, attributes={"account": account}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **_kw: None)
    reports = []
    monkeypatch.setattr(BatchReport, "write", lambda self: reports.append(self) or None)
    worker = BatchWorker(
        ["a", "b"], [BatchScript("task", "task")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account", rounds=2,
            workflows=BatchWorkflows(prepare_item="prepare.wf", batch_setup="setup.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1", "u2"],
    )
    monkeypatch.setattr(worker, "_run_stage", lambda *_args, **_kw:
                        BatchStageResult(status=setup_status, state={}))
    monkeypatch.setattr(worker, "_run_script", lambda *_args, **_kw:
                        pytest.fail("batch_setup 失败后仍运行了业务任务"))
    progress = []
    finished = []
    worker.progress.connect(lambda _idx, _label, _script, status:
                            progress.append(status))
    worker.finished_all.connect(finished.append)
    worker.run()

    assert progress == [ST_SKIPPED] * 4
    assert len(finished[0]["entries"]) == 4
    assert finished[0]["stopped"] is (setup_status == "stopped")
    assert len(reports[0]._entries) == 4
    assert all(record.prepare_status == ST_SKIPPED
               and [script.status for script in record.scripts] == [ST_SKIPPED]
               for record in reports[0]._entries)


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


def test_attribute_rechecks_each_script_after_previous_script(tmp_path, monkeypatch, qapp):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(history, "try_create_task_run", lambda **kw: None)
    monkeypatch.setattr(BatchReport, "write", lambda self: None)
    worker = BatchWorker(
        ["a"], [BatchScript("A", "A"), BatchScript("B", "B")],
        BatchConfigItem(name="group", execution_unit_key="account",
                        workflows=BatchWorkflows(prepare_item="prepare.wf")),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    changed = False
    checks = []
    executed = []

    def check(script, username, **_kwargs):
        checks.append((username, script.id))
        return BatchCheckResult(
            status="skipped" if script.id == "B" and changed else "success")

    def run(script, *_args, **_kwargs):
        nonlocal changed
        executed.append(script.id)
        changed = True
        return {}

    monkeypatch.setattr(worker, "_check_script", check)
    monkeypatch.setattr(worker, "_run_stage", lambda phase, *_args, **_kw:
                        BatchStageResult(username="u1" if phase == "prepare_item" else "",
                                         state={}))
    monkeypatch.setattr(worker, "_run_script", run)
    monkeypatch.setattr(worker, "_save_result", lambda *args: None)
    finished = []
    worker.finished_all.connect(finished.append)
    worker.run()

    assert checks == [("u1", "A"), ("u1", "B"), ("u1", "A"), ("u1", "B")]
    assert executed == ["A"]
    assert next(iter(finished[0]["entries"].values()))["scripts"] == {
        "A": ST_SUCCESS, "B": ST_SKIPPED,
    }


def test_attribute_session_failure_keeps_successful_prepare(tmp_path, monkeypatch, qapp):
    import lvjiang.core.daily_history as history

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)

    class BatchRun:
        batch_run_id = "batch"
        repository = None

        def finish(self, **kwargs):
            statuses.append(kwargs["status"])

    statuses = []
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: BatchRun())
    reports = []
    monkeypatch.setattr(BatchReport, "write", lambda self: reports.append(self) or None)
    worker = BatchWorker(
        ["a"], [BatchScript("A", "A")],
        BatchConfigItem(name="group", execution_unit_key="account",
                        workflows=BatchWorkflows(prepare_item="prepare.wf")),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    monkeypatch.setattr(worker, "_run_stage", lambda phase, *_args, **_kw:
                        BatchStageResult(username="u1" if phase == "prepare_item" else "",
                                         state={}))
    monkeypatch.setattr(worker._session_manager, "load", lambda _name:
                        (_ for _ in ()).throw(OSError("session unavailable")))
    finished = []
    worker.finished_all.connect(finished.append)
    worker.run()

    entry = next(iter(finished[0]["entries"].values()))
    assert entry["prepare"] == ST_SUCCESS
    assert entry["error"] == "session unavailable"
    assert reports[0]._entries[0].prepare_status == ST_SUCCESS
    assert reports[0]._entries[0].error == "session unavailable"
    assert statuses == ["failed"]


def test_attribute_lock_conflict_limit_reports_skipped_unit(tmp_path, monkeypatch, qapp):
    import itertools

    import lvjiang.core.access as access
    import lvjiang.core.daily_history as history
    import lvjiang.ui.batch.batch_runner as runner

    save_user_metadata(User("u1", attributes={"account": "a"}), tmp_path)
    monkeypatch.setattr(history, "try_create_batch_run", lambda **kw: None)
    monkeypatch.setattr(
        access, "acquire_user",
        lambda *_args: (_ for _ in ()).throw(access.AccessDeniedError("busy")),
    )
    ticks = itertools.count()
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(ticks) * 61.0)
    reports = []
    monkeypatch.setattr(BatchReport, "write", lambda self: reports.append(self) or None)
    worker = BatchWorker(
        ["a"], [BatchScript("A", "A")],
        BatchConfigItem(name="group", execution_unit_key="account",
                        workflows=BatchWorkflows(prepare_item="prepare.wf")),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=["u1"],
    )
    monkeypatch.setattr(worker, "_run_stage", lambda *_args, **_kw:
                        BatchStageResult(state={}))
    logs = []
    progress = []
    worker.log.connect(logs.append)
    worker.progress.connect(lambda _idx, _label, _script, status:
                            progress.append(status))
    worker.run()

    assert len(reports[0]._entries) == 30
    assert "计划执行：1 单元轮次" in reports[0].render()
    assert "实际尝试：30 次" in reports[0].render()
    assert "跳过：30" in reports[0].render()
    assert progress == [ST_SKIPPED]
    assert any("连续 30 次无法取得用户锁" in message for message in logs)


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


@pytest.mark.parametrize(
    ("prepared", "expect"),
    [
        # wf 跑到末尾一次 return 都没走：engine.return_value 保持 None，
        # _normalize_stage_result 的默认状态恰好是 success + 空 username，
        # 于是真正的原因（本地覆盖成了没有 return 的旧版本）完全看不出来。
        (BatchStageResult(state={}, returned=False, source="/local/prepare.wf"),
         ["没有返回任何值", "顶层 return", "config/local", "/local/prepare.wf"]),
        # 返回了，但选的用户不在本单元可执行成员里
        (BatchStageResult(state={}, username="外人", source="/system/prepare.wf"),
         ["未返回本单元可执行的用户名", "'外人'", "/system/prepare.wf"]),
    ],
)
def test_unit_prepare_protocol_error_separates_the_two_causes(
    tmp_path, qapp, prepared, expect,
):
    """两种违反协议的排查方向完全不同，报文必须分开，并带上实际加载路径。"""
    worker = BatchWorker(
        ["a"], [BatchScript("task", "task")],
        BatchConfigItem(
            name="attribute", execution_unit_key="account",
            workflows=BatchWorkflows(prepare_item="batch/prepare_item_by_attr.wf"),
        ),
        BatchContext(None, None, None, None), SessionManager(tmp_path),
        lambda: False, candidate_usernames=[],
    )
    message = worker._unit_prepare_protocol_error(prepared, ["u1", "u2"])
    for fragment in expect:
        assert fragment in message
    assert "batch/prepare_item_by_attr.wf" in message


def test_stage_result_marks_whether_the_workflow_returned():
    """没有返回值与显式返回 null 都算「没回传」，供上层区分报文。"""
    assert BatchWorker._normalize_stage_result(None, {}).returned is False
    assert BatchWorker._normalize_stage_result(
        {"status": "success"}, {}).returned is True
