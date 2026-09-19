from PyQt6.QtCore import QObject, pyqtSignal

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.ui.batch.batch_tab import BatchTab


class _Hotkeys:
    start = "F9"
    pause = "F10"


class _UserConfig:
    hotkeys = _Hotkeys()


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)
    _user_config = _UserConfig()
    is_running = False

    @staticmethod
    def _selected_run_env():
        return None


def test_main_batch_lists_preserve_and_update_actual_execution_order(
    monkeypatch, qtbot,
):
    config = BatchConfig(configs={
        "日常": BatchConfigItem(
            name="日常",
            task_ids=["a", "b", "c"],
            usernames=["用户A", "用户B", "用户C"],
            selected_task_ids=["b", "a"],
            selected_usernames=["用户B", "用户A"],
        ),
    }, active_config="日常")
    scripts = [
        {"id": task_id, "name": task_id, "batchable": True}
        for task_id in ("a", "b", "c")
    ]
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", lambda _cfg: None)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts",
        lambda _run_env: scripts,
    )

    tab = BatchTab(_Host())
    qtbot.addWidget(tab)

    assert tab._checked_script_ids() == ["b", "a"]
    assert tab._get_enabled_usernames() == ["用户B", "用户A"]

    script_a = tab._script_list.takeTopLevelItem(1)
    tab._script_list.insertTopLevelItem(0, script_a)
    tab._on_script_rows_moved()
    user_a = tab._user_list.takeTopLevelItem(1)
    tab._user_list.insertTopLevelItem(0, user_a)
    tab._on_user_rows_moved()

    group = config.configs["日常"]
    assert group.task_ids == ["a", "b", "c"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert group.selected_task_ids == ["a", "b"]
    assert group.selected_usernames == ["用户A", "用户B"]
    assert tab._script_list.topLevelItem(0).text(1) == "1"
    assert tab._user_list.topLevelItem(0).text(1) == "1"
