"""画布上的实例删除：定义删了，画布也得删。

场景定义删掉之后画布若还留着，下一次保存布局会把 ``get_regions()`` 里的它
原样写回去——刚从磁盘删掉的坐标立刻复活，而且看不出来。
"""
from __future__ import annotations

import pytest

from lvjiang.core.layout_models import Arrow, Panel, Point, Region, SubsceneRef
from lvjiang.ui.scene_editor.canvas import RegionCanvas

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def canvas():
    widget = RegionCanvas()
    widget.set_regions([
        Region("btn", 0.1, 0.1, 0.2, 0.2),
        Region("keep", 0.5, 0.5, 0.1, 0.1),
    ])
    widget.set_points([Point("origin", 0.4, 0.6), Point("stay", 0.7, 0.8)])
    widget.set_arrows([
        Arrow("fwd", from_key="origin", to_cx_ratio=0.9, to_cy_ratio=0.1),
        Arrow("back", from_key="stay", to_key="origin"),
        Arrow("far", from_key="stay", to_cx_ratio=0.2, to_cy_ratio=0.2),
    ])
    return widget


def test_removing_a_region_takes_it_out_of_what_gets_saved(canvas) -> None:
    assert canvas.remove_item("region", "btn") is True

    assert [r.key for r in canvas.get_regions()] == ["keep"]


def test_removing_a_point_drops_the_arrows_that_end_on_it(canvas) -> None:
    """端点没了的 arrow 既画不出来也跑不了。"""
    assert canvas.remove_item("point", "origin") is True

    assert [p.key for p in canvas.get_points()] == ["stay"]
    assert [a.key for a in canvas.get_arrows()] == ["far"]


def test_removing_something_absent_reports_no_change(canvas) -> None:
    assert canvas.remove_item("region", "查无此项") is False
    assert canvas.remove_item("不存在的类型", "btn") is False


def test_items_hidden_by_the_view_filter_are_removed_too(canvas) -> None:
    """只删可见那份的话，切一次视图它又回来了。"""
    canvas.set_view_filter({"keep"})       # btn 被过滤进隐藏列表

    assert canvas.remove_item("region", "btn") is True

    canvas.set_view_filter(None)
    assert [r.key for r in canvas.get_regions()] == ["keep"]


def test_unbound_disabled_point_does_not_enter_canvas(canvas) -> None:
    canvas.set_points([])

    canvas.set_item_disabled("point", "optional", True)

    saved = canvas.get_points()
    assert [point.to_dict() for point in saved] == [
        {"key": "optional", "disabled": True},
    ]
    assert canvas._points == []
    assert canvas._hidden_points == []
    assert canvas.get_disabled_keys("point") == {"optional"}


def test_enabling_unbound_point_removes_disabled_placeholder(canvas) -> None:
    canvas.set_points([
        Point("optional", 0, 0, disabled=True, has_position=False),
    ])

    canvas.set_item_disabled("point", "optional", False)

    assert canvas.get_points() == []
    assert canvas.get_disabled_keys("point") == set()


def test_disabled_point_with_coordinates_remains_on_canvas(canvas) -> None:
    canvas.set_points([Point("placed", 0.4, 0.6, disabled=True)])

    canvas.set_item_disabled("point", "placed", False)
    canvas.set_item_disabled("point", "placed", True)

    assert [point.key for point in canvas._points] == ["placed"]
    assert canvas.get_points()[0].to_dict()["cx_ratio"] == pytest.approx(0.4)


def test_disabled_point_bound_at_zero_coordinate_is_not_a_placeholder(canvas) -> None:
    canvas.set_points([Point("corner", 0, 0, disabled=True)])

    canvas.set_item_disabled("point", "corner", False)

    assert [point.key for point in canvas._points] == ["corner"]
    assert canvas.get_points()[0].to_dict() == {
        "key": "corner",
        "cx_ratio": 0,
        "cy_ratio": 0,
        "r_ratio": 0.015,
    }


def test_unbound_disabled_region_does_not_enter_canvas(canvas) -> None:
    canvas.set_regions([])

    canvas.set_item_disabled("region", "optional", True)

    assert [region.to_dict() for region in canvas.get_regions()] == [
        {"key": "optional", "disabled": True},
    ]
    assert canvas._regions == []
    assert canvas._hidden_regions == []
    assert canvas.get_disabled_keys("region") == {"optional"}


def test_enabling_unbound_region_removes_disabled_placeholder(canvas) -> None:
    canvas.set_regions([
        Region(
            "optional", 0, 0, 0, 0,
            disabled=True, has_position=False),
    ])

    canvas.set_item_disabled("region", "optional", False)

    assert canvas.get_regions() == []
    assert canvas.get_disabled_keys("region") == set()


def test_disabled_region_with_coordinates_survives_toggle(canvas) -> None:
    canvas.set_regions([
        Region("placed", 0, 0.2, 0.3, 0.4, disabled=True),
    ])

    canvas.set_item_disabled("region", "placed", False)
    canvas.set_item_disabled("region", "placed", True)

    assert [region.key for region in canvas._regions] == ["placed"]
    saved = canvas.get_regions()[0].to_dict()
    assert saved["x_ratio"] == 0
    assert saved["y_ratio"] == pytest.approx(0.2)


def test_activation_only_region_is_saved_but_not_drawn(canvas) -> None:
    region = Region.from_dict({
        "key": "bag", "activation_key": "B", "disabled": True,
    })

    canvas.set_regions([region])

    assert canvas._regions == []
    assert canvas._hidden_regions == []
    assert [item.to_dict() for item in canvas.get_regions()] == [
        {"key": "bag", "activation_key": "B", "disabled": True},
    ]


def test_disabled_unbound_point_can_receive_activation_key(canvas) -> None:
    canvas.set_points([])
    canvas.set_item_disabled("point", "confirm", True)

    assert canvas.set_item_activation_key("point", "confirm", "SPACE") is True

    assert canvas._points == []
    assert [item.to_dict() for item in canvas.get_points()] == [{
        "key": "confirm", "activation_key": "SPACE", "disabled": True,
    }]


@pytest.mark.parametrize("kind", ["panel", "arrow", "subscene_ref"])
def test_other_unbound_disabled_items_stay_off_canvas(canvas, kind) -> None:
    canvas.set_panels([])
    canvas.set_arrows([])
    canvas.set_subscene_refs([])

    canvas.set_item_disabled(kind, "optional", True)

    getter = {
        "panel": canvas.get_panels,
        "arrow": canvas.get_arrows,
        "subscene_ref": canvas.get_subscene_refs,
    }[kind]
    assert [item.to_dict() for item in getter()] == [
        {"key": "optional", "disabled": True},
    ]
    drawable = {
        "panel": canvas._panels,
        "arrow": canvas._arrows,
        "subscene_ref": canvas._subscene_refs,
    }[kind]
    assert drawable == []

    canvas.set_item_disabled(kind, "optional", False)

    assert getter() == []
    assert canvas.get_disabled_keys(kind) == set()


@pytest.mark.parametrize("kind,item,setter,getter", [
    (
        "panel",
        Panel("placed", 0, 0.2, 0.3, 0.4, disabled=True),
        "set_panels",
        "get_panels",
    ),
    (
        "arrow",
        Arrow(
            "placed", from_key="origin",
            to_cx_ratio=0, to_cy_ratio=0, disabled=True,
        ),
        "set_arrows",
        "get_arrows",
    ),
    (
        "subscene_ref",
        SubsceneRef("placed", 0, 0.2, 0.3, 0.4, disabled=True),
        "set_subscene_refs",
        "get_subscene_refs",
    ),
])
def test_other_bound_disabled_items_survive_toggle(
    canvas, kind, item, setter, getter,
) -> None:
    getattr(canvas, setter)([item])

    canvas.set_item_disabled(kind, "placed", False)
    canvas.set_item_disabled(kind, "placed", True)

    saved = getattr(canvas, getter)()[0].to_dict()
    assert saved["disabled"] is True
    if kind != "arrow":
        assert saved["x_ratio"] == 0
