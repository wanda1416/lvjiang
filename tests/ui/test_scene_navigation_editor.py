import pytest
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox

from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.core.scene_definition_models import SceneDef, ViewDef
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
