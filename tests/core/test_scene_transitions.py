"""页面切换契约：to: 声明的解析、校验与查询。"""
from __future__ import annotations

from lvjiang.core.scene_definition_models import (
    PointDef,
    RegionDef,
    SceneDef,
    ViewDef,
)
from lvjiang.core.scene_transitions import (
    collect_transitions,
    entries_of_view,
    exits_of_view,
    find_unreachable_views,
    parse_target,
    validate_transitions,
)
from tests.case_matrix import case_matrix


def _scene(key, *, views=(), regions=(), points=(), same_layer=()):
    """views 里列出的 key 默认非同层（独立页面），便于测死视图检测。

    生产默认是同层——新建视图多半是滚动态。这里反过来，是因为绝大多数用例
    要验的是“独立页面缺入口”。要同层的显式写进 same_layer。
    """
    return SceneDef(
        key=key, name=key,
        views=[ViewDef(key=v, name=v, same_layer=v in same_layer)
               for v in views],
        regions=list(regions), points=list(points))


@case_matrix("raw,expected", [
    ("equip_tune_detail", ("equip_tune_detail", "")),
    ("equip_tune_detail/result", ("equip_tune_detail", "result")),
    ("/result", ("here", "result")),
    ("  bag_detail / base  ", ("bag_detail", "base")),
    ("", None),
    ("   ", None),
])
def test_parse_target(raw, expected):
    assert parse_target(raw, "here") == expected


def test_multi_view_button_yields_one_edge_per_view():
    """同一个按钮属于多个视图时，每个视图各算一条边。

    close_btn 在结果视图和返还视图都在——两个视图各自都有这条出边。
    """
    scenes = {"tune": _scene(
        "tune", views=("base", "result", "return_good"),
        regions=[RegionDef(key="close_btn", name="关闭", type="func",
                           is_clickable=True,
                           views=["result", "return_good"], to="/base")])}

    edges = collect_transitions(scenes)
    assert {e.from_view for e in edges} == {"result", "return_good"}
    assert all(e.is_internal and e.to_view == "base" for e in edges)


def test_validate_reports_bad_targets():
    scenes = {
        "a": _scene("a", views=("base", "v1"), regions=[
            RegionDef(key="ok", name="ok", is_clickable=True, to="b"),
            RegionDef(key="nope", name="nope", is_clickable=True, to="ghost"),
            RegionDef(key="badview", name="badview", is_clickable=True,
                      to="b/missing"),
        ]),
        "b": _scene("b"),
    }

    problems = validate_transitions(scenes)
    assert len(problems) == 2
    assert any("ghost" in p for p in problems)
    assert any("missing" in p for p in problems)
    # 合法的那条不该出现在问题里
    assert not any("[a].[ok]" in p for p in problems)


def test_base_view_target_requires_multi_view():
    scenes = {"a": _scene("a", regions=[
        RegionDef(key="x", name="x", is_clickable=True, to="a/base")])}
    assert validate_transitions(scenes)  # 未开启多视图却指向 base


def test_unreachable_views_are_reported():
    """没有任何入口的非基底视图 = 死视图，要么漏声明入口，要么不该存在。"""
    scenes = {"tune": _scene(
        "tune", views=("base", "result", "return_good"),
        regions=[RegionDef(key="tune_btn", name="调律", is_clickable=True,
                           to="/result")])}

    # result 有入口，return_good 没有
    assert find_unreachable_views(scenes) == ["tune/return_good"]


def test_base_view_never_counts_as_unreachable():
    """基底是场景入口，不需要场景内的按钮指向它。"""
    scenes = {"a": _scene("a", views=("base",))}
    assert find_unreachable_views(scenes) == []


def test_same_layer_views_are_exempt():
    """同层视图与基底处于同一图层，只是滚动后的另一个取景，本就没有“进入”这回事。

    菜单的 page_1 / page_2 就是典型：没有按钮进入它们，你只是把同一页滚过去了。
    不豁免的话死视图检测会满屏假警报。
    """
    scenes = {"menu": _scene(
        "menu", views=("base", "page_1", "page_2"),
        same_layer=("page_1", "page_2"))}

    assert find_unreachable_views(scenes) == []


def test_same_layer_view_still_contributes_exits():
    """同层视图没有入口，但它上面的按钮照样能跳到别处。"""
    scenes = {
        "menu": _scene("menu", views=("base", "page_2"),
                       same_layer=("page_2",),
                       regions=[RegionDef(key="bag", name="包裹",
                                          is_clickable=True,
                                          views=["page_2"], to="bag")]),
        "bag": _scene("bag"),
    }

    edges = collect_transitions(scenes)
    assert [(e.from_view, e.to_scene) for e in edges] == [("page_2", "bag")]
    assert find_unreachable_views(scenes) == []


def test_entry_and_exit_queries():
    scenes = {
        "bag": _scene("bag", views=("base",), regions=[
            RegionDef(key="tune", name="调律", is_clickable=True,
                      to="tune/base")]),
        "tune": _scene("tune", views=("base", "result"), points=[
            PointDef(key="go", name="go", views=["base"], to="/result")]),
    }

    entries = entries_of_view(scenes, "tune", "base")
    assert [(e.from_scene, e.entity) for e in entries] == [("bag", "tune")]

    exits = exits_of_view(scenes, "tune", "base")
    assert [(e.entity, e.to_view) for e in exits] == [("go", "result")]


def test_non_clickable_entity_cannot_contribute_transition():
    scenes = {
        "a": _scene("a", regions=[
            RegionDef(key="label", name="标签", is_clickable=False, to="b")]),
        "b": _scene("b"),
    }

    assert collect_transitions(scenes) == []
    assert validate_transitions(scenes) == [
        "[a].[label] 的 to='b' 属于不可点击实体，不能声明跳转"]
