"""批量配置只编辑定义层：工作流文件、调度单元与可见范围。

生命周期参数属于运行草稿，在主页面的「参数」页编辑；这个窗口不应该再出现
第二套参数控件，也不应该把参数写回 batch.json。
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.config import get_interface_store
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.batch import batch_config_dialog as module
from lvjiang.ui.theme import get_theme_manager


@pytest.fixture
def dialog(qtbot, monkeypatch):
    first = BatchConfigItem(id='first', name='甲组', usernames=['tester'],
                            workflows=BatchWorkflows(
                                prepare_item='prepare.wf',
                                recover_unattended='recover.wf'))
    second = BatchConfigItem(id='second', name='乙组', usernames=['tester'])
    config = BatchConfig(configs={'first': first, 'second': second})
    monkeypatch.setattr(module, 'load_batch_config', lambda: deepcopy(config))
    saved = []
    monkeypatch.setattr(module, 'save_batch_config', lambda value: saved.append(deepcopy(value)))
    monkeypatch.setattr('lvjiang.workflows.discovery.list_exposed_scripts', lambda: [])
    monkeypatch.setattr('lvjiang.core.profile.schema.get_profile_config', lambda: ProfileSchema())
    users = SimpleNamespace(list_users=lambda: ['tester'],
                            get_user=lambda _: SimpleNamespace(attributes={'team': 'demo'}))
    get_interface_store().update_node('batch', {'active_group_id': 'first'})
    widget = module.BatchConfigDialog(users)
    qtbot.addWidget(widget)
    widget.resize(1000, 700)
    widget.show()
    return widget, saved


def test_dialog_only_edits_workflows_and_saves_no_parameters(dialog):
    """参数已搬到主页面；保存配置组时不能重新写出 workflow_params。"""
    widget, saved = dialog
    assert not hasattr(widget, '_workflow_param_widgets')
    widget._selectors['prepare_item'].setCurrentText('batch/prepare_item.wf')
    widget._on_save()
    group = saved[-1].configs['first']
    assert group.workflows.prepare_item == 'batch/prepare_item.wf'
    assert 'workflow_params' not in group.to_dict()


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
    assert get_interface_store().get_node('batch')['active_group_id'] == 'first'


def test_task_row_marks_ids_whose_script_is_gone(dialog, monkeypatch):
    """配置组里留着已删除的任务 ID 时，任务行必须标出它已失效。

    失效行会回退用 ID 当显示名，而任务 ID 本身就是中文时，它和正常任务行
    看上去毫无区别：用户既看不出它已经失效，也就找不到清理它的理由。
    """
    widget, _ = dialog
    widget._cfg.configs['first'].task_ids = ['live_task', '已删除任务']
    monkeypatch.setattr(
        'lvjiang.workflows.discovery.list_exposed_scripts',
        lambda: [{'id': 'live_task', 'name': '存活任务', 'batchable': True}])
    widget._load_config('first')

    rows = {
        widget._task_list.topLevelItem(index).data(
            0, Qt.ItemDataRole.UserRole):
            widget._task_list.topLevelItem(index)
        for index in range(widget._task_list.topLevelItemCount())
    }
    missing = rows['已删除任务']
    assert missing.text(0) == '已删除任务（已失效）'
    assert missing.foreground(0).color().name() == QColor(
        get_theme_manager().tokens.text_muted).name()
    # 仍然可勾选：清理路径是用户自己取消勾选后保存，不能被禁用挡住。
    assert missing.flags() & Qt.ItemFlag.ItemIsUserCheckable
    assert rows['live_task'].text(0) == '存活任务'
