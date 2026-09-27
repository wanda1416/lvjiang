"""null/bool 字面量与 null 语义统一测试

归档自 P2 开发期冒烟测试（scripts/_phase5_smoke.py）。
"""
from lvjiang.workflows.grammar import parse_text
from tests.case_matrix import case_matrix
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

    @case_matrix("literal,empty", [
        # 没有值
        ("null", True),
        # 有字符串但没内容
        ('""', True),
        ('"   "', True),
        ('"abc"', False),
        # 字符串 "0" 有内容，和数字 0 是两回事
        ('"0"', False),
        # 空集合没有内容
        ("[]", True),
        ("{}", True),
        ('["a"]', False),
        ('{"a": 1}', False),
        # ⚠️ 数字与布尔永远不为空：0 / false 是「有值且为零/假」，
        # 不是「没读到东西」。判零用 == 0，判无值用 == null。
        # 实现曾是 not left，把它们一并算空只是 Python 真值性的副产品。
        # 如果这几行开始失败，说明 is_empty 又退回真值性了，不要改断言。
        ("0", False),
        ("0.0", False),
        ("1", False),
        ("false", False),
        ("true", False),
    ])
    def test_is_empty_asks_about_content_not_zero(self, literal, empty):
        """is_empty 只问「有没有内容」。三种「空」各有专属问法，互不重叠：

        没有值 → ``== null``；有串没内容 → ``is_empty``；值为零 → ``== 0``。
        """
        v = run(IF_ELSE_TPL % (f'eval $x = {literal}', '$x is_empty'))
        assert v["r"] == (1.0 if empty else 0.0)

    def test_is_empty_and_null_check_are_not_interchangeable(self):
        """收紧后 is_empty 与 == null 在 0 上分道扬镳，这正是收紧的目的。"""
        assert run(IF_ELSE_TPL % ('eval $x = 0', '$x is_empty'))["r"] == 0.0
        assert run(IF_ELSE_TPL % ('eval $x = 0', '$x == null'))["r"] == 0.0
        assert run(IF_ELSE_TPL % ('eval $x = null', '$x is_empty'))["r"] == 1.0
        assert run(IF_ELSE_TPL % ('eval $x = null', '$x == null'))["r"] == 1.0

    def test_undefined_var_falsy(self):
        v = run(IF_ELSE_TPL % ('', '$undefined_var'))
        assert v["r"] == 0.0

    def test_null_equals_null(self):
        """两个 null 在字符串上下文中都是 ""，equals 比较为 true"""
        v = run(IF_ELSE_TPL % ('eval $x = null\neval $y = null', '$x equals $y'))
        assert v["r"] == 1.0

    def test_in_and_contains_only_reject_null(self):
        """0 / false / 空串是合法值，不能被当成「不匹配」。

        守卫曾写成 ``if left else False``，于是 ``$zero in [0, 1]`` 为假——
        与 ``!=`` 恒真同一类错误：把「假值」当成「比不上」。
        """
        for expr, variables, expected in (
            ("$zero in [0, 1]", {"zero": 0}, 1.0),
            ("$flag in [false]", {"flag": False}, 1.0),
            ("$empty in [\"\"]", {"empty": ""}, 1.0),
            ("$one in [0, 1]", {"one": 1}, 1.0),
            ("$two in [0, 1]", {"two": 2}, 0.0),
            ("$none in [0, 1]", {"none": None}, 0.0),
            ("$zero contains \"0\"", {"zero": 0}, 1.0),
            ("$none contains \"x\"", {"none": None}, 0.0),
        ):
            v = run(IF_ELSE_TPL % ('', expr), variables)
            assert v["r"] == expected, expr

    def test_field_access_truthy(self):
        """if $dict.field → 存在且非空为 True"""
        v = run(IF_ELSE_TPL % ('', '$d.a'), {"d": {"a": "hello"}})
        assert v["r"] == 1.0

    def test_field_access_falsy_missing_key(self):
        """if $dict.missing → key 不存在为 False"""
        v = run(IF_ELSE_TPL % ('', '$d.missing'), {"d": {"a": 1}})
        assert v["r"] == 0.0

    def test_field_access_falsy_empty_string(self):
        """if $dict.field → 值为空字符串为 False"""
        v = run(IF_ELSE_TPL % ('', '$d.a'), {"d": {"a": ""}})
        assert v["r"] == 0.0

    def test_field_access_bracket_truthy(self):
        """if $dict.[key] → 数字索引 truthy 检查"""
        v = run(IF_ELSE_TPL % ('', '$d.[1].[1]'), {"d": {"1": {"1": "宋元通宝"}}})
        assert v["r"] == 1.0

    def test_field_access_bracket_falsy_empty(self):
        """if $dict.[key] → 空 dict 为 False"""
        v = run(IF_ELSE_TPL % ('', '$d.[1].[5]'), {"d": {"1": {}}})
        assert v["r"] == 0.0


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
