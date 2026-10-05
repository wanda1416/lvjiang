"""外观统一场景：tab、独立编辑页、布局绑定和工作流引用契约。"""

import json

import pytest

from lvjiang.core import layout_manager
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR, ConfigResolver
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.core.scene_transitions import find_unreachable_views, validate_transitions


@pytest.fixture
def appearance_config(tmp_path, monkeypatch):
    resolver = ConfigResolver(
        system_dir=SYSTEM_CONFIG_DIR,
        local_dir=tmp_path / "local",
        remote_dir=tmp_path / "remote",
        dev_mode=False,
    )
    monkeypatch.setattr(layout_manager, "get_resolver", lambda: resolver)
    return resolver, SceneRegistry(resolver=resolver)


def test_appearance_tabs_and_independent_editors(appearance_config):
    resolver, registry = appearance_config
    social = resolver.load_merged("scenes.yaml")["scenes"]["social"]["items"]
    assert social.count("appearance_main") == 1
    assert not {"waiguan_yigui", "waiguan_qingjing"} & set(registry.all_scene_keys())
    assert not {"waiguan_yigui", "waiguan_qingjing"} & set(social)
    scene = registry.get_scene("appearance_main")
    assert scene is not None
    assert [(v.key, v.name, v.relation) for v in scene.views] == [
        ("base", "衣柜", "page"),
        ("qingjing", "情境", "tab"),
        ("chuanda", "穿搭方案", "page"),
        ("qingjing_editing", "情境编辑", "page"),
    ]
    visible = {
        v.key: {r.key for r in scene.regions if v.key in (r.views or ["base"])}
        for v in scene.views
    }
    assert visible == {
        "base": {"yigui", "qingjing", "chuanda", "back"},
        "qingjing": {"yigui", "qingjing", "edit_qingjing", "back"},
        "chuanda": {"fangan_1", "taoyong", "back"},
        "qingjing_editing": {"save", "back"},
    }
    regions = {r.key: r for r in scene.regions}
    assert len(regions) == len(scene.regions)
    assert regions["yigui"].to == "/base"
    assert regions["qingjing"].to == "/qingjing"
    assert regions["chuanda"].to == "/chuanda"
    assert regions["edit_qingjing"].to == "/qingjing_editing"
    # 同位置返回按钮跨视图复用，目标随当前视图和入口上下文变化。
    assert regions["back"].to == "@caller"
    assert set(regions["back"].views) == {v.key for v in scene.views}
    for source, key, target in [
        ("game_menu_page", "waiguan", "appearance_main"),
        ("activity_jianghu", "goto_qingjing", "appearance_main/qingjing_editing"),
    ]:
        entry = registry.get_scene(source)
        assert entry is not None
        assert next(r for r in entry.regions if r.key == key).to == target
    assert not [
        p for p in validate_transitions(registry.all_scenes())
        if "appearance_main" in p or "waiguan_" in p
    ]
    assert not [
        v for v in find_unreachable_views(registry.all_scenes())
        if v.startswith("appearance_main/")
    ]


def test_appearance_bindings_match_scene_definition(appearance_config):
    """三套布局与磁盘上的绑定文件都必须与场景定义一对一。

    引用脚本的 key 由通用 ``test_system_wf_refs_gate`` 检查；
    这里只需保证布局与存储文件覆盖同一集合。
    """
    _, registry = appearance_config
    scene = registry.get_scene("appearance_main")
    assert scene is not None
    expected_keys = {r.key for r in scene.regions}
    for name in ("android", "desktop", "android_cast"):
        layout = layout_manager.load_layout_by_key(name)
        assert layout is not None
        regions = layout.get_scene_regions("appearance_main")
        bound = {r.key: r for r in regions}
        assert len(bound) == len(regions)
        assert set(bound) == expected_keys
        assert not {"waiguan_yigui", "waiguan_qingjing"} & set(layout.regions)
        if name == "desktop":
            assert bound["back"].activation_key == "ESC"
            for key in ("save", "edit_qingjing", "taoyong"):
                assert bound[key].activation_key == "SPACE"
    # 实际存储文件也必须一对一绑定，不只依赖加载器可能做的去重。
    for name in ("android", "desktop"):
        path = SYSTEM_CONFIG_DIR / "layouts" / name / "appearance_main.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = [r["key"] for r in data["regions"]]
        assert len(keys) == len(set(keys)) == len(expected_keys)
