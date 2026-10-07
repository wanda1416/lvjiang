"""批量配置换布局不能重置隐藏参数、草稿或主页面活动组。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.config import get_interface_store
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.batch import batch_config_dialog as module


@pytest.fixture
def dialog(qtbot, monkeypatch):
    first = BatchConfigItem(id='first', name='甲组', usernames=['tester'],
                            workflows=BatchWorkflows(prepare_item='prepare.wf', recover_unattended='recover.wf'),
                            workflow_params={'prepare_item': {'skip': False, 'wait': 600, 'note': '多行内容'}})
    second = BatchConfigItem(id='second', name='乙组', usernames=['tester'])
    config = BatchConfig(configs={'first': first, 'second': second})
    monkeypatch.setattr(module, 'load_batch_config', lambda: deepcopy(config))
    saved = []
    monkeypatch.setattr(module, 'save_batch_config', lambda value: saved.append(deepcopy(value)))
    monkeypatch.setattr('lvjiang.workflows.discovery.list_exposed_scripts', lambda: [])
    monkeypatch.setattr('lvjiang.core.profile.schema.get_profile_config', lambda: ProfileSchema())
    definitions = {
        'prepare_item': [
            {'name': 'count', 'label': '滚动次数', 'type': 'number', 'default': 50},
            {'name': 'skip', 'label': '在线跳过', 'type': 'bool', 'default': True},
            {'name': 'wait', 'label': '等待时间', 'type': 'number', 'max': 3600, 'require': 'not $skip'},
            {'name': 'restart', 'label': '允许重启', 'type': 'bool', 'default': False},
            {'name': 'mode', 'label': '模式', 'type': 'select', 'options': ['a', 'b'], 'default': 'b'},
            {'name': 'note', 'label': '备注', 'type': 'text', 'multiline': True},
            {'name': 'options', 'label': '选项', 'type': 'checkgroup', 'options': ['one', 'two']},
        ],
        'recover_unattended': [{'name': 'count', 'label': '恢复次数', 'type': 'number', 'default': 20}],
    }
    monkeypatch.setattr(module, 'lifecycle_parameter_definitions', lambda _: definitions)
    users = SimpleNamespace(list_users=lambda: ['tester'],
                            get_user=lambda _: SimpleNamespace(attributes={'team': 'demo'}))
    get_interface_store().update_node('ui_state', {'batch': {'active_group_id': 'first'}})
    widget = module.BatchConfigDialog(users)
    qtbot.addWidget(widget)
    widget.resize(1000, 700)
    widget.show()
    return widget, saved


def test_require_and_resize_keep_hidden_values_until_explicit_save(dialog, qtbot):
    widget, saved = dialog
    before = widget._collect_workflow_params()
    assert before['prepare_item']['wait'] == 600
    wait = widget._workflow_param_widgets[('prepare_item', 'wait')]
    skip = widget._workflow_param_widgets[('prepare_item', 'skip')]
    assert not wait.isHidden()
    skip.setChecked(True)
    assert wait.isHidden()
    widget.resize(860, 560)
    qtbot.wait(1)
    values = widget._collect_workflow_params()
    assert values['prepare_item'] == {**before['prepare_item'], 'skip': True}
    assert values['recover_unattended'] == before['recover_unattended']
    assert saved == []
    widget._on_save()
    assert saved[-1].configs['first'].workflow_params == values
    skip.setChecked(False)
    assert not wait.isHidden() and wait.value() == 600


def test_unit_and_editor_switch_do_not_change_skip_preference_or_active_group(dialog):
    widget, saved = dialog
    assert widget._skip_single_lifecycle.isChecked()
    widget._unit_combo.setCurrentIndex(widget._unit_combo.findData('team'))
    assert not widget._skip_single_lifecycle.isEnabled()
    assert not widget._skip_single_lifecycle.isChecked()
    assert widget._profile_sort_widget.isHidden()
    widget._on_save()
    assert saved[-1].configs['first'].skip_lifecycle_for_single_item
    widget._unit_combo.setCurrentIndex(widget._unit_combo.findData('user'))
    assert widget._skip_single_lifecycle.isEnabled() and widget._skip_single_lifecycle.isChecked()
    assert not widget._profile_sort_widget.isHidden()
    widget._config_combo.setCurrentIndex(widget._config_combo.findData('second'))
    assert get_interface_store().get_node('ui_state')['batch']['active_group_id'] == 'first'
