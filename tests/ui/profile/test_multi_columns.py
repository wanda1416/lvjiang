"""多选新增列保护筛选选择、原列定位与既有列宽，不写真实配置。"""
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QTableWidget, QWidget

from lvjiang.core.config import get_session_store
from lvjiang.core.profile import store
from lvjiang.core.profile.models import QuotaKeyDef, StockKeyDef
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.profile import column_management
from lvjiang.ui.profile.column_management import ProfileColumnMixin
from lvjiang.ui.profile.key_picker import create_profile_key_picker
from lvjiang.ui.profile.multi_key_dialog import ProfileKeyMultiSelectDialog


@pytest.fixture
def schema():
    return ProfileSchema(keys_by_model={
        'quota': [QuotaKeyDef(key='daily', label='每日')],
        'stock': [StockKeyDef(key='a', label='资源甲', group='资源'),
                  StockKeyDef(key='b', label='资源乙', group='资源'),
                  StockKeyDef(key='c', label='资源丙', group='资源'),
                  StockKeyDef(key='z', label='数量')],
    })


def leaves(dialog):
    return {item.data(0, Qt.ItemDataRole.UserRole): item for item in dialog._leaves}


def test_search_keeps_choices_and_group_selection_only_checks_matches(qtbot, schema):
    dialog = ProfileKeyMultiSelectDialog(schema, schema.get_all_keys())
    qtbot.addWidget(dialog)
    items = leaves(dialog)
    assert not dialog._add.isEnabled()
    items['c'].setCheckState(0, Qt.CheckState.Checked)
    dialog._search.setText('资源乙')
    items['b'].parent().setCheckState(0, Qt.CheckState.Checked)
    assert dialog.selected_keys() == ['b', 'c']
    assert dialog._add.text() == '添加（2）'
    dialog._search.setText('DAILY')
    dialog._select_visible.click()
    assert dialog.selected_keys() == ['daily', 'b', 'c']
    dialog._search.clear()
    assert items['b'].parent().checkState(0) == Qt.CheckState.PartiallyChecked
    dialog._clear.click()
    assert dialog.selected_keys() == []
    assert not dialog._add.isEnabled()
    # 原单选选择器仍一次返回一个 key，未变成勾选菜单。
    picked = []
    button = create_profile_key_picker(schema, schema.get_all_keys(), '', picked.append)
    qtbot.addWidget(button)
    menu = button.build_key_menu()
    action = menu.actions()[0].menu().actions()[0]
    assert not action.isCheckable()
    action.trigger()
    assert picked == ['daily']


@pytest.fixture
def overview(qtbot, monkeypatch, schema):
    store.insert_overview_columns('group', None, ['missing', 'a', 'z'])
    store.insert_overview_columns('other', None, ['b'])
    column_management._save_column_widths({'group': [70, 110, 130], 'other': [80, 90]})
    monkeypatch.setattr(column_management, 'get_profile_config', lambda: schema)

    class Overview(ProfileColumnMixin, QWidget):
        def __init__(self):
            super().__init__()
            self._tables = {'group': QTableWidget(0, 3, self)}
            self._visible_column_keys = {'group': ['a', 'z']}
            self.refreshes = []

        def _refresh_group(self, group, table):
            self.refreshes.append(group)

    widget = Overview()
    qtbot.addWidget(widget)
    return widget


def test_batch_add_merges_current_columns_preserves_widths_and_refreshes_once(overview, monkeypatch):
    def choose(dialog):
        items = leaves(dialog)
        assert 'a' not in items and 'z' not in items
        assert 'b' in items  # 其他总览分组使用过的字段仍可添加。
        items['c'].setCheckState(0, Qt.CheckState.Checked)
        items['b'].setCheckState(0, Qt.CheckState.Checked)
        items['daily'].setCheckState(0, Qt.CheckState.Checked)
        # 模拟窗口打开期间另一个编辑入口新增了一列及其列宽。
        store.insert_overview_column('group', 0, 'b')
        column_management._save_column_widths({'group': [70, 220, 110, 130], 'other': [80, 90]})
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ProfileKeyMultiSelectDialog, 'exec', choose)
    overview._add_column('group', 0)
    assert store.get_groups()['group']['columns'] == ['b', 'missing', 'a', 'daily', 'c', 'z']
    assert store.get_groups()['other']['columns'] == ['b']
    width = overview._tables['group'].horizontalHeader().defaultSectionSize()
    assert column_management._get_column_widths() == {
        'group': [70, 220, 110, width, width, 130], 'other': [80, 90]}
    assert overview.refreshes == ['group']


def test_cancel_does_not_save_or_refresh(overview, monkeypatch):
    before = get_session_store().path.read_bytes()
    monkeypatch.setattr(ProfileKeyMultiSelectDialog, 'exec', lambda _: QDialog.DialogCode.Rejected)
    overview._add_column('group', -1)
    assert get_session_store().path.read_bytes() == before
    assert overview.refreshes == []


def test_store_inserts_ordered_unique_batch_and_rejects_removed_anchor():
    assert store.insert_overview_columns('group', None, ['a', 'z']) == ['a', 'z']
    assert store.insert_overview_columns('group', 'a', ['b', 'c', 'b', 'z']) == ['b', 'c']
    assert store.get_groups()['group']['columns'] == ['a', 'b', 'c', 'z']
    before = get_session_store().path.read_bytes()
    with pytest.raises(ValueError, match='原列'):
        store.insert_overview_columns('group', 'removed', ['new'])
    assert get_session_store().path.read_bytes() == before
