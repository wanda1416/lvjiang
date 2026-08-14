"""算术运算符语法解析与引擎求值测试

原为 print 式手写脚本（pytest 收集 0 条用例，失败不报错），
改写为标准 pytest 断言以纳入回归基线。
"""

import pytest

from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

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


class TestArithEval:
    @pytest.mark.parametrize("code,variables,expected", [
        # 基础运算（int+int→int）
        ("eval $x = 1 + 2", {}, 3),
        ("eval $x = 10 - 3", {}, 7),
        ("eval $x = 4 * 5", {}, 20),
        ("eval $x = 10 / 3", {}, 10 / 3),   # 浮点除
        ("eval $x = 10 / 0", {}, 0.0),      # 除 0 返回 0.0
        # 变量参与
        ("eval $x = $a + $b", {"a": 10, "b": 20}, 30),
        ("eval $x = $a * 2 + 1", {"a": 5}, 11),
        # 括号改优先级
        ("eval $x = (1 + 2) * 3", {}, 9),
        ("eval $x = ($a + $b) * ($c - 1)", {"a": 2, "b": 3, "c": 4}, 15),
        # 负数
        ("eval $x = -5 + 3", {}, -2),
        ("eval $x = $a - 10", {"a": 3}, -7),
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


# ─── 字符串拼接 ──────────────────────────────────────────────


class TestStringConcat:
    @pytest.mark.parametrize("code,variables,expected", [
        # 字面量拼接
        ('eval $x = "hello" + " world"', {}, "hello world"),
        # 变量 + 字面量
        ('eval $x = $name + "!"', {"name": "hi"}, "hi!"),
        # 字面量 + 变量
        ('eval $x = "prefix_" + $val', {"val": "test"}, "prefix_test"),
        # 变量 + 变量
        ('eval $x = $a + $b', {"a": "foo", "b": "bar"}, "foobar"),
        # 数字 + 字符串（数字转字符串）
        ('eval $x = $n + " items"', {"n": 3}, "3 items"),
        # 字符串 + 数字
        ('eval $x = "count: " + $n', {"n": 42}, "count: 42"),
        # null + 字符串（null 视为空串）
        ('eval $x = $null + "text"', {}, "text"),
        # 字符串 + null
        ('eval $x = "text" + $null', {}, "text"),
        # 链式拼接
        ('eval $x = "a" + "b" + "c"', {}, "abc"),
        # int+int→int
        ('eval $x = 1 + 2', {}, 3),
        # 任一侧为 str → 字符串拼接
        ('eval $x = $a + $b', {"a": "1.0", "b": "2.0"}, "1.02.0"),
        # str + int → 拼接
        ('eval $x = $a + 1', {"a": "5"}, "51"),
        # int + str → 拼接
        ('eval $x = 1 + $b', {"b": "5"}, "15"),
        # null + 数值字符串 → 拼接（null 视为空串，不应走算术）
        ('eval $x = $null + "5"', {}, "5"),
        ('eval $x = "5" + $null', {}, "5"),
    ])
    def test_string_concat(self, code, variables, expected):
        engine = make_engine()
        engine.variables = dict(variables)
        for stmt in parse_text(code).body:
            engine._exec_stmt(stmt)
        assert engine.variables.get("x") == expected
