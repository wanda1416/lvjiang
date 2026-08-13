"""null/bool 字面量与 null 语义统一测试

归档自 P2 开发期冒烟测试（scripts/_phase5_smoke.py）。
"""
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine, run

IF_ELSE_TPL = '''%s
if %s
    eval $r = 1
else
    eval $r = 0
end
'''


# ─── 字面量 ───────────────────────────────────────────────

class TestLiterals:
    def test_null_assign(self):
        assert run('eval $x = null')["x"] is None

    def test_bool_assign(self):
        assert run('eval $flag = true')["flag"] is True
        assert run('eval $flag = false')["flag"] is False

    def test_implicit_eval(self):
        assert run('$x = null')["x"] is None
        assert run('$flag = true')["flag"] is True

    def test_list_with_null_bool(self):
        v = run('eval $l = [null, true, false, 1, "hello"]')
        assert v["l"] == [None, True, False, 1.0, "hello"]

    def test_dict_with_null_bool(self):
        v = run('eval $d = {"a": null, "b": true, "c": false}')
        assert v["d"]["a"] is None
        assert v["d"]["b"] is True
        assert v["d"]["c"] is False


# ─── 条件语义 ─────────────────────────────────────────────

class TestConditions:
    def test_null_is_falsy(self):
        v = run(IF_ELSE_TPL % ('eval $x = null', '$x'))
        assert v["r"] == 0.0

    def test_bool_condition(self):
        assert run(IF_ELSE_TPL % ('eval $flag = true', '$flag'))["r"] == 1.0
        assert run(IF_ELSE_TPL % ('eval $flag = false', '$flag'))["r"] == 0.0

    def test_null_is_empty(self):
        v = run(IF_ELSE_TPL % ('eval $x = null', '$x is_empty'))
        assert v["r"] == 1.0

    def test_undefined_var_falsy(self):
        v = run(IF_ELSE_TPL % ('', '$undefined_var'))
        assert v["r"] == 0.0

    def test_null_equals_null(self):
        """两个 null 在字符串上下文中都是 ""，equals 比较为 true"""
        v = run(IF_ELSE_TPL % ('eval $x = null\neval $y = null', '$x equals $y'))
        assert v["r"] == 1.0


# ─── null 语义统一 ────────────────────────────────────────

class TestNullSemantics:
    def test_undefined_var_returns_null(self):
        assert run('eval $x = $undefined_var')["x"] is None

    def test_missing_dict_key_returns_null(self):
        v = run('eval $val = $d.missing_key', {"d": {"a": 1}})
        assert v["val"] is None

    def test_null_in_arithmetic(self):
        """null 在算术中视为 0.0"""
        v = run('eval $x = null\neval $y = $x + 5\n')
        assert v["y"] == 5.0

    def test_null_in_concat(self):
        """null 在字符串上下文中视为空字符串"""
        v = run('eval $x = null\neval $s = concat("before", $x, "after")\n')
        assert v["s"] == "beforeafter"
        v = run('eval $x = null\neval $s = concat("[", $x, "]")\n')
        assert v["s"] == "[]"

    def test_collect_null(self):
        eng = make_engine()
        eng.variables = {"x": None}
        eng._exec_body(parse_text('collect $x').body)
        assert "x" in eng.output
        assert eng.output["x"] is None
