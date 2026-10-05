"""DSL 内置函数测试（_registry / arithmetic / general）

ee855a7 内置函数模块化拆分后无直测，本文件补充回归保护。
system 模块（confirm/pause/save/panel_rows）依赖 engine 与 GUI，不纳入。
"""

import pytest

from lvjiang.workflows.builtins import builtin_func, get_function, list_functions
from tests.case_matrix import case_matrix


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
            "clock", "datetime",
        }
        assert expected <= registered


    def test_builtin_func_decorator_registers(self):
        @builtin_func("_test_only_fn")
        def _impl(*args):
            return "ok"

        assert get_function("_test_only_fn") is _impl


# ─── arithmetic ────────────────────────────────────────────

class TestArithmetic:
    @case_matrix("name,a,b,expected", [
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

    @case_matrix("name", ["div", "mod"])
    def test_divide_by_zero_returns_zero(self, name):
        assert _fn(name)(7, 0) == 0

    @case_matrix("name", [
        "add", "sub", "mul", "div", "mod",
    ])
    def test_invalid_input_returns_zero(self, name):
        assert _fn(name)("abc", 1) == 0
        assert _fn(name)(None, 1) == 0

    def test_min_max_skip_invalid(self):
        """min/max 跳过无效值，仅全无效时返回 0"""
        assert _fn("min")("abc", 1) == 1
        assert _fn("max")("abc", 1) == 1
        assert _fn("min")(None, 1) == 1
        assert _fn("max")(None, 1) == 1
        assert _fn("min")("abc", "def") == 0
        assert _fn("max")("abc", "def") == 0

    def test_abs(self):
        assert _fn("abs")(-5) == 5
        assert _fn("abs")("3") == 3
        assert _fn("abs")("abc") == 0


# ─── general ───────────────────────────────────────────────

class TestConcat:
    def test_concat_mixed_and_empty_arguments(self):
        # mixed_args
        assert _fn("concat")("结果: ", 3, " 完成") == "结果: 3 完成"

        # empty
        assert _fn("concat")() == ""


class TestRange:
    def test_range_closed_bounds_and_arity(self):
        # single_arg_starts_from_one
        assert _fn("range")(3) == [1, 2, 3]

        # two_args_closed_interval
        assert _fn("range")(2, 5) == [2, 3, 4, 5]

        # too_many_args_raises
        with pytest.raises(ValueError):
            _fn("range")(1, 2, 3)


class TestCountNonempty:
    def test_count_nonempty_input_types(self):
        # dict_counts_non_empty_values
        assert _fn("count_nonempty")({"a": "x", "b": "", "c": "  ", "d": "y"}) == 2

        # list_counts_elements
        assert _fn("count_nonempty")([1, 2, 3]) == 3

        # other_types_return_zero
        assert _fn("count_nonempty")("text") == 0
        assert _fn("count_nonempty")(None) == 0


class TestContains:
    def test_contains_hit_miss_and_invalid_input(self):
        # hit_and_miss
        result = {"f1": "开始调律", "f2": "取消"}
        assert _fn("contains")(result, "调律") is True
        assert _fn("contains")(result, "不存在") is False

        # non_dict_or_no_args
        assert _fn("contains")("text", "t") is False
        assert _fn("contains")({"a": "b"}) is False


class TestFindKey:
    def test_find_key_order_miss_and_non_text_values(self):
        # returns_first_matching_key
        result = {"f1": "取消", "f2": "开始调律", "f3": "调律记录"}
        assert _fn("find_key")(result, "调律") == "f2"

        # not_found_returns_empty
        assert _fn("find_key")({"f1": "取消"}, "调律") == ""

        # non_string_values_skipped
        assert _fn("find_key")({"f1": 123, "f2": "调律"}, "调律") == "f2"


class TestAppend:
    def test_append_list_mutation_and_invalid_arity(self):
        # append_to_list
        lst = [1]
        assert _fn("append")(lst, 2) == ""
        assert lst == [1, 2]

        # mismatched_args_noop
        lst = [1]
        _fn("append")(lst)          # list 缺 value
        _fn("append")({}, "only")   # dict 缺 value
        assert lst == [1]

    def test_append_dict_mutation_and_key_conversion(self):
        # append_to_dict
        d = {}
        _fn("append")(d, "slot1", {"v": 1})
        assert d == {"slot1": {"v": 1}}

        # dict_key_coerced_to_str
        d = {}
        _fn("append")(d, 5, "x")
        assert d == {"5": "x"}


# ─── 时间函数 ─────────────────────────────────────────────


class TestDatetime:


    def test_datetime_timestamp_with_default_and_custom_format(self):
        # datetime_with_timestamp
        ts = 1768470645.0
        result = _fn("datetime")(ts)
        assert isinstance(result, str)
        assert len(result) == 19  # "YYYY-MM-DD HH:MM:SS"

        # datetime_with_timestamp_and_format
        ts = 1768470645.0
        time_str = _fn("datetime")(ts, "%H:%M:%S")
        assert isinstance(time_str, str)
        parts = time_str.split(":")
        assert len(parts) == 3
