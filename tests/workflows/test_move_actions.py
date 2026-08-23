"""move/place 指令的坐标换算与输入调度测试。"""

from unittest.mock import MagicMock

from lvjiang.workflows.grammar import parse_text

from .conftest import make_engine


def _engine_for_canvas():
    capture = MagicMock()
    capture.get_capture_size.return_value = (1000, 500)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0.1,
        y_ratio=0.2,
        w_ratio=0.8,
        h_ratio=0.6,
    )
    input_ctrl = MagicMock()
    engine = make_engine(
        capture=capture,
        layout=layout,
        input_ctrl=input_ctrl,
        window_left=10,
        window_top=20,
    )
    return engine, input_ctrl


def test_move_to_with_start_places_then_moves():
    engine, input_ctrl = _engine_for_canvas()
    program = parse_text(
        "move (0.5, 0.5) to (0.75, 0.25) duration 0.4")

    engine._exec_body(program.body)

    input_ctrl.place_screen.assert_called_once_with(
        510, 270, "coord(0.5,0.5)")
    input_ctrl.move_screen.assert_called_once_with(
        710, 195, "coord(0.75,0.25)", duration=0.4)
    input_ctrl.move_relative.assert_not_called()


def test_move_by_scales_vector_by_canvas_size_without_origin():
    engine, input_ctrl = _engine_for_canvas()
    program = parse_text("move by (0.25, -0.5) duration 0.3")

    engine._exec_body(program.body)

    input_ctrl.move_relative.assert_called_once_with(
        200, -150, "canvas_delta(0.25,-0.5)", duration=0.3)
    input_ctrl.place_screen.assert_not_called()
    input_ctrl.move_screen.assert_not_called()


def test_move_duration_accepts_runtime_parameter():
    engine, input_ctrl = _engine_for_canvas()
    engine.variables["turn_time"] = "0.25"
    program = parse_text("move by (-0.1, 0) duration $turn_time")

    engine._exec_body(program.body)

    input_ctrl.move_relative.assert_called_once_with(
        -80, 0, "canvas_delta(-0.1,0.0)", duration=0.25)
