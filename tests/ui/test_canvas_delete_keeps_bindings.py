"""从画布删除实体：只解除坐标绑定，不动与坐标无关的定义。

按键绑定（activation_key）不依赖矩形——有绑定的区域即使没坐标照样能 click（走按键
通道）。连带删掉的话，用户是在「挪掉一个框」，结果丢了按键绑定，而且要等脚本点不动
才发现。click_rect 与 template 相反：它们相对本区域定义，矩形没了就无从依附，随坐标
一起清。
"""

import pytest

from lvjiang.core.layout_models import Point, Region, TemplateBinding
from lvjiang.ui.scene_editor.canvas import RegionCanvas

pytestmark = pytest.mark.usefixtures('qapp')


def _canvas(qtbot) -> RegionCanvas:
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    return canvas


class TestRegion:
    def test_keeps_activation_key_and_drops_coordinates(self, qtbot):
        canvas = _canvas(qtbot)
        canvas.set_regions([
            Region("back", 0.1, 0.1, 0.2, 0.2, activation_key="ESC"),
        ])
        canvas._selected_idx = 0
        canvas.delete_selected()

        kept = canvas.get_regions()
        assert [r.key for r in kept] == ["back"]
        region = kept[0]
        assert region.activation_key == "ESC"
        assert not region.has_position
        # 落盘形态里不该再有坐标，但按键绑定要留着
        payload = region.to_dict()
        assert "x_ratio" not in payload
        assert payload["activation_key"] == "ESC"

    def test_drops_coordinate_relative_extras(self, qtbot):
        """click_rect 是区域的子集、template 在区域内搜索，矩形没了就无从依附。"""
        canvas = _canvas(qtbot)
        canvas.set_regions([
            Region("back", 0.1, 0.1, 0.2, 0.2, activation_key="ESC",
                   click_rect=(0.1, 0.1, 0.5, 0.5),
                   template=TemplateBinding("desktop/x/back", 0.8, 1920, 1080)),
        ])
        canvas._selected_idx = 0
        canvas.delete_selected()

        region = canvas.get_regions()[0]
        assert region.click_rect is None
        assert region.template is None

    def test_keeps_disabled_only_entry(self, qtbot):
        """停用标记也与坐标无关：静态检查据此认为该实体仍被声明过。"""
        canvas = _canvas(qtbot)
        canvas.set_regions([Region("x", 0.1, 0.1, 0.2, 0.2, disabled=True)])
        canvas._selected_idx = 0
        canvas.delete_selected()
        assert [r.key for r in canvas.get_regions()] == ["x"]

    def test_plain_region_is_removed_entirely(self, qtbot):
        """没有可保留内容时整条移除——只剩 key 的空条目没有意义。"""
        canvas = _canvas(qtbot)
        canvas.set_regions([Region("x", 0.1, 0.1, 0.2, 0.2)])
        canvas._selected_idx = 0
        canvas.delete_selected()
        assert canvas.get_regions() == []

    def test_reference_region_is_untouched(self, qtbot):
        canvas = _canvas(qtbot)
        canvas.set_regions([
            Region("shared", 0.1, 0.1, 0.2, 0.2, source_scene="other"),
        ])
        canvas._selected_idx = 0
        canvas.delete_selected()
        assert [r.key for r in canvas.get_regions()] == ["shared"]


class TestPoint:
    def test_keeps_activation_key(self, qtbot):
        canvas = _canvas(qtbot)
        canvas.set_points([Point("jump", 0.5, 0.5, activation_key="SPACE")])
        assert canvas.delete_point_by_key("jump")

        kept = canvas.get_points()
        assert [p.key for p in kept] == ["jump"]
        assert kept[0].activation_key == "SPACE"
        assert not kept[0].has_position
        assert "cx_ratio" not in kept[0].to_dict()

    def test_plain_point_is_removed_entirely(self, qtbot):
        canvas = _canvas(qtbot)
        canvas.set_points([Point("p", 0.5, 0.5)])
        assert canvas.delete_point_by_key("p")
        assert canvas.get_points() == []
