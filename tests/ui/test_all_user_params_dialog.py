from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QListWidget,
    QPushButton,
    QStackedWidget,
)

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.config.wf_configs import set_wf_config
from lvjiang.core.user_config import UserConfigManager, set_user_workflow_params
from lvjiang.ui.all_user_params_dialog import AllUserParamsDialog


def test_dialog_shows_effective_source_and_switches_tasks(
    qtbot, tmp_path, monkeypatch,
):
    import lvjiang.constants as constants

    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "users")
    reset_session_store()
    manager = UserConfigManager()
    assert manager.create_user("bob")
    configs = [
        {
            "id": "task_a", "name": "任务 A", "scope": "daily",
            "parameters": [
                {"name": "count", "label": "次数", "type": "number", "default": 1},
            ],
        },
        {
            "id": "task_b", "name": "任务 B", "scope": "daily",
            "parameters": [
                {"name": "code", "label": "代码", "type": "text", "default": "D"},
            ],
        },
        {"id": "dedicated", "name": "专用", "scope": "dedicated"},
    ]
    set_wf_config("task_a", {"count": "3"})
    set_user_workflow_params("bob", "task_a", {"count": "9"}, manager.users_dir)

    dialog = AllUserParamsDialog(
        configs, manager.list_users(), manager.users_dir, "task_a")
    qtbot.addWidget(dialog)

    combo = dialog.findChild(QComboBox, "all_user_params_task")
    previous = dialog.findChild(QPushButton, "previous_task")
    next_ = dialog.findChild(QPushButton, "next_task")
    tabs = dialog.findChild(QListWidget, "all_user_params_users")
    pages = dialog.findChild(QStackedWidget)
    assert combo.count() == 2
    assert combo.currentData() == "task_a"
    assert not previous.isEnabled()
    assert next_.isEnabled()
    assert tabs.property("navigation") is True
    assert [tabs.item(i).text() for i in range(tabs.count())] == ["default", "bob *"]
    assert tabs.item(1).font().bold()

    default_page = pages.widget(0)
    bob_page = pages.widget(1)
    assert default_page.findChild(QLabel, "parameter_source").property("source") == "global"
    assert default_page.findChild(QLabel, "parameter_value_count").text() == "3"
    assert bob_page.findChild(QLabel, "parameter_source").property("source") == "user"
    assert bob_page.findChild(QLabel, "parameter_value_count").text() == "9"

    next_.click()
    assert combo.currentData() == "task_b"
    assert previous.isEnabled()
    assert not next_.isEnabled()
    assert [tabs.item(i).text() for i in range(tabs.count())] == ["default", "bob"]
    assert pages.widget(0).findChild(QLabel, "parameter_source").property("source") == "global"
    assert pages.widget(0).findChild(QLabel, "parameter_value_code").text() == "D"
    reset_session_store()
