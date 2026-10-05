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


def _system_wf_files() -> list:
    """系统 .wf 清单，跳过 `_` 前缀的编辑器临时脚本。

    临时运行脚本不属于发布配置，可能保留用户上次调试的旧引用。
    """
    workflows_dir = SYSTEM_CONFIG_DIR / "workflows"
    return sorted(
        p for p in workflows_dir.rglob("*.wf")
        if not any(part.startswith("_")
                   for part in p.relative_to(workflows_dir).parts)
    )


@pytest.mark.parametrize(
    "wf_path", _system_wf_files(),
    ids=lambda p: p.relative_to(SYSTEM_CONFIG_DIR / "workflows").as_posix())
def test_workflow_appearance_references_are_bound(appearance_config, wf_path):
    """系统脚本对外观场景的引用必须指向场景定义里存在的区域。

    按脚本参数化：每个脚本都要读盘解析，折成单项会让 xdist 只能在一个 worker
    上串行跑完，成为整条流水线的长尾。
    """
    _, registry = appearance_config
    scene = registry.get_scene("appearance_main")
    assert scene is not None
    expected_keys = {r.key for r in scene.regions}
    program = parse_file(wf_path)
    for ref in collect_refs(program.body, program.procs, reachable_only=False):
        assert ref.scene not in {"waiguan_yigui", "waiguan_qingjing"}, wf_path
        if ref.scene == "appearance_main":
            assert ref.key in expected_keys, (wf_path, ref)


def test_appearance_scene_is_referenced_by_some_workflow():
    """前提门禁：确实有脚本引用 appearance_main，否则上面的绑定检查会空转。"""
    hits = [
        path for path in _system_wf_files()
        if "appearance_main" in path.read_text(encoding="utf-8")
    ]
    assert hits, "没有任何系统脚本引用 appearance_main"


def test_appearance_bindings_match_scene_definition(appearance_config):
    """三套布局与磁盘上的绑定文件都必须与场景定义一对一。

    引用脚本的 key 已由 ``test_workflow_appearance_references_are_bound``
    逐个限定在场景定义内，这里只需保证布局与存储文件覆盖同一集合。
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
