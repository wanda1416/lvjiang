"""既有 key 不能随普通字段编辑误改；需点击独立编辑按钮。"""


from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from lvjiang.core.profile import schema
from lvjiang.core.profile.models import NoteKeyDef, StockKeyDef, SyncTargetDef
from lvjiang.ui.profile.settings_dialog import ProfileDefinitionDialog


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
