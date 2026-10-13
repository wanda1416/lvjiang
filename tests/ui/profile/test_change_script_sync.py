"""同步选项随定义保存；关联同步脚本不能在 UI 线程执行。"""
import threading

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QCheckBox, QDialog, QPushButton, QWidget

from lvjiang.core.profile import schema
from lvjiang.core.profile.models import (
    NoteKeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StockKeyDef,
)
from lvjiang.ui.profile.cell_editing import ProfileCellEditingMixin, _write_profile
from lvjiang.ui.profile.settings_dialog import (
    ProfileDefinitionDialog,
    _ChangeScriptFileField,
)


def test_existing_script_sync_option_roundtrips_and_clear_resets(qtbot, monkeypatch):
    # 四个模型都必须保留共有字段，否则某个专属 from_dict 会悄悄丢失执行方式。
    for definition in (StockKeyDef(key='value'), NoteKeyDef(key='text'),
                       QuotaKeyDef(key='period'), RegenKeyDef(key='energy')):
        definition.change_script = 'profile/example.wf'
        definition.change_script_sync = True
        restored = type(definition).from_dict(definition.to_dict())
        assert restored.change_script_sync and restored.change_script == definition.change_script
    definition = NoteKeyDef(key='text', label='示例文本', change_script='profile/example.wf')
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={'note': [definition]}))
    schema.reload_profile_config()
    parent = QWidget()
    qtbot.addWidget(parent)
    def edit(dialog):
        checkbox = next(c for c in dialog.findChildren(QCheckBox) if c.text() == '同步修改')
        assert not checkbox.isChecked()
        checkbox.setChecked(True)
        next(b for b in dialog.findChildren(QPushButton) if b.text() == '保存').click()
        return dialog.result()
    monkeypatch.setattr(QDialog, 'exec', edit)
    ProfileDefinitionDialog.open_key_editor(parent, 'note', definition, {'text'})
    assert schema.get_profile_config().get_key('text').change_script_sync
    field = _ChangeScriptFileField('profile/example.wf', synchronous=True)
    qtbot.addWidget(field)
    field._clear_button.click()
    assert not field.text() and not field.synchronous()
    assert not field._sync_checkbox.isEnabled()


def test_sync_profile_write_runs_off_ui_and_completion_on_ui(qtbot, monkeypatch):
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={
        'stock': [StockKeyDef(key='source', change_script='profile/example.wf', change_script_sync=True)]}))
    schema.reload_profile_config()
    owner = QWidget()
    qtbot.addWidget(owner)
    ui_thread = QThread.currentThread()
    started, release = threading.Event(), threading.Event()
    results = []
    def operation():
        assert QThread.currentThread() != ui_thread
        started.set()
        assert release.wait(3)
    def complete(error):
        assert QThread.currentThread() == ui_thread
        results.append(error)
    _write_profile(owner, operation, complete)
    try:
        qtbot.waitUntil(started.is_set)
        assert not results  # UI 能继续处理事件，尚未完成同步链。
    finally:
        release.set()
        qtbot.waitUntil(lambda: bool(results))
    assert results == [None]


def test_queued_adjustments_keep_increment_and_absolute_override_semantics(qtbot, monkeypatch):
    from lvjiang.core.profile import triggers
    from lvjiang.core.profile.service import profile_read

    definition = StockKeyDef(key='source', change_script='profile/example.wf', change_script_sync=True)
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={'stock': [definition]}))
    schema.reload_profile_config()
    started, release = threading.Event(), threading.Event()
    runner = triggers.ProfileScriptRunner(schema._PROFILE_PATH.parent / 'users')
    def run(_event):
        started.set()
        assert release.wait(3)
    monkeypatch.setattr(runner, '_run_event', run)
    monkeypatch.setattr(triggers, '_runner', runner)
    class Host(ProfileCellEditingMixin, QWidget):
        def _get_current_group_name(self):
            return 'example'
        def _refresh_group(self, _group, _table):
            self.finished += 1
    host = Host()
    host.finished = 0
    host._tables = {'example': object()}
    qtbot.addWidget(host)
    try:
        host._adjust_value('tester', 'stock', 'source', definition, 0, 1)
        qtbot.waitUntil(started.is_set)
        host._adjust_value('tester', 'stock', 'source', definition, 0, 1)
    finally:
        release.set()
    qtbot.waitUntil(lambda: host.finished == 2)
    assert profile_read('tester', 'source') == 2  # 第二次不能用排队前的 0 覆盖第一次。
    host._adjust_value('tester', 'stock', 'source', definition, 0, 5,
                       is_action=True, set_value=5)
    qtbot.waitUntil(lambda: host.finished == 3)
    assert profile_read('tester', 'source') == 5  # 勾选依赖同步的覆写仍是绝对目标，不是加 5。
    runner.request_stop()
