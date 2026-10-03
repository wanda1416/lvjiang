"""click/drag 指令执行测试

覆盖 _exec_click / _exec_drag 的多种目标模式：
- CoordPoint 坐标点击
- EntityRef + entity 点击
- CoordPoint 对拖拽
"""

from unittest.mock import MagicMock, call, patch

import pytest
from lark.exceptions import UnexpectedInput

from lvjiang.core.config import InputSimConfig
from lvjiang.core.layout_models import Region
from lvjiang.workflows.align import GridAlignment
from lvjiang.workflows.errors import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.grammar.ast_nodes import Scroll
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

    def test_click_hold_reaches_backend(self):
        eng = make_engine()
        eng._exec_body(parse_text("click (0.5, 0.5) hold 1.4\n").body)

        assert eng._input.click_screen.call_args.kwargs["hold"] == 1.4

    def test_click_hold_range_and_tuple_variable(self, monkeypatch):
        eng = make_engine()
        monkeypatch.setattr(
            "lvjiang.workflows.engine.actions.random.uniform",
            lambda lo, hi: (lo + hi) / 2,
        )
        eng.variables["hold_range"] = (1.2, 1.4)
        eng._exec_body(parse_text(
            "click (0.5, 0.5) hold (1.0, 1.2)\n"
            "click (0.5, 0.5) hold $hold_range\n"
        ).body)

        calls = eng._input.click_screen.call_args_list
        assert calls[0].kwargs["hold"] == pytest.approx(1.1)
        assert calls[1].kwargs["hold"] == pytest.approx(1.3)

    def test_click_hold_rejects_invalid_duration(self):
        eng = make_engine()
        eng.variables["hold_time"] = 0

        with pytest.raises(Exception, match="click hold 时长必须 > 0"):
            eng._exec_body(parse_text(
                "click (0.5, 0.5) hold $hold_time\n").body)

    def test_python_workflow_facade_uses_engine_panel_click_primitive(self):
        eng = make_engine()
        workflow = eng._ensure_workflow()
        with patch.object(eng, "click_panel", return_value=True) as click_panel:
            assert workflow.click_panel("bag", "items", 2, 3)
        click_panel.assert_called_once_with("bag", "items", 2, 3)


class TestPressMouseButton:
    def test_down_up_reach_backend_in_order(self):
        eng = make_engine()
        program = parse_text(
            'press "MOUSE_X1" down\npress "MOUSE_X1" up\n')

        eng._exec_body(program.body)

        assert eng._input.mouse_button.call_args_list == [
            call("x1", True),
            call("x1", False),
        ]
        assert not eng._key_registry.is_pressed("MOUSE_X1")

    def test_back_forward_aliases_are_normalized(self):
        eng = make_engine()
        program = parse_text(
            'press "MOUSE_BACK" down\npress "MOUSE_BACK" up\n')

        eng._exec_body(program.body)

        assert [call.args[0] for call in eng._input.mouse_button.call_args_list] == [
            "x1", "x1",
        ]

    def test_removed_mouse_instruction_is_rejected(self):
        with pytest.raises(UnexpectedInput):
            parse_text("mouse left down\n")


class TestClickSceneRegion:
    def test_click_scene_region(self):
        """click [scene].[region] 语法执行"""
        eng = make_engine()
        # 设置 layout mock 返回 region
        region = Region("btn_ok", 0.25, 0.25, 0.5, 0.5)
        eng._layout.get_scene_regions.return_value = [region]

        code = "click [test_scene].[btn_ok]\n"
        program = parse_text(code)
        eng._exec_body(program.body)
        assert eng._input.click_screen.called


class TestDragCoordPoint:
    def test_drag_coord_pair_executes(self):
        """drag 坐标对拖拽正常执行"""
        code = "drag (0.5, 0.8) to (0.5, 0.2)\n"
        eng = make_engine()
        program = parse_text(code)
        eng._exec_body(program.body)
        assert eng._input.drag_screen.called

    def test_drag_duration_forms_reach_backend(self):
        """duration 固定值 / 区间 / 变量（数值或二元 tuple）都透传到后端。"""
        eng = make_engine()
        eng.variables["t"] = 0.25
        eng.variables["r"] = (0.3, 0.8)
        eng._exec_body(parse_text(
            "drag (0.5, 0.8) to (0.5, 0.2) duration 0.4 hold 1.5\n"
            "drag (0.5, 0.8) to (0.5, 0.2) duration [0.3, 0.8]\n"
            "drag (0.5, 0.8) to (0.5, 0.2) duration $t\n"
            "drag (0.5, 0.8) to (0.5, 0.2) duration $r\n"
        ).body)

        calls = eng._input.drag_screen.call_args_list
        assert calls[0].kwargs["duration"] == 0.4 and calls[0].kwargs["hold"] == 1.5
        assert calls[1].kwargs["duration"] == (0.3, 0.8)
        assert calls[2].kwargs["duration"] == 0.25
        assert calls[3].kwargs["duration"] == (0.3, 0.8)

    def test_drag_exact_scale_extends_vector_from_start(self):
        """scale 沿起点→终点向量放大；exact 时两端都不抖动，坐标可精确断言。"""
        eng = make_engine()
        eng.variables["k"] = 0.5
        eng._exec_body(parse_text(
            "drag (0.2, 0.5) to (0.3, 0.5) scale 2 exact\n"
            "drag (0.2, 0.5) to (0.2, 0.3) scale $k exact\n"
        ).body)

        calls = eng._input.drag_screen.call_args_list
        assert calls[0].args[:4] == (384, 540, 768, 540)   # 0.2→0.4 × 1920
        assert calls[1].args[:4] == (384, 540, 384, 432)   # 0.5→0.4 × 1080

    def test_drag_jitters_both_ends_by_default(self, monkeypatch):
        """默认两端抖动：把随机固定到半径最远处，起点终点都应偏离圆心。"""
        eng = make_engine()
        monkeypatch.setattr(
            "lvjiang.workflows.base.coords.random.uniform", lambda lo, hi: hi)
        eng._exec_body(parse_text("drag (0.2, 0.5) to (0.4, 0.5)\n").body)

        x1, y1, x2, y2 = eng._input.drag_screen.call_args.args[:4]
        assert (x1, y1) != (384, 540) and (x2, y2) != (768, 540)
        # 偏移量 = 默认半径 0.015 × min(1920, 1080)，两端一致
        assert (x1 - 384, y1 - 540) == (x2 - 768, y2 - 540)

    def test_drag_scale_clamps_endpoint_to_canvas(self):
        """放大后越界按画布边缘截断，不报错：推到边缘就是推满。"""
        eng = make_engine()
        eng._exec_body(parse_text(
            "drag (0.2, 0.5) to (0.9, 0.5) scale 2 exact\n"     # x 1.6 → 1.0
            "drag (0.5, 0.3) to (0.5, 0.1) scale 3 exact\n"     # y -0.3 → 0.0
        ).body)

        calls = eng._input.drag_screen.call_args_list
        assert calls[0].args[:4] == (384, 540, 1920, 540)
        assert calls[1].args[:4] == (960, 324, 960, 0)

    def test_drag_scale_clamps_after_endpoint_jitter(self, monkeypatch):
        """scale 把圆心推到边缘后，默认随机抖动也不能让最终落点越界。"""
        eng = make_engine()
        monkeypatch.setattr(
            "lvjiang.workflows.base.coords.random.uniform", lambda lo, hi: hi)

        eng._exec_body(parse_text(
            "drag (0.2, 0.5) to (0.9, 0.5) scale 2\n"
        ).body)

        _x1, _y1, x2, y2 = eng._input.drag_screen.call_args.args[:4]
        assert (x2, y2) == (1920, 540)

    def test_drag_scale_variable_must_be_positive_number(self):
        eng = make_engine()
        eng.variables["k"] = 0
        with pytest.raises(Exception, match="drag scale 必须是 > 0"):
            eng._exec_body(parse_text(
                "drag (0.2, 0.5) to (0.4, 0.5) scale $k\n").body)

    def test_drag_scale_rejected_for_region_default_drag(self):
        eng = make_engine()
        eng._layout.get_scene_arrows.return_value = []
        with pytest.raises(Exception, match="不是 arrow"):
            eng._exec_body(parse_text("drag [s].[list] scale 2\n").body)

    def test_drag_duration_variable_must_be_numeric(self):
        eng = make_engine()
        eng.variables["t"] = "fast"
        with pytest.raises(Exception, match="drag duration \\$t"):
            eng._exec_body(parse_text(
                "drag (0.5, 0.8) to (0.5, 0.2) duration $t\n").body)


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

    def test_dsl_panel_grid_adapts_to_engine_primitive(self):
        eng = make_engine()
        with patch.object(eng, "drag_grid") as drag_grid:
            eng._exec_body(parse_text("drag [bag].[items] up 2\n").body)
        drag_grid.assert_called_once_with(
            "bag", "items", "up", distance=2.0, row=None, col=None,
            duration=None, hold=None,
        )

    def test_python_workflow_facade_uses_engine_drag_primitive(self):
        eng = make_engine()
        workflow = eng._ensure_workflow()
        with patch.object(eng, "drag_grid") as drag_grid:
            workflow.drag_grid("bag", "items", "up", distance=2, hold=0.3)
        drag_grid.assert_called_once_with(
            "bag", "items", "up", distance=2, hold=0.3,
        )

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
        region = Region("item", 0.5, 0.5, 0.1, 0.1)
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


class TestScrollInterval:
    """scroll 的 interval 参数必须透传到 scroll_screen（逐格固定间隔）。"""

    def test_interval_reaches_backend(self):
        eng = make_engine()
        eng._exec_body(parse_text("scroll down 3 interval 0.1\n").body)
        _args, kwargs = eng._input.scroll_screen.call_args
        assert kwargs["interval"] == 0.1

    def test_default_interval_is_none(self):
        """未写 interval 时传 None，后端回退到默认随机间隔。"""
        eng = make_engine()
        eng._exec_body(parse_text("scroll down 3\n").body)
        _args, kwargs = eng._input.scroll_screen.call_args
        assert kwargs["interval"] is None

    @pytest.mark.parametrize("bad", ["abc", -0.5])
    def test_invalid_interval_falls_back_to_default(self, bad):
        """非法 interval 记 error 后按默认随机间隔执行，不中断工作流。

        间隔只影响滚动节奏、不影响滚动是否发生，为它中止整条批量任务不划算。
        """
        eng = make_engine()
        eng._exec_scroll(Scroll(direction="down", amount=3, interval=bad))
        _args, kwargs = eng._input.scroll_screen.call_args
        assert kwargs["interval"] is None


def test_execute_injects_stop_check_into_input_backend(tmp_path):
    """桌面后端 hold 等待靠引擎注入的 stop_check 提前释放，每次执行都要挂上。"""
    stop = lambda: False  # noqa: E731
    eng = make_engine(stop_check=stop)
    wf = tmp_path / "noop.wf"
    wf.write_text("wait 0\n", encoding="utf-8")

    eng.execute(wf)

    assert eng._input.stop_check is stop


class TestDragGridAxisAnchor:
    """`[panel][row]` / `[panel][][col]`：只指定一维，起点落在那一行（列）的中心。

    面板中心起拖会浪费一半行程：向上滚时，中心以下那半个面板本来可以用来起拖。
    而指定到格子（`[row][col]`）又会让人以为两维都参与——上下滚时列对位移毫无
    影响。所以单维形态存在，并且方向与维度必须相配。
    """

    @staticmethod
    def _area(key: str):
        return MagicMock(key=key, x_ratio=0.1, y_ratio=0.2,
                         w_ratio=0.4, h_ratio=0.3, disabled=False)

    @staticmethod
    def _alignment():
        return GridAlignment(
            row_centers=[0.25, 0.75], col_centers=[0.125, 0.875],
            row_bounds=[0.0, 0.5, 1.0], col_bounds=[0.0, 0.5, 1.0],
            row_slot=0.2, row_span=0.04, col_slot=0.3, col_span=0.02,
        )

    def _engine(self):
        eng = make_engine()
        eng._layout.get_scene_panels.return_value = [self._area("list")]
        eng._panel_alignments[("bag", "list")] = self._alignment()
        # _panel_ratio_to_screen 会按 click_random_offset 做内缩钳位；
        # conftest 给的 input_sim 是 MagicMock，拿它参与算术会直接 TypeError
        eng._input_sim = InputSimConfig()
        return eng

    def _start(self, source: str):
        eng = self._engine()
        eng._exec_body(parse_text(source).body)
        return eng._input.drag_screen.call_args.args[:4]

    def test_row_anchor_moves_the_start_down_without_shifting_x(self):
        centre = self._start("drag [bag].[list] up 1\n")
        row2 = self._start("drag [bag].[list][2] up 1\n")

        assert row2[0] == centre[0], "上下滚时列不参与，横向应与面板中心一致"
        assert row2[1] > centre[1], "第 2 行中心应低于面板中心，换来更多向上行程"
        # 位移量不受起点影响：两种写法的 dy 必须相同
        assert row2[3] - row2[1] == centre[3] - centre[1]

    def test_col_anchor_moves_the_start_right_without_shifting_y(self):
        centre = self._start("drag [bag].[list] right 1\n")
        col2 = self._start("drag [bag].[list][][2] right 1\n")

        assert col2[1] == centre[1], "左右滚时行不参与，纵向应与面板中心一致"
        assert col2[0] > centre[0]
        assert col2[2] - col2[0] == centre[2] - centre[0]

    def test_row_only_rejects_horizontal_directions(self):
        """left/right 是沿着列走的，指定行对它没有影响——写错要当场报出来。"""
        for direction in ("left", "right"):
            with pytest.raises(Exception) as caught:
                parse_text(f"drag [bag].[list][2] {direction} 1\n")
            assert "up / down" in str(caught.value)

    def test_col_only_rejects_vertical_directions(self):
        for direction in ("up", "down"):
            with pytest.raises(Exception) as caught:
                parse_text(f"drag [bag].[list][][2] {direction} 1\n")
            assert "left / right" in str(caught.value)

    def test_cell_form_still_accepts_any_direction(self):
        """两维都给的 cell 形态不受这条约束——它本来就是显式指定一个格子。"""
        node = parse_text("drag [bag].[list][2][2] left 1\n").body[0]

        assert node.scene.row == 2 and node.scene.col == 2

    def test_out_of_range_index_skips_instead_of_dragging(self):
        """行列数来自运行期对齐，越界是运行时状态：记日志跳过，但绝不能用
        面板中心悄悄替代——那会滚一个用户没要求的距离。"""
        eng = self._engine()

        eng._exec_body(parse_text("drag [bag].[list][5] up 1\n").body)

        assert not eng._input.drag_screen.called

    def test_region_rejects_an_axis_index(self):
        """region 没有网格，行列号无从解释——静默忽略会让脚本以为起点挪过去了。"""
        eng = make_engine()
        eng._layout.get_scene_panels.return_value = []
        eng._layout.get_scene_regions.return_value = [
            Region("scroll_area", 0.1, 0.2, 0.4, 0.3)]

        with pytest.raises(WorkflowUserError, match="region"):
            eng._exec_body(
                parse_text("drag [bag].[scroll_area][2] up 1\n").body)


class TestOutOfBoundsGuard:
    """引擎层主动拦截越界坐标：默认报错结束，开关打开才自动截断。

    判定必须在引擎层：同一个越界坐标，设备端无障碍手势是硬拒绝（一句
    `Path bounds must not be negative`，看不出是 wf 的哪一行），而桌面
    SendInput / PostMessage 与 adb shell input 会静默接受并自行截断——于是
    PC 上一直在悄悄少走位移，没人发现。

    默认报错而不是自动截断：越界说明脚本的位移参数与当前面板几何不匹配，
    截断会把「滚两行」悄悄变成「滚一行」，脚本和日志都看不出来。起点怎么挪
    是用户的决定（`[行]` / `[][列]` 形态），引擎不替他改。
    """

    @staticmethod
    def _engine(*, clamp: bool):
        eng = make_engine()
        eng._input_sim = InputSimConfig(clamp_out_of_bounds=clamp)
        eng._capture.get_capture_size.return_value = (1920, 1080)
        return eng

    def test_out_of_bounds_drag_fails_by_default(self):
        eng = self._engine(clamp=False)

        with pytest.raises(WorkflowUserError) as caught:
            eng._send_drag(900, 300, 900, -220, "grid(bag.list) up 2")

        message = str(caught.value)
        assert "(900, -220)" in message, "必须报出到底哪个坐标越界"
        assert "1920×1080" in message, "必须报出画面尺寸，否则无从判断差多少"
        assert "越界坐标自动截断" in message, "要指路到那个开关"
        assert not eng._input.drag_screen.called, "报错后不许再下发"

    def test_enabling_the_switch_clamps_and_keeps_running(self):
        eng = self._engine(clamp=True)

        eng._send_drag(900, 300, 900, -220, "grid(bag.list) up 2")

        assert eng._input.drag_screen.call_args.args[:4] == (900, 300, 900, 0)

    def test_in_bounds_coordinates_pass_through_untouched(self):
        """没越界时不许改动坐标——抖动与钳位都已经在别处做过了。"""
        eng = self._engine(clamp=False)

        eng._send_drag(900, 300, 900, 120, "grid(bag.list) up 1")
        eng._send_click(10, 1079, "edge")

        assert eng._input.drag_screen.call_args.args[:4] == (900, 300, 900, 120)
        assert eng._input.click_screen.call_args.args[:2] == (10, 1079)

    def test_start_point_is_checked_too(self):
        """起点越界等于按在画面外，和终点一样要拦。"""
        eng = self._engine(clamp=False)

        with pytest.raises(WorkflowUserError, match="起点"):
            eng._send_drag(-5, 300, 900, 300, "grid(bag.list) left 1")

    def test_click_and_move_go_through_the_same_guard(self):
        eng = self._engine(clamp=False)

        with pytest.raises(WorkflowUserError, match="click"):
            eng._send_click(1920, 500, "region(bag.slot)")
        with pytest.raises(WorkflowUserError, match="move"):
            eng._send_move(500, 1080, "point(bag.anchor)")

    def test_desktop_window_offset_uses_absolute_capture_bounds(self):
        """桌面后端收到的是绝对屏幕坐标，窗口偏移不能被当成内容越界。"""
        eng = self._engine(clamp=False)
        eng._window_left = 66
        eng._window_top = 145

        eng._send_click(66 + 1919, 145 + 1079, "bottom-right edge")
        eng._send_drag(
            66 + 1500, 145 + 900,
            66 + 1500, 145 + 300,
            "desktop menu scroll",
        )

        assert eng._input.click_screen.call_args.args[:2] == (1985, 1224)
        assert eng._input.drag_screen.call_args.args[:4] == (
            1566, 1045, 1566, 445,
        )

    def test_desktop_window_offset_still_rejects_true_local_overflow(self):
        eng = self._engine(clamp=False)
        eng._window_left = 66
        eng._window_top = 145

        with pytest.raises(WorkflowUserError) as caught:
            eng._send_click(66 + 100, 145 + 1080, "below client")

        message = str(caught.value)
        assert "(166, 1225)" in message
        assert "屏幕区域 (66, 145)-(1985, 1224)" in message

    def test_desktop_window_offset_clamps_to_absolute_capture_bounds(self):
        eng = self._engine(clamp=True)
        eng._window_left = 66
        eng._window_top = 145

        eng._send_drag(60, 140, 2000, 1300, "desktop overflow")

        assert eng._input.drag_screen.call_args.args[:4] == (
            66, 145, 1985, 1224,
        )
