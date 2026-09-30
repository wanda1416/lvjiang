import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox

from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.core.scene_definition_models import SceneDef, ViewDef
from lvjiang.core.scene_transitions import Transition
from lvjiang.ui.button_styles import ACTION_BUTTON_STYLE

pytestmark = pytest.mark.usefixtures('qapp')


def test_caller_picker_roundtrip(monkeypatch):
    from lvjiang.ui.scene_editor import scene_select
    registry = SceneRegistry()
    registry._init_groups({'all': registry.all_scene_keys()}, None)
    monkeypatch.setattr(scene_select, 'get_registry', lambda: registry)
    monkeypatch.setattr(scene_select, 'get_scene_views', registry.get_scene_views)
    picker = scene_select.TransitionPicker('game_settings', '@caller')
    assert picker._group.itemText(1) == '调用方'
    assert picker.value() == '@caller'
    assert not picker._scene.isEnabled() and not picker._view.isEnabled()
    picker.set_value('/other')
    assert picker.value() == '/other'
    picker.set_value('@caller')
    assert picker.value() == '@caller'


def test_view_manager_saves_explicit_owner(monkeypatch):
    from lvjiang.ui.scene_editor import scene_view_dialog
    registry = SceneRegistry()
    registry._scenes['test'] = SceneDef('test', '测试', views=[
        ViewDef('base', '页面', kind='page'), ViewDef('tab', '标签', kind='tab', owner='/base')])
    registry._order.append('test')
    registry._init_groups({'main': ['test']}, {'main': '主场景'})
    saved = []
    monkeypatch.setattr(registry, 'save_scene_views', lambda key: saved.append(key))
    monkeypatch.setattr(scene_view_dialog, 'get_registry', lambda: registry)
    dialog = scene_view_dialog.ViewManagerDialog('test')
    dialog._list.setCurrentRow(1)
    assert dialog._relation.currentData() == 'tab'
    assert dialog._owner_group.currentData() == 'main'
    assert dialog._owner_scene.currentData() == 'test'
    assert dialog._owner_view.currentData() == 'base'
    assert dialog._owner_value() == '/base'
    dialog._relation.setCurrentIndex(dialog._relation.findData('viewport'))
    dialog._save_relation()
    assert registry.get_scene('test').views[1].relation == 'viewport'
    assert saved == ['test']


def test_view_manager_relation_controls_are_readable_and_styled(monkeypatch):
    from lvjiang.ui.scene_editor import scene_view_dialog

    registry = SceneRegistry()
    registry._scenes['test'] = SceneDef('test', '测试', views=[
        ViewDef('base', '页面', kind='page')])
    registry._order.append('test')
    registry._init_groups({'main': ['test']}, {'main': '主场景'})
    monkeypatch.setattr(scene_view_dialog, 'get_registry', lambda: registry)

    dialog = scene_view_dialog.ViewManagerDialog('test')
    combo = dialog._relation
    assert [combo.itemText(i) for i in range(combo.count())] == [
        '独立页面', '同页取景', '页内标签', '浮层窗口']

    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.rect = QRect(0, 0, combo.minimumWidth(), combo.sizeHint().height())
    content_rect = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    )
    widest_text = max(
        combo.fontMetrics().horizontalAdvance(combo.itemText(i))
        for i in range(combo.count())
    )
    assert content_rect.width() >= widest_text
    popup = combo.view()
    assert popup is not None
    assert popup.minimumWidth() >= combo.minimumWidth() + 12
    assert dialog._btn_save_relation.styleSheet() == ACTION_BUTTON_STYLE


def test_view_manager_owner_picker_filters_by_group_and_scene(monkeypatch):
    from lvjiang.ui.scene_editor import scene_view_dialog

    registry = SceneRegistry()
    registry._scenes['test'] = SceneDef('test', '测试', views=[
        ViewDef('base', '页面', kind='page'),
        ViewDef('tab', '标签', kind='tab', owner='other/second'),
    ])
    registry._scenes['other'] = SceneDef('other', '其他场景', views=[
        ViewDef('base', '基底', kind='page'),
        ViewDef('second', '第二页', kind='page'),
    ])
    registry._scenes['single'] = SceneDef('single', '单视图')
    registry._scenes['component'] = SceneDef('component', '组件', type='subscene')
    registry._order.extend(['test', 'other', 'single', 'component'])
    registry._init_groups(
        {'first': ['test'], 'second': ['other', 'single'],
         'components': ['component']},
        {'first': '第一页组', 'second': '第二页组', 'components': '组件'},
    )
    saved = []
    monkeypatch.setattr(registry, 'save_scene_views', lambda key: saved.append(key))
    monkeypatch.setattr(scene_view_dialog, 'get_registry', lambda: registry)

    dialog = scene_view_dialog.ViewManagerDialog('test')
    dialog._list.setCurrentRow(1)
    assert dialog._owner_group.findData('components') == -1
    assert dialog._owner_group.currentData() == 'second'
    assert [dialog._owner_scene.itemData(i) for i in range(dialog._owner_scene.count())] == [
        'other', 'single']
    assert dialog._owner_scene.currentData() == 'other'
    assert dialog._owner_view.currentData() == 'second'
    assert dialog._owner_value() == 'other/second'

    dialog._owner_scene.setCurrentIndex(dialog._owner_scene.findData('single'))
    assert dialog._owner_view.currentData() == 'base'
    assert dialog._owner_value() == 'single/base'
    dialog._save_relation()
    assert registry.get_scene('test').views[1].owner == 'single/base'
    assert saved == ['test']
    dialog._owner_group.setCurrentIndex(0)
    assert not dialog._owner_scene.isEnabled()
    assert not dialog._owner_view.isEnabled()
    assert dialog._owner_value() == ''


def test_view_manager_contract_scrolls_instead_of_squeezing(monkeypatch, qapp):
    """入口/跳转条数由场景声明决定，多了必须能滚，而不是把整页挤扁。

    game_settings 的页签视图实测有近 20 条契约行。契约区不封顶时它会按内容一路
    撑高，把视图列表和按钮排压到最小高度以下——控件都还在，只是挤成一片文字，
    读不出哪条属于哪一段。这里钉住「契约区高度有上限 + 溢出时可滚动 + 视图列表
    不被压到最小高度以下」。
    """
    from lvjiang.ui.scene_editor import scene_view_dialog

    registry = SceneRegistry()
    # 一个页签视图 + 足够多的来源场景，凑出溢出所需的行数
    registry._scenes['host'] = SceneDef('host', '宿主', views=[
        ViewDef('base', '页面', kind='page'),
        ViewDef('tab', '标签', kind='tab', owner='/base')])
    registry._order.append('host')
    registry._init_groups({'main': ['host']}, {'main': '主场景'})
    monkeypatch.setattr(scene_view_dialog, 'get_registry', lambda: registry)
    monkeypatch.setattr(
        scene_view_dialog, 'entries_of_view',
        lambda *_a: [_transition(f'entry_{i}') for i in range(10)])
    monkeypatch.setattr(
        scene_view_dialog, 'exits_of_view',
        lambda *_a: [_transition(f'exit_{i}') for i in range(10)])

    dialog = scene_view_dialog.ViewManagerDialog('host')
    dialog.show()
    dialog._list.setCurrentRow(1)
    qapp.processEvents()      # 断言的是布局结果，要等这一轮布局跑完

    # 10 条入口（页签视图还会多一行调用方说明）+ 10 条跳转，一条不少
    assert dialog._entry_lines.count() >= 10
    assert dialog._exit_lines.count() == 10
    scroll = dialog._contract_scroll
    # 封顶：契约区不会随行数无限长
    assert scroll.maximumHeight() <= 260
    assert scroll.height() <= scroll.maximumHeight()
    # 溢出时内容仍是完整高度，且可以滚到底——不是被压缩进视口
    assert dialog._contract.height() > scroll.viewport().height()
    assert scroll.verticalScrollBar().maximum() > 0
    # 视图列表没有被契约区挤到最小高度以下
    assert dialog._list.height() >= dialog._list.minimumHeight()


def _transition(entity: str) -> Transition:
    return Transition(from_scene='host', from_view='tab', entity=entity,
                      to_scene='host', to_view='tab')
