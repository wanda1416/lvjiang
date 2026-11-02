"""Profile 变更记录的可编辑来源与撤销入口。"""

from PyQt6.QtWidgets import QMessageBox, QPushButton

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
        "change_type": "set",
        "delta_value": 2.0,
        "source": "误填来源",
        "sync_from": None,
    }


def test_history_delete_requires_confirmation_and_refreshes_without_value_change(qapp, monkeypatch):
    from lvjiang.core.profile import repository

    # 真实临时 DB 验证按钮只删除这一行，不写余额或删除审计。
    repository.db_upsert('tester', 'stock', 'item', 2, change_type='delta', delta_value=2, source='example')
    repository.db_upsert('tester', 'stock', 'item', 3, change_type='delta', delta_value=1, source='example')
    # 未定义 key 不影响历史删除，按钮不复用撤销的限制。
    dialog = dialogs.HistoryDialog('tester', 'stock', 'item', '示例')
    table = dialog._table
    first = repository.db_get_history('tester', 'stock', 'item')[0]['id']
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
    next(b for b in table.cellWidget(0, 8).findChildren(QPushButton) if b.text() == '删除').click()
    assert repository.db_count_history('tester', 'stock', 'item') == 2
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    next(b for b in table.cellWidget(0, 8).findChildren(QPushButton) if b.text() == '删除').click()
    assert not repository.db_get_history_record(first)
    assert repository.db_count_history('tester', 'stock', 'item') == 1
    assert repository.db_read_entry('tester', 'stock', 'item')['value'] == 3
    assert table.rowCount() == 1 and not dialog.values_changed
    dialog.close()
