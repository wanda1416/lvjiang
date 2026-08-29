"""click/drag 指令执行测试

覆盖 _exec_click / _exec_drag 的多种目标模式：
- CoordPoint 坐标点击
- EntityRef + entity 点击
- CoordPoint 对拖拽
"""

from unittest.mock import MagicMock, call

from lvjiang.workflows.align import GridAlignment
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


class TestClickCoordPoint:
    def test_click_coord_executes(self):
        """click 坐标点正常执行"""
        code = "click (0.5, 0.5)\n"
        eng = make_engine()
        program = parse_text(code)
        eng._exec_body(program.body)
        assert eng._input.click_screen.called

    def test_click_default_button_is_left(self):
        """省略鼠标键时传给 click_screen 的 button 默认 left"""
        eng = make_engine()
        program = parse_text("click (0.5, 0.5)\n")
        eng._exec_body(program.body)
        _args, kwargs = eng._input.click_screen.call_args
        assert kwargs["button"] == "left"

    def test_click_explicit_button_reaches_backend(self):
        """click ... right 等显式鼠标键要透传到 click_screen 的 button 参数"""
        eng = make_engine()
        program = parse_text("click (0.5, 0.5) x1\n")
        eng._exec_body(program.body)
        _args, kwargs = eng._input.click_screen.call_args
        assert kwargs["button"] == "x1"


class TestRawMouseButton:
    def test_down_up_reach_backend_in_order(self):
        eng = make_engine()
        program = parse_text("mouse x1 down\nmouse x1 up\n")

        eng._exec_body(program.body)

        assert eng._input.mouse_button.call_args_list == [
            call("x1", True),
            call("x1", False),
        ]
        assert eng._pressed_mouse_buttons == set()

    def test_back_forward_aliases_are_normalized(self):
        eng = make_engine()
        program = parse_text("mouse back down\nmouse back up\n")

        eng._exec_body(program.body)

        assert [call.args[0] for call in eng._input.mouse_button.call_args_list] == [
            "x1", "x1",
        ]


class TestClickSceneRegion:
    def test_click_scene_region(self):
        """click [scene].[region] 语法执行"""
        eng = make_engine()
        # 设置 layout mock 返回 region
        region = MagicMock()
        region.key = "btn_ok"
        region.x_ratio = 0.25
        region.y_ratio = 0.25
        region.w_ratio = 0.5
        region.h_ratio = 0.5
        eng._layout.get_scene_regions.return_value = [region]

        code = "click [test_scene].[btn_ok]\n"
        program = parse_text(code)
        eng._exec_body(program.body)
        assert eng._input.click_screen.called


class TestDragCoordPoint:
    def test_drag_coord_pair_executes(self):
        """drag 坐标对拖拽正常执行"""
        code = "drag (0.5, 0.8) (0.5, 0.2)\n"
        eng = make_engine()
        program = parse_text(code)
        eng._exec_body(program.body)
        assert eng._input.drag_screen.called


class TestDragStructuredTargets:
    @staticmethod
    def _area(key: str, x=0.1, y=0.2, w=0.4, h=0.3):
        return MagicMock(
            key=key,
            x_ratio=x,
            y_ratio=y,
            w_ratio=w,
            h_ratio=h,
            disabled=False,
        )

    @staticmethod
    def _alignment():
        return GridAlignment(
            row_centers=[0.25, 0.75],
            col_centers=[0.25, 0.75],
            row_bounds=[0.0, 0.5, 1.0],
            col_bounds=[0.0, 0.5, 1.0],
            row_slot=0.2,
            row_span=0.04,
            col_slot=0.3,
            col_span=0.02,
        )

    def test_panel_grid_uses_alignment_and_invalidates_cache(self):
        eng = make_engine()
        panel = self._area("items")
        eng._layout.get_scene_panels.return_value = [panel]
        eng._panel_alignments[("bag", "items")] = self._alignment()

        eng._exec_body(parse_text("drag [bag].[items] up 2\n").body)

        args = eng._input.drag_screen.call_args.args
        assert args[:4] == (576, 378, 576, 236)
        assert ("bag", "items") not in eng._panel_alignments

    def test_region_grid_uses_declared_region_size(self):
        eng = make_engine()
        region = self._area("scroll_area")
        eng._layout.get_scene_panels.return_value = []
        eng._layout.get_scene_regions.return_value = [region]

        eng._exec_body(
            parse_text("drag [bag].[scroll_area] right 0.5\n").body
        )

        args = eng._input.drag_screen.call_args.args
        assert args[:4] == (576, 378, 960, 378)

    def test_entity_region_defaults_to_one_region_height_up(self):
        eng = make_engine()
        region = self._area("scroll_area")
        eng._layout.get_scene_arrows.return_value = []
        eng._layout.get_scene_regions.return_value = [region]

        eng._exec_body(parse_text("drag [bag].[scroll_area]\n").body)

        args = eng._input.drag_screen.call_args.args
        assert args[:4] == (576, 378, 576, 54)


class TestForLoopWithClick:
    """通过 DSL 集成测试循环内的点击"""

    def test_for_loop_click(self):
        """for 循环内点击正常执行"""
        eng = make_engine()
        region = MagicMock()
        region.key = "item"
        region.x_ratio = 0.5
        region.y_ratio = 0.5
        region.w_ratio = 0.1
        region.h_ratio = 0.1
        eng._layout.get_scene_regions.return_value = [region]

        code = '''for i in [1, 2, 3]
    click [test_scene].[item]
end
'''
        program = parse_text(code)
        eng._exec_body(program.body)
        # 循环 3 次，每次点击
        assert eng._input.click_screen.call_count == 3


class TestDragPanelRefAlignmentCache:
    """drag panel-ref 滚动后必须失效对齐缓存——变量写法也要生效。

    缓存按解析后的 (scene_key, panel_key) 存。曾经用未解析的 ref 去 pop，
    `drag $s.$p[1][2] down` 命中不了，滚动后缓存还在，后续格子坐标全部
    按滚动前的对齐算。
    """

    @staticmethod
    def _run(code: str, variables: dict) -> dict:
        from unittest.mock import MagicMock

        eng = make_engine()
        eng.variables = dict(variables)
        panel = MagicMock(x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
        eng._find_panel_in_layout = lambda s, p: panel
        eng._panel_ref_to_screen = lambda ref: (100, 100)
        eng._panel_alignments = {
            ("sc", "pn"): MagicMock(row_slot=10, col_slot=10,
                                    row_span=0, col_span=0),
        }
        eng._exec_body(parse_text(code).body)
        return eng._panel_alignments

    def test_literal_form_invalidates(self):
        assert self._run("drag [sc].[pn][1][2] down\n", {}) == {}

    def test_variable_form_also_invalidates(self):
        left = self._run("drag $s.$p[1][2] down\n", {"s": "sc", "p": "pn"})
        assert left == {}, f"变量写法未失效缓存，残留: {list(left)}"
