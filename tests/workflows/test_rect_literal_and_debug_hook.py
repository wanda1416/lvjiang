"""矩形字面量 (x, y, w, h) + 引擎调试钩子 / 单步模式

1. `$r = (x, y, w, h)` 求值为 RectCoordRef；可 click、可喂图色函数；2 元组语义不变
2. statement_hook 每条语句前回调 (行号, 变量快照)，快照是拷贝
3. step_mode：引擎在每条语句前 clear pause_event 并等待；UI set 一次走一条；停止能唤醒
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.core.coord_types import RectCoordRef
from lvjiang.workflows.builtins import get_function
from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine, run

# ─── 矩形字面量 ─────────────────────────────────────────

class TestRectLiteral:
    def test_parses_and_evaluates_to_rect(self):
        v = run("$r = (0.1, 0.2, 0.3, 0.4)\n")
        r = v["r"]
        assert isinstance(r, RectCoordRef)
        assert (r.cx, r.cy, r.w, r.h) == (pytest.approx(0.25), pytest.approx(0.4), 0.3, 0.4)

    def test_variables_allowed_in_elements(self):
        v = run("$w = 0.5\n$r = (0, 0, $w, 0.2)\n")
        assert v["r"].w == 0.5 and v["r"].cx == 0.25

    def test_two_tuple_unchanged(self):
        assert run("$t = (1, 2)\n")["t"] == (1.0, 2.0)

    def test_click_rect_var(self):
        eng = make_engine()
        eng._input_sim.region_jitter_ratio = 0.0
        prog = parse_text("$r = (0.5, 0.5, 0.2, 0.2)\nclick $r\n")
        eng._procs = dict(prog.procs)
        eng._exec_body(prog.body)
        eng._input.click_screen.assert_called_once()
        x, y = eng._input.click_screen.call_args.args[:2]
        # 中心 (0.6, 0.6) × 1920×1080
        assert abs(x - 1152) <= 1 and abs(y - 648) <= 1

    def test_vision_accepts_rect_and_point_tuples(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        frame[:, 100:] = (113, 204, 46)   # BGR of #2ecc71 on right half
        eng = make_engine()
        eng._capture.capture.return_value = frame
        eng._layout.get_canvas.return_value = MagicMock(x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
        ratio = get_function("color_ratio")(eng, (0.5, 0.0, 0.5, 1.0), "#2ecc71", 10)
        assert ratio == pytest.approx(1.0)
        assert get_function("pixel")(eng, (0.75, 0.5)) == [0x2E, 0xCC, 0x71]
        assert get_function("bright")(eng, (0.25, 0.5)) == 0


# ─── statement_hook ─────────────────────────────────────

class TestStatementHook:
    def test_hook_receives_line_and_snapshot(self):
        eng = make_engine()
        seen: list[tuple[int, dict]] = []
        eng.statement_hook = lambda line, vars_: seen.append((line, vars_))
        prog = parse_text('$a = 1\n$b = 2\nlog "x"\n')
        eng._procs = dict(prog.procs)
        eng._exec_body(prog.body)
        assert [ln for ln, _ in seen] == [1, 2, 3]
        assert seen[0][1] == {} and seen[1][1] == {"a": 1} and seen[2][1] == {"a": 1, "b": 2}
        # 快照是拷贝：引擎后续改动不反映到旧快照
        assert seen[1][1] is not eng.variables

    def test_hook_exception_does_not_break_script(self):
        eng = make_engine()

        def boom(_line, _vars):
            raise RuntimeError("panel broke")

        eng.statement_hook = boom
        prog = parse_text("$a = 1\n$b = $a + 1\n")
        eng._procs = dict(prog.procs)
        eng._exec_body(prog.body)
        assert eng.variables["b"] == 2

    def test_hook_sees_statements_inside_blocks(self):
        eng = make_engine()
        lines: list[int] = []
        eng.statement_hook = lambda line, _v: lines.append(line)
        prog = parse_text("$n = 0\nloop 2\n    $n = $n + 1\nend\n")
        eng._procs = dict(prog.procs)
        eng._exec_body(prog.body)
        # 块语句（loop）本身也回调一次，再加循环体两次；loop 头节点的 line_no 由
        # 文法取自首个子 token（落在第 3 行），是既有行为，这里只验调用次数与体内行
        assert len(lines) == 4 and lines[0] == 1 and lines[2:] == [3, 3]


# ─── step_mode ───────────────────────────────────────────

def _wait_until(pred, timeout=3.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


class TestStepMode:
    def _engine(self, code: str):
        pause = threading.Event()
        pause.set()
        stopped = {"flag": False}
        eng = make_engine(stop_check=lambda: stopped["flag"], pause_event=pause)
        prog = parse_text(code)
        eng._procs = dict(prog.procs)
        lines: list[int] = []
        eng.statement_hook = lambda line, _v: lines.append(line)
        eng.step_mode = True
        return eng, prog, pause, stopped, lines

    def test_blocks_before_each_statement_until_released(self):
        eng, prog, pause, _stopped, lines = self._engine("$a = 1\n$b = 2\n$c = 3\n")
        t = threading.Thread(target=eng._exec_body, args=(prog.body,), daemon=True)
        t.start()
        assert _wait_until(lambda: lines == [1])
        time.sleep(0.1)
        assert "a" not in eng.variables          # 停在第 1 条之前
        pause.set()                               # 单步
        assert _wait_until(lambda: lines == [1, 2])
        assert eng.variables.get("a") == 1 and "b" not in eng.variables
        eng.step_mode = False                     # 继续
        pause.set()
        t.join(3)
        assert not t.is_alive() and eng.variables["c"] == 3

    def test_release_from_statement_hook_is_not_lost(self):
        """UI 收到 stepped 后立即放行，也不能被引擎随后 clear 掉。"""
        pause = threading.Event()
        pause.set()
        eng = make_engine(stop_check=lambda: False, pause_event=pause)
        prog = parse_text("$a = 1\n")
        eng._procs = dict(prog.procs)
        eng.step_mode = True
        eng.statement_hook = lambda _line, _vars: pause.set()

        worker = threading.Thread(
            target=eng._exec_body, args=(prog.body,), daemon=True)
        worker.start()
        worker.join(1)

        assert not worker.is_alive()
        assert eng.variables["a"] == 1

    def test_stop_wakes_blocked_engine(self):
        eng, prog, pause, stopped, lines = self._engine("$a = 1\n$b = 2\n")
        worker = threading.Thread(target=lambda: self._run_swallow(eng, prog), daemon=True)
        worker.start()
        assert _wait_until(lambda: lines == [1])
        stopped["flag"] = True
        pause.set()
        worker.join(3)
        assert not worker.is_alive()
        assert "a" not in eng.variables

    @staticmethod
    def _run_swallow(eng: WorkflowEngine, prog):
        from lvjiang.workflows.engine.signals import _BreakSignal
        try:
            eng._exec_body(prog.body)
        except _BreakSignal:
            pass
