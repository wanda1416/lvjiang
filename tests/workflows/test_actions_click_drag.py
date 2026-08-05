"""click/drag 指令执行测试

覆盖 _exec_click / _exec_drag 的多种目标模式：
- CoordPoint 坐标点击
- SceneRef + region 点击
- CoordPoint 对拖拽
"""

from unittest.mock import MagicMock

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
