"""外观统一场景：tab、独立编辑页、布局绑定和工作流引用契约。"""

import json

import pytest

from lvjiang.core import layout_manager
from lvjiang.core.config.resolver import SYSTEM_CONFIG_DIR, ConfigResolver
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.core.scene_transitions import find_unreachable_views, validate_transitions
from lvjiang.workflows.grammar import parse_file
from lvjiang.workflows.workflow_references import collect_refs


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


def test_appearance_bindings_and_all_workflow_references(appearance_config):
    _, registry = appearance_config
    scene = registry.get_scene("appearance_main")
    assert scene is not None
    expected_keys = {r.key for r in scene.regions}
    refs = []
    workflows_dir = SYSTEM_CONFIG_DIR / "workflows"
    for path in workflows_dir.rglob("*.wf"):
        # 编辑器临时运行脚本不属于发布配置，可能保留用户上次调试的旧引用。
        if any(part.startswith("_") for part in path.relative_to(workflows_dir).parts):
            continue
        program = parse_file(path)
        for ref in collect_refs(program.body, program.procs, reachable_only=False):
            assert ref.scene not in {"waiguan_yigui", "waiguan_qingjing"}, path
            if ref.scene == "appearance_main":
                assert ref.key in expected_keys, (path, ref)
                refs.append(ref)
    assert refs
    for name in ("默认布局", "桌面布局", "继承布局"):
        layout = layout_manager.load_layout_by_name(name)
        assert layout is not None
        regions = layout.get_scene_regions("appearance_main")
        bound = {r.key: r for r in regions}
        assert len(bound) == len(regions)
        assert set(bound) == expected_keys
        assert not {"waiguan_yigui", "waiguan_qingjing"} & set(layout.regions)
        assert all(ref.key in bound for ref in refs)
        if name == "桌面布局":
            assert bound["back"].activation_key == "ESC"
            for key in ("save", "edit_qingjing", "taoyong"):
                assert bound[key].activation_key == "SPACE"
    # 实际存储文件也必须一对一绑定，不只依赖加载器可能做的去重。
    for name in ("默认布局", "桌面布局"):
        path = SYSTEM_CONFIG_DIR / "layouts" / name / "appearance_main.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        keys = [r["key"] for r in data["regions"]]
        assert len(keys) == len(set(keys)) == len(expected_keys)
