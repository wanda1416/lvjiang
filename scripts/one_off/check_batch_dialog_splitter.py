"""一次性验证：批量配置对话框改用纵向 splitter 后首帧布局正确。

场景一：未选中配置组，参数区隐藏，树和表单瓜分高度。
场景二：选中带生命周期参数的配置组，参数区首帧即出现且不被压没，
        不依赖用户缩放对话框。
"""
import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QSplitter

import lvjiang.ui.batch.batch_config_dialog as m
from lvjiang.core.batch_config import BatchConfig, BatchConfigItem, BatchWorkflows
from lvjiang.core.profile.schema import ProfileSchema

app = QApplication(sys.argv)
users = SimpleNamespace(list_users=lambda: ['tester'],
                        get_user=lambda _: SimpleNamespace(attributes={'team': 'demo'}))
first = BatchConfigItem(
    id='first', name='甲组', usernames=['tester'],
    workflows=BatchWorkflows(prepare_item='prepare.wf'),
    workflow_params={'prepare_item': {'count': 50}})
config = BatchConfig(configs={'first': first})
definitions = {
    'prepare_item': [
        {'name': 'count', 'label': '滚动次数', 'type': 'number', 'default': 50},
        {'name': 'wait', 'label': '等待时间', 'type': 'number', 'max': 3600},
        {'name': 'note', 'label': '备注', 'type': 'text', 'multiline': True},
    ],
}
with patch.object(m, 'load_batch_config', lambda: deepcopy(config)), \
     patch('lvjiang.workflows.discovery.list_exposed_scripts', lambda: []), \
     patch('lvjiang.core.profile.schema.get_profile_config', lambda: ProfileSchema()), \
     patch.object(m, 'lifecycle_parameter_definitions', lambda _: definitions), \
     patch('lvjiang.core.config.get_interface_store') as store:
    store.return_value.update_node = lambda *_: None
    store.return_value.get_node = lambda *_: {'active_group_id': 'first'}
    dlg = m.BatchConfigDialog(users)
    # 默认高度 = sizeHint * 1.2
    hint = dlg.sizeHint()
    print('sizeHint h:', hint.height(), 'actual h:', dlg.height())
    assert dlg.height() >= int(hint.height() * 1.2) - 2, dlg.height()
    dlg.resize(1000, 700)
    dlg.show()
    app.processEvents()
    main = next(s for s in dlg.findChildren(QSplitter) if s.orientation().name == 'Vertical')

    # 未选中任何配置组
    dlg._load_config('')
    app.processEvents()
    sizes = main.sizes()
    print('no selection:', sizes)
    assert dlg._workflow_params_scroll.isHidden() and sizes[2] == 0, sizes
    assert sizes[0] >= 180 and sizes[1] >= 140, sizes

    # 选中带参数的配置组：参数区首帧即出现
    dlg._load_config('first')
    app.processEvents()
    sizes = main.sizes()
    params_h = dlg._workflow_params_scroll.height()
    print('with params:', sizes, 'params scroll height:', params_h)
    assert not dlg._workflow_params_scroll.isHidden(), sizes
    assert params_h >= 120, params_h
    # 表单 pane 必须贴内容：高度 == sizeHint，底部无空白带
    print('form pane h:', sizes[1], 'form hint:', dlg._scope_form.sizeHint().height())
    assert sizes[1] <= dlg._scope_form.sizeHint().height() + 2, sizes
    # 表单行距必须紧凑：富余高度沉底，不摊进行间距
    grid = dlg._scope_form._grid
    row0 = grid.itemAtPosition(0, 0).geometry()
    row1 = grid.itemAtPosition(1, 0).geometry()
    gap = row1.top() - row0.bottom() - 1
    print('form row gap:', gap, 'grid spacing:', grid.verticalSpacing())
    assert gap <= grid.verticalSpacing() + 2, gap
    dlg.close()
print('OK')
