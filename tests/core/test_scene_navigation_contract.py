"""页面归属、动态返回和公共入口的声明契约。"""
from lvjiang.core.scene_definition_models import (
    RegionDef,
    SceneDef,
    SceneRefDef,
    ViewDef,
)
from lvjiang.core.scene_transitions import (
    collect_transitions,
    page_owner,
    validate_transitions,
)


def test_shared_tabs_and_nested_caller_are_distinct():
    scene = SceneDef('appearance', '外观', views=[
        ViewDef('base', '衣柜', kind='page'),
        ViewDef('tab', '情境', kind='tab', owner='/base'),
        ViewDef('plan', '方案', kind='page'),
        ViewDef('scroll', '方案第二屏', kind='viewport', owner='/plan'),
    ], regions=[
        RegionDef('tab', '情境', is_clickable=True, to='/tab', navigation='switch'),
        RegionDef('plan', '方案', is_clickable=True, views=['base', 'tab'], to='/plan', navigation='open'),
        RegionDef('back', '返回', is_clickable=True, views=['base', 'tab', 'plan', 'scroll'], to='@caller'),
    ])
    scenes = {'appearance': scene}
    assert not validate_transitions(scenes)
    assert page_owner(scenes, 'appearance', 'tab') == ('appearance', 'base')
    assert page_owner(scenes, 'appearance', 'scroll') == ('appearance', 'plan')
    edges = collect_transitions(scenes)
    assert len([e for e in edges if e.is_return]) == 4
    assert {e.from_view for e in edges if e.navigation == 'open'} == {'base', 'tab'}
    scene.regions[1].navigation = 'switch'
    assert any('不属于同一页面' in p for p in validate_transitions(scenes))


def test_reference_return_and_explicit_empty_override():
    common = SceneDef('common', '公共', regions=[RegionDef('cancel', '取消', is_clickable=True, to='@caller')])
    host = SceneDef('host', '宿主', views=[ViewDef('base', '页面'), ViewDef('dialog', '弹层', kind='modal')],
                    references=[SceneRefDef('common', 'cancel', views=['dialog'])])
    scenes = {'common': common, 'host': host}
    edge = next(e for e in collect_transitions(scenes) if e.from_scene == 'host')
    assert edge.is_return and edge.from_view == 'dialog'
    host.references[0].to = ''
    assert not any(e.from_scene == 'host' for e in collect_transitions(scenes))
    assert host.references[0].to_dict()['to'] == ''


def test_global_entry_records_definition_and_current_source():
    source = SceneDef('common', '公共', regions=[RegionDef('settings', '设置', is_clickable=True,
                      to='settings', navigation='open', available_from=['*'])])
    scenes = {'common': source, 'settings': SceneDef('settings', '设置'),
              'host': SceneDef('host', '主页')}
    edges = collect_transitions(scenes)
    edge = next(e for e in edges if e.from_scene == 'host')
    assert edge.definition_scene == 'common' and edge.navigation == 'open'
    assert not any(e.from_scene == 'settings' for e in edges)
    assert not validate_transitions(scenes)


def test_cross_scene_tab_and_relation_cycles():
    scenes = {'bag': SceneDef('bag', '背包'),
              'items': SceneDef('items', '道具', views=[ViewDef('base', '道具', kind='tab', owner='bag/base')])}
    assert page_owner(scenes, 'items', 'base') == ('bag', 'base')
    scenes['items'].views[0].owner = '/base'
    assert any('循环' in p for p in validate_transitions(scenes))


def test_navigation_roundtrip_and_view_reference_rename(tmp_path):
    import pytest
    import yaml

    from lvjiang.core.config.resolver import ConfigResolver
    from lvjiang.core.scene_definition import SceneRegistry

    system = tmp_path / 'system'
    directory = system / 'scenes'
    directory.mkdir(parents=True)
    docs = {
        'source': {'key': 'source', 'name': '源',
                   'views': [{'key': 'base', 'name': '基底'}, {'key': 'child', 'name': '子页', 'kind': 'page'}],
                   'regions': [{'key': 'open', 'name': '打开', 'type': 'func', 'is_clickable': True,
                                'to': '/child', 'navigation': 'open', 'available_from': ['host']}]},
        'host': {'key': 'host', 'name': '宿主',
                 'views': [{'key': 'base', 'name': '基底', 'kind': 'tab', 'owner': 'source/child'}],
                 'references': [{'scene': 'source', 'entity': 'open', 'to': '', 'navigation': ''}]},
    }
    for key, doc in docs.items():
        (directory / f'{key}.yaml').write_text(
            yaml.safe_dump(doc, allow_unicode=True), encoding='utf-8')
    resolver = ConfigResolver(system_dir=system, local_dir=tmp_path / 'local', dev_mode=True)
    registry = SceneRegistry(resolver=resolver)
    for key in docs:
        registry.save_scene_views(key)
    registry = SceneRegistry(resolver=resolver)
    assert registry.get_scene('host').references[0].to == ''
    assert registry.get_scene('source').regions[0].available_from == ['host']
    registry.rename_scene_view_key('source', 'child', 'renamed', '改名')
    registry = SceneRegistry(resolver=resolver)
    assert registry.get_scene('host').views[0].owner == 'source/renamed'
    assert registry.get_scene('source').regions[0].to == '/renamed'
    with pytest.raises(ValueError, match='引用'):
        registry.delete_scene_view('source', 'renamed')


def test_system_navigation_and_desktop_shortcuts():
    from lvjiang.core import layout_manager
    from lvjiang.core.key_validation import validate_layout_activation_keys
    from lvjiang.core.scene_registry import get_registry

    scenes = get_registry().all_scenes()
    assert not validate_transitions(scenes)
    for name in ('桌面布局', '默认布局'):
        layout = layout_manager.load_layout_by_name(name)
        validate_layout_activation_keys(layout, {'game_main_page', 'general_control'})
        setting = next(r for r in layout.get_scene_regions('general_control') if r.key == 'open_settings')
        bag = next(r for r in layout.get_scene_regions('game_main_page') if r.key == 'bag')
        if name == '桌面布局':
            assert setting.activation_key == 'SLASH' and bag.activation_key == 'B'
        else:
            assert setting.disabled and bag.disabled
