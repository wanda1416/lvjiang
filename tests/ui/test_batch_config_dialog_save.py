from PyQt6.QtCore import Qt

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog


class _Users:
    @staticmethod
    def list_users() -> list[str]:
        return ["用户A"]


def test_save_preserves_main_page_selection_order(monkeypatch, qtbot):
    stale = BatchConfig(configs={
        "日常": BatchConfigItem(
            name="日常", task_ids=["task"], usernames=["用户A"],
            selected_task_ids=["task"], selected_usernames=["用户A"],
        )
    }, active_config="日常")
    latest = BatchConfig(configs={
        "日常": BatchConfigItem(
            name="日常", task_ids=["task"], usernames=["用户A"],
            selected_task_ids=[], selected_usernames=[],
            rounds=4,
            workflow_params={"prepare_item": {"wait": 30}},
        )
    }, active_config="日常")
    saved = []
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config", lambda: stale)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config", saved.append)

    dialog = BatchConfigDialog(_Users())
    qtbot.addWidget(dialog)
    accepted = []
    saved_signals = []
    monkeypatch.setattr(dialog, "accept", lambda: accepted.append(True))
    dialog.saved.connect(lambda: saved_signals.append(True))
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config", lambda: latest)
    dialog._on_save()

    group = saved[0].configs["日常"]
    assert group.selected_task_ids == []
    assert group.selected_usernames == []
    assert group.rounds == 4
    assert group.workflow_params == {"prepare_item": {"wait": 30}}
    assert accepted == []
    assert saved_signals == [True]
    assert dialog._cfg is saved[0]


def test_rename_preserves_group_data_and_position(monkeypatch, qtbot):
    config = BatchConfig(configs={
        "第一组": BatchConfigItem(name="第一组"),
        "第二组": BatchConfigItem(
            name="第二组", task_ids=["task"], selected_task_ids=["task"],
        ),
    }, active_config="第二组")
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config", lambda: config)
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.QInputDialog.getText",
        lambda *args, **kwargs: ("新名称", True),
    )
    dialog = BatchConfigDialog(_Users())
    qtbot.addWidget(dialog)

    dialog._on_rename_config()

    assert list(dialog._cfg.configs) == ["第一组", "新名称"]
    renamed = dialog._cfg.configs["新名称"]
    assert renamed.name == "新名称"
    assert renamed.selected_task_ids == ["task"]
    assert dialog._cfg.active_config == "新名称"


def test_visibility_lists_support_select_all_and_none(monkeypatch, qtbot):
    config = BatchConfig(configs={
        "组": BatchConfigItem(name="组", usernames=["用户A"]),
    }, active_config="组")
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config", lambda: config)
    dialog = BatchConfigDialog(_Users())
    qtbot.addWidget(dialog)

    dialog._set_all_checked(dialog._user_list, False)
    assert all(
        dialog._user_list.item(i).checkState() == Qt.CheckState.Unchecked
        for i in range(dialog._user_list.count())
    )
    dialog._set_all_checked(dialog._user_list, True)
    assert all(
        dialog._user_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(dialog._user_list.count())
    )
