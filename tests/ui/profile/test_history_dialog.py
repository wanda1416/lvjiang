"""Profile 变更记录的可编辑来源与撤销入口。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QPushButton, QTableWidget

from lvjiang.ui.profile import dialogs


def _record() -> dict:
    return {
        "id": 17,
        "ts": "2026-10-06T10:00:00",
        "username": "测试用户",
        "type": "quota",
        "key": "weekly_item",
        "old_value": 1.0,
        "new_value": 3.0,
        "old_value_text": "",
        "new_value_text": "",
        "change_type": "action",
        "delta_value": 2.0,
        "source": "误填来源",
        "sync_from": None,
    }


def test_history_source_double_click_edit_and_undo_confirmation(qapp, monkeypatch):
    records = [_record()]
    source_updates = []
    undo_calls = []
    monkeypatch.setattr(dialogs, "db_count_history", lambda *args, **kwargs: 1)
    monkeypatch.setattr(
        dialogs, "db_get_history", lambda *args, **kwargs: records,
    )
    monkeypatch.setattr(
        dialogs,
        "db_update_history_source",
        lambda history_id, **kwargs: source_updates.append(
            (history_id, kwargs["expected_source"], kwargs["new_source"]),
        ) or True,
    )
    monkeypatch.setattr(
        dialogs, "profile_history_undo_unavailable_reason",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        dialogs, "undo_profile_history", lambda history_id: undo_calls.append(history_id),
    )
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    dialog = dialogs.HistoryDialog(
        None, "quota", "weekly_item", "每周项目",
    )
    table = dialog.findChild(QTableWidget)
    assert table is not None
    assert table.columnCount() == 9
    assert table.horizontalHeaderItem(8).text() == ""
    assert not table.item(0, 4).flags() & Qt.ItemFlag.ItemIsEditable
    assert table.item(0, 5).flags() & Qt.ItemFlag.ItemIsEditable

    table.item(0, 5).setText("正确来源")
    assert source_updates == [(17, "误填来源", "正确来源")]

    undo = table.cellWidget(0, 8)
    assert isinstance(undo, QPushButton)
    assert undo.text() == "撤销"
    undo.click()
    assert undo_calls == [17]
    assert dialog.values_changed
    dialog.close()
