"""画布上的实例删除：定义删了，画布也得删。

场景定义删掉之后画布若还留着，下一次保存布局会把 ``get_regions()`` 里的它
原样写回去——刚从磁盘删掉的坐标立刻复活，而且看不出来。
"""
from __future__ import annotations

import pytest

from lvjiang.core.layout_models import Arrow, Point, Region
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
