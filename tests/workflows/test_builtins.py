"""DSL 内置函数测试（_registry / arithmetic / general）

ee855a7 内置函数模块化拆分后无直测，本文件补充回归保护。
system 模块（confirm/pause/save/panel_rows）依赖 engine 与 GUI，不纳入。
"""

import pytest

from lvjiang.workflows.builtins import builtin_func, get_function, list_functions


def _fn(name):
    fn = get_function(name)
    assert fn is not None, f"内置函数 {name} 未注册"
    return fn


# ─── 注册表 ────────────────────────────────────────────────

class TestRegistry:
    def test_all_general_functions_registered(self):
        registered = set(list_functions())
        expected = {
            "add", "sub", "mul", "div", "mod", "min", "max", "abs",
            "concat", "range", "count_nonempty", "contains", "find_key", "append",
        }
        assert expected <= registered

    def test_get_function_unknown_returns_none(self):
        assert get_function("no_such_builtin") is None

    def test_builtin_func_decorator_registers(self):
        @builtin_func("_test_only_fn")
        def _impl(*args):
            return "ok"

        assert get_function("_test_only_fn") is _impl


# ─── arithmetic ────────────────────────────────────────────

class TestArithmetic:
    @pytest.mark.parametrize("name,a,b,expected", [
        ("add", 2, 3, 5),
        ("add", 3, 0.5, 3.5),
        ("sub", 10, 4, 6),
        ("mul", 3, 4, 12),
        ("mul", 3, 0.5, 1.5),
        ("div", 7, 2, 3.5),   # 浮点除
        ("mod", 7, 3, 1),
        ("min", 5, 9, 5),
        ("max", 5, 9, 9),
    ])
    def test_binary_ops(self, name, a, b, expected):
        assert _fn(name)(a, b) == expected

    def test_min_max_variadic(self):
        assert _fn("min")(5, 9, 2, 7) == 2
        assert _fn("max")(5, 9, 2, 7) == 9

    def test_string_numbers_coerced(self):
        # DSL 变量常以字符串形态传入
        assert _fn("add")("2", "3") == 5
        assert _fn("add")("2.5", "0.5") == 3.0

    @pytest.mark.parametrize("name", ["div", "mod"])
    def test_divide_by_zero_returns_zero(self, name):
        assert _fn(name)(7, 0) == 0

    @pytest.mark.parametrize("name", [
        "add", "sub", "mul", "div", "mod", "min", "max",
    ])
    def test_invalid_input_returns_zero(self, name):
        assert _fn(name)("abc", 1) == 0
        assert _fn(name)(None, 1) == 0

    def test_abs(self):
        assert _fn("abs")(-5) == 5
        assert _fn("abs")("3") == 3
        assert _fn("abs")("abc") == 0


# ─── general ───────────────────────────────────────────────

class TestConcat:
    def test_mixed_args(self):
        assert _fn("concat")("结果: ", 3, " 完成") == "结果: 3 完成"

    def test_empty(self):
        assert _fn("concat")() == ""


class TestRange:
    def test_single_arg_starts_from_one(self):
        assert _fn("range")(3) == [1, 2, 3]

    def test_two_args_closed_interval(self):
        assert _fn("range")(2, 5) == [2, 3, 4, 5]

    def test_too_many_args_raises(self):
        with pytest.raises(ValueError):
            _fn("range")(1, 2, 3)


class TestCountNonempty:
    def test_dict_counts_non_empty_values(self):
        assert _fn("count_nonempty")({"a": "x", "b": "", "c": "  ", "d": "y"}) == 2

    def test_list_counts_elements(self):
        assert _fn("count_nonempty")([1, 2, 3]) == 3

    def test_other_types_return_zero(self):
        assert _fn("count_nonempty")("text") == 0
        assert _fn("count_nonempty")(None) == 0


class TestContains:
    def test_hit_and_miss(self):
        result = {"f1": "开始调律", "f2": "取消"}
        assert _fn("contains")(result, "调律") is True
        assert _fn("contains")(result, "不存在") is False

    def test_non_dict_or_no_args(self):
        assert _fn("contains")("text", "t") is False
        assert _fn("contains")({"a": "b"}) is False


class TestFindKey:
    def test_returns_first_matching_key(self):
        result = {"f1": "取消", "f2": "开始调律", "f3": "调律记录"}
        assert _fn("find_key")(result, "调律") == "f2"

    def test_not_found_returns_empty(self):
        assert _fn("find_key")({"f1": "取消"}, "调律") == ""

    def test_non_string_values_skipped(self):
        assert _fn("find_key")({"f1": 123, "f2": "调律"}, "调律") == "f2"


class TestAppend:
    def test_append_to_list(self):
        lst = [1]
        assert _fn("append")(lst, 2) == ""
        assert lst == [1, 2]

    def test_append_to_dict(self):
        d = {}
        _fn("append")(d, "slot1", {"v": 1})
        assert d == {"slot1": {"v": 1}}

    def test_dict_key_coerced_to_str(self):
        d = {}
        _fn("append")(d, 5, "x")
        assert d == {"5": "x"}

    def test_none_target_noop(self):
        assert _fn("append")(None, 1) == ""

    def test_mismatched_args_noop(self):
        lst = [1]
        _fn("append")(lst)          # list 缺 value
        _fn("append")({}, "only")   # dict 缺 value
        assert lst == [1]
