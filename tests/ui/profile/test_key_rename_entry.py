"""既有 key 不能随普通字段编辑误改；需点击独立编辑按钮。"""

from dataclasses import replace

from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QWidget,
)

from lvjiang.core import access
from lvjiang.core.profile import repository, schema
from lvjiang.core.profile.models import NoteKeyDef, StockKeyDef, SyncTargetDef
from lvjiang.core.profile.store import get_groups, insert_overview_column
from lvjiang.ui.profile.column_management import ProfileColumnMixin
from lvjiang.ui.profile.dialogs import HistoryDialog, KeyRenameHistoryDialog
from lvjiang.ui.profile.settings_dialog import ProfileDefinitionDialog


def test_key_edit_feedback_cancel_and_confirmed_save(qapp, monkeypatch):
    definition = StockKeyDef(key="credits", label="资源")
    accepting = False

    def edit(dialog):
        key_input = next(field for field in dialog.findChildren(QLineEdit) if field.text() == "credits")
        assert key_input.isEnabled()
        assert key_input.isReadOnly()
        key_input.selectAll()
        assert key_input.selectedText() == "credits"
        edit = next(button for button in dialog.findChildren(QPushButton) if button.text() == "编辑")
        assert edit.isEnabled()
        messages = []
        with monkeypatch.context() as patch:
            patch.setattr(access, "is_readonly", lambda: True)
            patch.setattr(QMessageBox, "information", lambda parent, title, text: messages.append(text))
            edit.click()
            assert messages == ["只读实例不可以重命名 key"]
            assert key_input.isEnabled()
            assert key_input.isReadOnly()
            assert edit.isEnabled()
        edit.click()
        assert key_input.isEnabled()
        assert not key_input.isReadOnly()
        assert key_input.selectedText() == "credits"
        assert not edit.isEnabled()
        key_input.setText("coins")
        if accepting:
            confirm = next(button for button in dialog.findChildren(QPushButton) if button.text() == "保存")
            confirm.click()
            return dialog.result()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", edit)
    parent = QWidget()
    assert ProfileDefinitionDialog.open_key_editor(parent, "stock", definition, {"credits"}) is None
    assert definition.key == "credits"
    # 必须覆盖定义窗口的实际保存入口，而非只调用后端重命名函数。
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={"stock": [definition]}))
    schema.reload_profile_config()
    db = repository.get_profile_db()
    db.upsert("tester", "stock", "credits", 12)
    manager = ProfileDefinitionDialog(parent)
    accepting = True
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    manager._edit_key("stock", 0)
    # 内层保存已完成重命名；外层取消不得撤销。
    assert schema.get_profile_config().get_key("coins") is not None
    assert db.get_entry("tester", "stock", "coins")["value"] == 12
    assert db.get_key_renames("stock", "coins")[0]["old_key"] == "credits"
    manager.reject()
    assert manager.has_saved_changes
    assert schema.reload_profile_config().get_key("coins") is not None
    manager.close()
    # 外层仅拥有待保存的分组，不得回滚后续单 key 保存的标签与上限。
    manager = ProfileDefinitionDialog(parent)
    manager._drafts["stock"][0] = replace(manager._drafts["stock"][0], group="pending")
    ProfileDefinitionDialog._save_key_definition(
        "stock", "coins", replace(schema.get_profile_config().get_key("coins"), label="新资源", cap=99))
    manager._on_save()
    saved = schema.get_profile_config().get_key("coins")
    assert (saved.group, saved.label, saved.cap) == ("pending", "新资源", 99)
    audit = KeyRenameHistoryDialog("stock", "coins", parent)
    table = audit.findChild(QTableWidget)
    assert table.rowCount() == 1 and table.item(0, 1).text() == "credits"
    history = HistoryDialog(None, "stock", "coins", "资源", parent)
    assert not any("重命名" in label.text() for label in history.findChildren(QLabel))
    manager.close()
    audit.close()
    history.close()
    parent.close()


def test_overview_column_uses_shared_editor_and_independent_rename_history(qapp, monkeypatch):
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={
        "stock": [StockKeyDef(key="coins", label="资源")]}))
    schema.reload_profile_config()
    db = repository.get_profile_db()
    db.upsert("tester", "stock", "credits", 12)
    db.rename_keys([("stock", "credits", "coins")], operation_id="setup", save_references=lambda: None)
    insert_overview_column("group", 0, "coins")
    viewed = []

    def edit(dialog):
        if isinstance(dialog, KeyRenameHistoryDialog):
            assert dialog.findChild(QTableWidget).rowCount() == 1
            viewed.append(True)
            return QDialog.DialogCode.Rejected
        key_input = next(field for field in dialog.findChildren(QLineEdit) if field.text() == "coins")
        assert key_input.isEnabled()
        assert key_input.isReadOnly()
        audit = next(button for button in dialog.findChildren(QPushButton) if button.text() == "查看 key 重命名记录")
        assert audit.isEnabled()
        audit.click()
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "编辑").click()
        key_input.setText("currency")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "保存").click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", edit)
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    parent = QWidget()
    parent._visible_columns = lambda name: ["coins"]
    refreshed = []
    parent.refresh = lambda: refreshed.append(True)
    ProfileColumnMixin._edit_column_definition(parent, "group", 0)
    assert viewed and refreshed
    assert db.get_entry("tester", "stock", "currency")["value"] == 12
    assert get_groups()["group"]["columns"] == ["currency"]
    assert len(db.get_key_renames("stock", "currency")) == 2
    parent.close()


def test_note_editor_hides_inert_sync_and_keeps_script_and_legacy_config(qapp, monkeypatch):
    definition = NoteKeyDef(
        key="status", label="备注", change_script="profile/example.wf",
        sync_targets=[SyncTargetDef(key="stock:credits")])
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={
        "note": [definition], "stock": [StockKeyDef(key="credits", label="资源")]}))
    schema.reload_profile_config()

    def edit(dialog):
        assert not any(label.text() == "同步目标:" for label in dialog.findChildren(QLabel))
        assert any(label.text() == "变更脚本:" for label in dialog.findChildren(QLabel))
        next(field for field in dialog.findChildren(QLineEdit) if field.text() == "备注").setText("新备注")
        next(button for button in dialog.findChildren(QPushButton) if button.text() == "保存").click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", edit)
    parent = QWidget()
    assert ProfileDefinitionDialog.open_key_editor(parent, "note", definition, {"status"}) is not None
    saved = schema.get_profile_config().get_key("status")
    assert saved.label == "新备注"
    assert saved.change_script == definition.change_script
    assert saved.sync_targets == definition.sync_targets
    parent.close()
