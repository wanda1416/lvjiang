"""视觉状态 subcall 的 DSL 端到端测试。"""

from pathlib import Path

import numpy as np
import pytest

from lvjiang.workflows.grammar import parse_text

from .conftest import make_engine

SUBCALL = Path("config/system/workflows/subcall/visual_state.wf")


def _frame(rgb: tuple[int, int, int]) -> np.ndarray:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:] = rgb[2], rgb[1], rgb[0]
    return image


def _run(frame: np.ndarray, call: str) -> tuple[str, int]:
    source = SUBCALL.read_text(encoding="utf-8") + f"\n{call}\n"
    program = parse_text(source)
    engine = make_engine()
    engine._capture.capture.return_value = frame
    engine._procs = dict(program.procs)
    engine._exec_body(program.body)
    return engine.variables["state"], engine._capture.capture.call_count


@pytest.mark.parametrize(("frame", "expected"), [
    (_frame((220, 190, 80)), "completed"),
    (_frame((230, 230, 230)), "incomplete"),
])
def test_jianghu_card_state_uses_gold_coverage(frame, expected):
    state, captures = _run(
        frame,
        "call $state = detect_jianghu_card_state((0, 0, 1, 1))",
    )
    assert state == expected
    assert captures == 1


def test_jianghu_card_state_preserves_ambiguous_band():
    frame = _frame((230, 230, 230))
    frame[:, :20] = (80, 190, 220)  # BGR，对应暖黄色 RGB(220, 190, 80)
    state, _captures = _run(
        frame,
        "call $state = detect_jianghu_card_state((0, 0, 1, 1))",
    )
    assert state == "unknown"


@pytest.mark.parametrize(("frame", "expected"), [
    (_frame((90, 40, 30)), "locked"),
    (_frame((80, 80, 80)), "unlock"),
    (_frame((0, 0, 0)), "unknown"),
])
def test_equipment_lock_state_matches_existing_three_states(frame, expected):
    state, captures = _run(
        frame,
        "call $state = detect_equipment_lock_state((0, 0, 1, 1))",
    )
    assert state == expected
    assert captures == 1


def test_equipment_lock_state_keeps_middle_ratio_unknown():
    frame = _frame((80, 80, 80))
    frame[:, :5] = (30, 40, 90)  # BGR，对应红色主导 RGB(90, 40, 30)
    state, _captures = _run(
        frame,
        "call $state = detect_equipment_lock_state((0, 0, 1, 1))",
    )
    assert state == "unknown"
