"""配置驱动的推杆必须按当前秒数执行；非法时长不能下发输入。"""
from unittest.mock import MagicMock

import pytest

from lvjiang.workflows.errors import WorkflowUserError
from lvjiang.workflows.grammar import VarRef, parse_text
from tests.workflows.conftest import make_engine


def test_drag_resolves_each_hold_without_mutating_ast(monkeypatch):
    engine = make_engine()
    node = parse_text('drag [general_move].[move_forward] duration 0.15 hold $seconds exact\n').body[0]
    dispatch = MagicMock()
    monkeypatch.setattr(engine, "_drag_entity", dispatch)
    for seconds in (8, 3):
        engine.variables["seconds"] = seconds
        engine._exec_drag(node)
        assert dispatch.call_args.args[0].hold == seconds
    assert isinstance(node.hold, VarRef)
    dispatch.reset_mock()
    engine.variables["seconds"] = -1
    with pytest.raises(WorkflowUserError):
        engine._exec_drag(node)
    dispatch.assert_not_called()


def test_timeline_drag_resolves_hold_before_dispatch(monkeypatch):
    from tests.workflows.test_input_timeline import TestCompiler

    host = TestCompiler._host()
    original = host._resolve
    monkeypatch.setattr(host, "_resolve", lambda value: 5 if isinstance(value, VarRef) else original(value))
    entry = parse_text('timeline\n @0 drag [general_move].[move_backward] hold $seconds\nend\n').body[0].entries[0]
    assert host._compile_timeline_entry(entry).hold == 5


def test_drag_and_press_share_range_and_variable_hold(monkeypatch):
    engine = make_engine()
    dispatch = MagicMock()
    monkeypatch.setattr(engine, "_drag_entity", dispatch)
    monkeypatch.setattr("lvjiang.workflows.engine.actions.random.uniform", lambda lo, hi: (lo + hi) / 2)
    engine.variables["seconds"] = (3, 5)
    for expression in ("(3, 5)", "$seconds"):
        drag = parse_text(f'drag [general_move].[move_forward] hold {expression}\n').body[0]
        press = parse_text(f'press "W" hold {expression}\n').body[0]
        engine._exec_drag(drag)
        assert dispatch.call_args.args[0].hold == engine._resolve_hold_duration(press.duration, "press") == 4
