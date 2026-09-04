"""跨场景 area 引用：只引用一级场景，坐标转读不复制。"""
from __future__ import annotations

import json

import pytest
import yaml

from lvjiang.core.layout_manager import (
    _drop_orphan_coords,
    _expand_scene_references,
)
from lvjiang.core.layout_models import Point, Region
from lvjiang.core.scene_definition import SceneRegistry


def _write(dir_path, key, doc):
    doc.setdefault("key", key)
    doc.setdefault("name", key)
    (dir_path / f"{key}.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


class _Resolver:
    """最小 resolver 替身：只从一个目录枚举/读取场景。"""

    def __init__(self, root):
        self.root = root

    def enumerate_entities(self, _kind, _pattern):
        return sorted(p.name for p in self.root.glob("*.yaml"))

    def resolve_read(self, rel):
        return self.root / rel.split("/", 1)[1]


@pytest.fixture
def scenes_dir(tmp_path):
    d = tmp_path / "scenes"
    d.mkdir()
    return d


def _registry(scenes_dir):
    return SceneRegistry(resolver=_Resolver(scenes_dir))


def test_reference_joins_the_referencing_scene_namespace(scenes_dir):
    _write(scenes_dir, "general_control", {
        "regions": [{"key": "confirm", "name": "确认", "type": "func"}]})
    _write(scenes_dir, "equip_tune_detail", {
        "regions": [{"key": "close_btn", "name": "关闭", "type": "func"}],
        "references": [{"scene": "general_control", "entity": "confirm"}]})

    scene = _registry(scenes_dir).get_scene("equip_tune_detail")
    assert [r.key for r in scene.references] == ["confirm"]
    assert scene.references[0].scene == "general_control"


def test_subscene_cannot_be_referenced(scenes_dir):
    """子场景实体坐标相对外框，搬过来需要变换；只允许一级场景。"""
    _write(scenes_dir, "jianghu_card", {
        "type": "subscene",
        "regions": [{"key": "title", "name": "标题", "type": "attr"}]})
    _write(scenes_dir, "activity_jianghu", {
        "references": [{"scene": "jianghu_card", "entity": "title"}]})

    scene = _registry(scenes_dir).get_scene("activity_jianghu")
    assert scene.references == []


def test_reference_to_a_reference_is_rejected(scenes_dir):
    """禁止传递：源必须是原生定义。"""
    _write(scenes_dir, "a", {
        "regions": [{"key": "confirm", "name": "确认", "type": "func"}]})
    _write(scenes_dir, "b", {"references": [{"scene": "a", "entity": "confirm"}]})
    _write(scenes_dir, "c", {"references": [{"scene": "b", "entity": "confirm"}]})

    reg = _registry(scenes_dir)
    assert [r.key for r in reg.get_scene("b").references] == ["confirm"]
    assert reg.get_scene("c").references == []


def test_missing_source_is_dropped_not_fatal(scenes_dir):
    """单个场景写错不该让整个注册表加载失败，否则 UI 都起不来。"""
    _write(scenes_dir, "a", {"regions": [{"key": "x", "name": "X", "type": "func"}]})
    _write(scenes_dir, "b", {"references": [
        {"scene": "nope", "entity": "gone"},      # 源场景不存在
        {"scene": "a", "entity": "missing"},      # 源场景没有该实体
        {"scene": "a", "entity": "x"},            # 合法
    ]})

    reg = _registry(scenes_dir)
    # 非法的两条被丢弃，合法的保留；场景本身照常可用。
    assert [r.key for r in reg.get_scene("b").references] == ["x"]
    assert reg.get_scene("a") is not None


def test_two_references_sharing_a_key_are_rejected(scenes_dir):
    """引用名恒等于源实体 key，不支持重命名，所以同名引用无法共存。

    这是"少一个概念"的代价，钉下来免得以后误以为能同时引用
    general_control.confirm 和 general_action.confirm。
    """
    _write(scenes_dir, "s1", {
        "regions": [{"key": "confirm", "name": "确认", "type": "func"}]})
    _write(scenes_dir, "s2", {
        "regions": [{"key": "confirm", "name": "确认", "type": "func"}]})
    _write(scenes_dir, "b", {"references": [
        {"scene": "s1", "entity": "confirm"},
        {"scene": "s2", "entity": "confirm"},
    ]})

    assert _registry(scenes_dir).get_scene("b") is None


def test_self_reference_and_name_collision_are_rejected(scenes_dir):
    _write(scenes_dir, "a", {
        "regions": [{"key": "x", "name": "X", "type": "func"}],
        "references": [{"scene": "a", "entity": "x"}]})
    # 自引用在加载期抛错 → 该场景整体加载失败被记 error 并跳过
    assert _registry(scenes_dir).get_scene("a") is None

    _write(scenes_dir, "src", {
        "regions": [{"key": "dup", "name": "D", "type": "func"}]})
    _write(scenes_dir, "b", {
        "regions": [{"key": "dup", "name": "本地", "type": "func"}],
        "references": [{"scene": "src", "entity": "dup"}]})
    # 引用与原生同名 → key 去重直接拒绝，绝不静默覆盖
    assert _registry(scenes_dir).get_scene("b") is None


def test_expansion_copies_coordinates_verbatim_and_marks_the_source():
    """一级场景坐标同属画布归一化，零变换直取。"""
    regions = {"general_control": [
        Region(key="confirm", x_ratio=0.4, y_ratio=0.5,
               w_ratio=0.1, h_ratio=0.05, activation_key="SPACE")]}
    points: dict = {}

    class _Scene:
        references = [type("R", (), {"scene": "general_control",
                                     "entity": "confirm"})()]

    import lvjiang.core.layout_manager as lm
    original = lm.get_registry if hasattr(lm, "get_registry") else None
    import lvjiang.core.scene_registry as sr
    saved = sr.get_registry
    sr.get_registry = lambda: type(
        "Reg", (), {"all_scenes": lambda self: {"equip_tune_detail": _Scene()}})()
    try:
        _expand_scene_references(regions, points)
    finally:
        sr.get_registry = saved
        assert original is None or lm.get_registry is original

    got = regions["equip_tune_detail"][0]
    src = regions["general_control"][0]
    assert (got.x_ratio, got.y_ratio, got.w_ratio, got.h_ratio) == (
        src.x_ratio, src.y_ratio, src.w_ratio, src.h_ratio)
    assert got.activation_key == "SPACE"
    assert got.source_scene == "general_control"
    assert got.is_reference and not src.is_reference


def test_reference_never_serializes_back_into_the_layout():
    """写回就把引用烘死成拷贝，源场景改坐标不再同步——两道闸都要在。"""
    ref = Region(key="confirm", x_ratio=0.4, y_ratio=0.5, w_ratio=0.1,
                 h_ratio=0.05, source_scene="general_control")
    assert "source_scene" not in ref.to_dict()
    assert "source_scene" not in json.dumps(ref.to_dict())

    p = Point(key="p", cx_ratio=0.1, cy_ratio=0.2, source_scene="other")
    assert "source_scene" not in p.to_dict()


def test_dsl_addressing_works_without_touching_the_action_layer():
    """展开在加载期做，所以 get_scene_regions 仍是纯查表。

    click [equip_tune_detail].[confirm] 走的就是这条路径，
    click_region / click_any / _validate_refs_bound 一行都不用改。
    """
    from lvjiang.core.layout_models import Layout

    regions = {"general_control": [
        Region(key="confirm", x_ratio=0.4, y_ratio=0.5,
               w_ratio=0.1, h_ratio=0.05)]}

    class _Scene:
        references = [type("R", (), {"scene": "general_control",
                                     "entity": "confirm"})()]

    import lvjiang.core.scene_registry as sr
    saved = sr.get_registry
    sr.get_registry = lambda: type(
        "Reg", (), {"all_scenes": lambda self: {"equip_tune_detail": _Scene()}})()
    try:
        _expand_scene_references(regions, {})
    finally:
        sr.get_registry = saved

    layout = Layout(name="t", regions=regions)
    found = next(r for r in layout.get_scene_regions("equip_tune_detail")
                 if r.key == "confirm")
    assert found.source_scene == "general_control"


def test_save_layout_never_writes_references_to_disk(tmp_path, monkeypatch):
    """真正走 save_layout：引用项不能落进本场景的布局 JSON。

    写回就把引用烘死成拷贝，源场景再改坐标也不同步——正好毁掉这个特性的
    全部意义，而且完全静默（保存一次配置就污染了，看起来一切正常）。
    """
    import lvjiang.constants as constants
    import lvjiang.core.config.resolver as cr
    from lvjiang.core import layout_manager
    from lvjiang.core.layout_manager import LayoutConfigManager
    from lvjiang.core.layout_models import CanvasConfig, Layout

    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", tmp_path / "system")
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", tmp_path / "local")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(layout_manager, "SESSION_CONFIG_DIR", tmp_path)
    monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
    monkeypatch.setattr(cr, "_resolver", None)

    layout = Layout(name="布局A", canvas=CanvasConfig(),
                    regions={"equip_tune_detail": [
                        Region(key="close_btn", x_ratio=0.7, y_ratio=0.7,
                               w_ratio=0.06, h_ratio=0.07),
                        Region(key="confirm", x_ratio=0.4, y_ratio=0.5,
                               w_ratio=0.1, h_ratio=0.05,
                               source_scene="general_control"),
                    ]})
    assert LayoutConfigManager().save_layout(layout)

    written = json.loads(next(
        tmp_path.rglob("equip_tune_detail.json")).read_text(encoding="utf-8"))
    assert [r["key"] for r in written["regions"]] == ["close_btn"]
    assert "source_scene" not in json.dumps(written)



def test_view_filter_includes_references(scenes_dir, monkeypatch):
    """选中视图时引用项必须仍然可见——否则画布不画它们，而右侧列表里有。

    视图过滤按场景定义算，引用项如果不算进去就会被当成"不属于这个视图"。
    """
    _write(scenes_dir, "general_control", {
        "regions": [{"key": "confirm", "name": "确认", "type": "func"}],
        "points": [{"key": "anchor", "name": "锚点", "type": "func"}]})
    _write(scenes_dir, "equip_tune_detail", {
        "views": [{"key": "base", "name": "基底"},
                  {"key": "reset", "name": "重置"}],
        "regions": [{"key": "close_btn", "name": "关闭", "type": "func",
                     "view": "reset"}],
        "references": [
            {"scene": "general_control", "entity": "confirm", "view": "reset"},
            {"scene": "general_control", "entity": "anchor", "view": "reset"},
        ]})

    reg = _registry(scenes_dir)
    import lvjiang.core.scene_registry as sr
    monkeypatch.setattr(sr, "_registry", reg)

    visible = sr.get_view_visible_keys("equip_tune_detail", "reset")
    assert visible == {"close_btn", "confirm", "anchor"}

    # 不属于该视图的引用不该混进来
    assert sr.get_view_visible_keys("equip_tune_detail", "base") == set()


def test_points_are_referenceable_too(scenes_dir):
    """坐标和区域本质都是 area，都可以被引用。"""
    _write(scenes_dir, "src", {
        "points": [{"key": "anchor", "name": "锚点", "type": "func"}]})
    _write(scenes_dir, "dst", {
        "references": [{"scene": "src", "entity": "anchor"}]})

    scene = _registry(scenes_dir).get_scene("dst")
    assert [r.entity for r in scene.references] == ["anchor"]


def test_point_references_expand_into_the_point_table():
    """引用的坐标要展开进 points 表，区域进 regions 表，各归各的。"""
    regions = {"src": [Region(key="btn", x_ratio=0.1, y_ratio=0.1,
                              w_ratio=0.1, h_ratio=0.1)]}
    points = {"src": [Point(key="anchor", cx_ratio=0.5, cy_ratio=0.5)]}

    class _Scene:
        references = [
            type("R", (), {"scene": "src", "entity": "anchor"})(),
            type("R", (), {"scene": "src", "entity": "btn"})(),
        ]

    import lvjiang.core.scene_registry as sr
    saved = sr.get_registry
    sr.get_registry = lambda: type(
        "Reg", (), {"all_scenes": lambda self: {"dst": _Scene()}})()
    try:
        _expand_scene_references(regions, points)
    finally:
        sr.get_registry = saved

    assert [p.key for p in points["dst"]] == ["anchor"]
    assert [r.key for r in regions["dst"]] == ["btn"]
    assert points["dst"][0].source_scene == "src"
    assert regions["dst"][0].source_scene == "src"


class _Def:
    def __init__(self, key):
        self.key = key


def _fake_registry(scene_key, region_keys, point_keys, references):
    """同时喂饱 _drop_orphan_coords（get_scene）与展开（all_scenes）。"""
    scene = type("S", (), {
        "regions": [_Def(k) for k in region_keys],
        "points": [_Def(k) for k in point_keys],
        "references": references,
    })()
    return type("Reg", (), {
        "all_scenes": lambda self: {scene_key: scene},
        "get_scene": lambda self, key: scene if key == scene_key else None,
    })()


def test_orphan_coords_are_dropped_so_the_reference_can_expand(monkeypatch):
    """定义删了、布局坐标没删的残留项会顶掉同名引用的展开。

    game_login_page 的真实事故：cancel 从原生 region 改成引用 general_control
    之后，布局 JSON 里的旧坐标还留着。它没有 source_scene，保存路径不过滤它，
    于是每次加载都撞名、引用永远展不开，click 一直点那份再也不会同步的陈旧
    坐标，而且完全静默。
    """
    import lvjiang.core.scene_registry as sr

    regions = {
        "general_control": [
            Region(key="cancel", x_ratio=0.05, y_ratio=0.64,
                   w_ratio=0.10, h_ratio=0.09)],
        "game_login_page": [
            Region(key="enter", x_ratio=0.80, y_ratio=0.89,
                   w_ratio=0.16, h_ratio=0.07),
            Region(key="cancel", x_ratio=0.50, y_ratio=0.50,
                   w_ratio=0.10, h_ratio=0.10),   # 孤儿：定义已删
        ],
    }
    points = {"game_login_page": [
        Point(key="user_2", cx_ratio=0.3, cy_ratio=0.5)]}

    ref = type("R", (), {"scene": "general_control", "entity": "cancel"})()
    monkeypatch.setattr(sr, "get_registry", lambda: _fake_registry(
        "game_login_page", ["enter"], ["point_1"], [ref]))

    _drop_orphan_coords(regions, points)
    _expand_scene_references(regions, points)

    # 孤儿清掉后引用正常展开，坐标取自源场景而不是那份陈旧残留
    assert [r.key for r in regions["game_login_page"]] == ["enter", "cancel"]
    got = regions["game_login_page"][1]
    assert got.source_scene == "general_control" and got.is_reference
    assert (got.x_ratio, got.y_ratio) == (0.05, 0.64)
    # 源场景自己那一格不在注册表里（get_scene 认不出）→ 整体跳过，不误删
    assert [r.key for r in regions["general_control"]] == ["cancel"]
    assert points["game_login_page"] == []


def test_orphan_cleanup_refuses_to_run_without_definitions(monkeypatch):
    """两道保险：注册表炸了、场景 YAML 没了，都不能清坐标。

    这两种情况下“已定义 key 集合”要么拿不到要么是空的，照清等于把整套布局
    的坐标抹掉——比留着孤儿糟得多，而且下次保存就永久写死。
    """
    import lvjiang.core.scene_registry as sr

    regions = {"s": [Region(key="a", x_ratio=0.1, y_ratio=0.1,
                            w_ratio=0.1, h_ratio=0.1)]}

    def _boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr(sr, "get_registry", _boom)
    _drop_orphan_coords(regions, {})
    assert [r.key for r in regions["s"]] == ["a"]

    monkeypatch.setattr(sr, "get_registry", lambda: type(
        "Reg", (), {"get_scene": lambda self, key: None})())
    _drop_orphan_coords(regions, {})
    assert [r.key for r in regions["s"]] == ["a"]


def test_referenced_entity_shows_the_source_name_not_its_key(
        scenes_dir, monkeypatch):
    """引用行的名称要回源场景取。

    引用只存 (scene, entity)，本场景的 regions/points 里查不到它；名称查询
    若就此退回 key，画布标签和面板列表里就只有引用项标着 key。
    """
    import lvjiang.core.scene_registry as scene_registry

    _write(scenes_dir, "general_control", {
        "regions": [{"key": "confirm", "name": "确认", "type": "func"}],
        "points": [{"key": "close_dot", "name": "关闭点", "type": "func"}]})
    _write(scenes_dir, "equip_tune_detail", {
        "regions": [{"key": "close_btn", "name": "关闭", "type": "func"}],
        "references": [{"scene": "general_control", "entity": "confirm"},
                       {"scene": "general_control", "entity": "close_dot"}]})
    monkeypatch.setattr(
        scene_registry, "_registry", _registry(scenes_dir))

    assert scene_registry.get_region_name(
        "equip_tune_detail", "close_btn") == "关闭"
    assert scene_registry.get_region_name(
        "equip_tune_detail", "confirm") == "确认"
    assert scene_registry.get_point_name(
        "equip_tune_detail", "close_dot") == "关闭点"
    # 查无此实体仍退回 key，调用方靠它显示"这行的定义丢了"
    assert scene_registry.get_region_name(
        "equip_tune_detail", "nope") == "nope"
