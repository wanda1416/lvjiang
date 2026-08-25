"""click/drag 指令执行测试

覆盖 _exec_click / _exec_drag 的多种目标模式：
- CoordPoint 坐标点击
- EntityRef + entity 点击
- CoordPoint 对拖拽
"""

from unittest.mock import MagicMock, call

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
