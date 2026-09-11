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


def test_navigation_picker_is_on_second_row(monkeypatch):
    from lvjiang.ui.scene_editor import scene_select

    registry = SceneRegistry()
    registry._init_groups({'all': registry.all_scene_keys()}, None)
    monkeypatch.setattr(scene_select, 'get_registry', lambda: registry)
    monkeypatch.setattr(scene_select, 'get_scene_views', registry.get_scene_views)
    picker = scene_select.TransitionPicker('game_settings')
    picker.resize(560, picker.sizeHint().height())
    picker.show()
    picker.layout().activate()

    assert picker.layout().itemAt(0).layout().count() == 3
    assert picker.layout().itemAt(1).widget() is picker._navigation
    assert picker._navigation.x() == picker._group.x()
    assert picker._navigation.y() > picker._group.geometry().bottom()
    assert picker._navigation.width() >= picker._navigation.sizeHint().width()
    assert picker._navigation.geometry().right() == picker._view.geometry().right()
    picker.close()


def test_view_manager_saves_explicit_owner(monkeypatch):
    from lvjiang.ui.scene_editor import scene_view_dialog
    registry = SceneRegistry()
    registry._scenes['test'] = SceneDef('test', '测试', views=[
        ViewDef('base', '页面', kind='page'), ViewDef('tab', '标签', kind='tab', owner='/base')])
    registry._order.append('test')
    saved = []
    monkeypatch.setattr(registry, 'save_scene_views', lambda key: saved.append(key))
    monkeypatch.setattr(scene_view_dialog, 'get_registry', lambda: registry)
    dialog = scene_view_dialog.ViewManagerDialog('test')
    dialog._list.setCurrentRow(1)
    assert dialog._relation.currentData() == 'tab'
    assert dialog._owner.currentData() == '/base'
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
