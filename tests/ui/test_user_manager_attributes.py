from PyQt6.QtWidgets import QTableWidgetItem

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.user_config import UserConfigManager
from lvjiang.ui.user_manager_dialog import UserManagerDialog


def test_user_manager_is_larger_and_edits_arbitrary_key_values(
    tmp_path, monkeypatch, qtbot,
):
    from lvjiang import constants

    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "users")
    reset_session_store()
    manager = UserConfigManager()
    username = manager.get_active_user_name()
    manager.update_user_attributes(username, {"custom_key": "custom value"})

    dialog = UserManagerDialog(manager)
    qtbot.addWidget(dialog)
    assert dialog.minimumWidth() == 864
    assert dialog.minimumHeight() == 576
    assert dialog._attribute_table.horizontalHeaderItem(0).text() == "Key"
    assert dialog._attribute_table.horizontalHeaderItem(1).text() == "Value"
    assert dialog._attribute_table.rowCount() == 1
    assert dialog._attribute_table.item(0, 0).text() == "custom_key"

    dialog._attribute_table.setItem(0, 0, QTableWidgetItem("renamed_key"))
    dialog._attribute_table.setItem(0, 1, QTableWidgetItem("新值"))
    dialog._add_attribute()
    dialog._attribute_table.setItem(1, 0, QTableWidgetItem("another"))
    dialog._attribute_table.setItem(1, 1, QTableWidgetItem("2"))
    dialog._save_attributes()

    assert manager.get_user(username).attributes == {
        "renamed_key": "新值",
        "another": "2",
    }
    reset_session_store()
