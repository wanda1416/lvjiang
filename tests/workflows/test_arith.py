"""算术运算符语法解析与引擎求值测试

原为 print 式手写脚本（pytest 收集 0 条用例，失败不报错），
改写为标准 pytest 断言以纳入回归基线。
"""

from unittest.mock import MagicMock

import pytest

from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.grammar import parse_text

# ─── 语法解析 ──────────────────────────────────────────────

class TestArithParse:
    @pytest.mark.parametrize("code", [
        'eval $x = 1 + 2\n',
        'eval $x = $a + $b * 2\n',
        'eval $x = (1 + 2) * 3\n',
        'eval $x = $a / 2\n',
        'if $a > $b + 1\n    log "yes"\nend\n',
        'eval $d.k = $a + 1\n',
        'eval $x = -5\n',
        'eval $x = -5 + 3\n',
        'eval $x = ($a + $b) * ($c - 1)\n',
        'eval $x = add(1, 2) + $a\n',
        'if $a + 1 == $b * 2\n    log "eq"\nend\n',
        '$x = $a + 1\n',   # 隐式 eval
    ])
    def test_parse_succeeds(self, code):
        prog = parse_text(code)
        assert prog.body


# ─── 引擎求值 ──────────────────────────────────────────────

def make_engine() -> WorkflowEngine:
    """创建一个最小化的引擎实例用于求值测试"""
    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1)
    return WorkflowEngine(
        capture=capture, ocr=MagicMock(), input_ctrl=MagicMock(),
        layout=layout, delay_config=MagicMock(),
    )


class TestArithEval:
    @pytest.mark.parametrize("code,variables,expected", [
        # 基础运算
        ("eval $x = 1 + 2", {}, 3.0),
        ("eval $x = 10 - 3", {}, 7.0),
        ("eval $x = 4 * 5", {}, 20.0),
        ("eval $x = 10 / 3", {}, 10 / 3),   # 浮点除
        ("eval $x = 10 / 0", {}, 0.0),      # 除 0 返回 0
        # 变量参与
        ("eval $x = $a + $b", {"a": 10, "b": 20}, 30.0),
        ("eval $x = $a * 2 + 1", {"a": 5}, 11.0),
        # 括号改优先级
        ("eval $x = (1 + 2) * 3", {}, 9.0),
        ("eval $x = ($a + $b) * ($c - 1)", {"a": 2, "b": 3, "c": 4}, 15.0),
        # 负数
        ("eval $x = -5 + 3", {}, -2.0),
        ("eval $x = $a - 10", {"a": 3}, -7.0),
        # 复杂嵌套
        ("eval $x = ($a + 1) * ($b - 2) / 2", {"a": 4, "b": 6}, 10.0),
    ])
    def test_eval_result(self, code, variables, expected):
        engine = make_engine()
        engine.variables = dict(variables)
        for stmt in parse_text(code).body:
            engine._exec_stmt(stmt)
        assert engine.variables.get("x") == expected

    @pytest.mark.parametrize("variables,expected", [
        ({"a": 10, "b": 5}, True),
        ({"a": 5, "b": 10}, False),
    ])
    def test_condition_with_arith(self, variables, expected):
        engine = make_engine()
        engine.variables = dict(variables)
        prog = parse_text('if $a > $b + 1\n    log "yes"\nend\n')
        assert engine._eval_condition(prog.body[0].condition) is expected

    @pytest.mark.parametrize("variables,expected", [
        ({"a": 9, "b": 5}, True),
        ({"a": 8, "b": 5}, False),
    ])
    def test_equality_both_sides_expr(self, variables, expected):
        engine = make_engine()
        engine.variables = dict(variables)
        prog = parse_text('if $a + 1 == $b * 2\n    log "eq"\nend\n')
        assert engine._eval_condition(prog.body[0].condition) is expected

    def test_float_equality_tolerance(self):
        """== 用容差比较，避免浮点误差：0.1+0.2 == 0.3 应为 true"""
        engine = make_engine()
        engine.variables = {}
        prog = parse_text('if 0.1 + 0.2 == 0.3\n    log "eq"\nend\n')
        assert engine._eval_condition(prog.body[0].condition) is True

    def test_float_inequality_tolerance(self):
        """!= 与 == 互补：0.1+0.2 != 0.3 应为 false，真差异仍为 true"""
        engine = make_engine()
        engine.variables = {}
        prog = parse_text('if 0.1 + 0.2 != 0.3\n    log "ne"\nend\n')
        assert engine._eval_condition(prog.body[0].condition) is False
        prog = parse_text('if 0.1 + 0.2 != 0.4\n    log "ne"\nend\n')
        assert engine._eval_condition(prog.body[0].condition) is True
