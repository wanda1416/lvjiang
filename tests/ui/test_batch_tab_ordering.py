from PyQt6.QtCore import QObject, pyqtSignal

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.core.profile.models import MODEL_QUOTA, QuotaKeyDef
from lvjiang.core.profile.schema import ProfileSchema
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
    assert tab._get_enabled_usernames() == ["用户A", "用户B"]

    script_b = tab._script_list.takeTopLevelItem(0)
    tab._script_list.insertTopLevelItem(2, script_b)
    tab._on_script_rows_moved()
    user_a = tab._user_list.takeTopLevelItem(0)
    tab._user_list.insertTopLevelItem(2, user_a)
    tab._on_user_rows_moved()

    group = config.configs["日常"]
    assert group.task_ids == ["a", "b", "c"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert group.selected_task_ids == ["a", "b"]
    assert group.selected_usernames == ["用户B", "用户A"]
    assert tab._script_list.topLevelItem(0).text(1) == "1"
    assert tab._user_list.topLevelItem(0).text(1) == "1"

    tab._script_list.restore_order_requested.emit()
    tab._user_list.restore_order_requested.emit()

    assert [
        tab._script_id(tab._script_list.topLevelItem(index))
        for index in range(tab._script_list.topLevelItemCount())
    ] == ["a", "b", "c"]
    assert [
        tab._user_name(tab._user_list.topLevelItem(index))
        for index in range(tab._user_list.topLevelItemCount())
    ] == ["用户A", "用户B", "用户C"]
    assert tab._checked_script_ids() == ["a", "b"]
    assert tab._get_enabled_usernames() == ["用户A", "用户B"]

    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.random.shuffle",
        lambda values: values.reverse(),
    )
    tab._script_list.shuffle_order_requested.emit()
    tab._user_list.shuffle_order_requested.emit()

    assert tab._checked_script_ids() == ["b", "a"]
    assert tab._get_enabled_usernames() == ["用户B", "用户A"]
    assert group.task_ids == ["a", "b", "c"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert group.selected_usernames == ["用户B", "用户A"]


def test_user_profile_order_is_explicit_temporary_and_missing_quota_is_zero(
    monkeypatch, qtbot,
):
    group = BatchConfigItem(
        name="日常", usernames=["用户A", "用户B", "用户C"],
        selected_usernames=["用户A", "用户B", "用户C"],
    )
    config = BatchConfig({"日常": group}, "日常")
    schema = ProfileSchema(keys_by_model={
        MODEL_QUOTA: [QuotaKeyDef(key="weekly_work", label="周进度")],
    })
    values = {"用户A": 4, "用户B": None, "用户C": 2}
    saves = []
    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.save_batch_config", saves.append)
    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.get_profile_config", lambda: schema)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.profile_read",
        lambda username, _key: values[username],
    )
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", lambda _run_env: [],
    )
    tab = BatchTab(_Host())
    qtbot.addWidget(tab)
    assert not tab._script_list._profile_order
    assert tab._user_list._profile_order

    tab._profile_sort_key.setCurrentIndex(1)
    assert group.profile_sort_key == "weekly_work"
    assert tab._get_enabled_usernames() == ["用户A", "用户B", "用户C"]
    saves.clear()
    tab._user_list.profile_order_requested.emit()
    assert tab._get_enabled_usernames() == ["用户B", "用户C", "用户A"]
    assert group.selected_usernames == ["用户A", "用户B", "用户C"]
    assert group.usernames == ["用户A", "用户B", "用户C"]
    assert not saves

    tab._profile_sort_direction.setCurrentIndex(1)
    assert group.profile_sort_direction == "desc"
    saves.clear()
    tab._user_list.profile_order_requested.emit()
    assert tab._get_enabled_usernames() == ["用户A", "用户C", "用户B"]
    assert not saves
    tab._refresh_entry_list()
    assert tab._get_enabled_usernames() == ["用户A", "用户B", "用户C"]
