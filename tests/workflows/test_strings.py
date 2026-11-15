"""字符串与正则内置函数测试

覆盖 join / startswith / endswith / index_of / repeat、正则五件套与防护边界，
以及 lib/text.wf 里纯 DSL 实现的 progress_of。
"""

from pathlib import Path

import pytest

from lvjiang.workflows.builtins import get_function
from lvjiang.workflows.engine.signals import WorkflowUserError
from tests.workflows.conftest import make_engine, run

LIB_DIR = Path(__file__).resolve().parents[2] / "config/system/workflows/lib"


def _fn(name):
    fn = get_function(name)
    assert fn is not None, f"内置函数 {name} 未注册"
    return fn


# ─── 字符串工具 ────────────────────────────────────────────


class TestSubstr:
    def test_closed_interval_is_unchanged(self):
        fn = _fn("substr")
        assert fn("abcdef", 0, 2) == "abc"
        assert fn("abcdef", 1) == "bcdef"
        assert fn("abcdef", 0) == "abcdef"

    def test_negative_end_counts_from_the_tail(self):
        fn = _fn("substr")
        assert fn("abcdef", 0, -1) == "abcdef"
        assert fn("abcdef", 1, -2) == "bcde"
        assert fn("abcdef", -3, -2) == "de"
        assert fn("abcdef", -1, -1) == "f"

    def test_out_of_range_end_truncates(self):
        fn = _fn("substr")
        assert fn("abcdef", 0, -100) == ""
        assert fn("abcdef", 0, 100) == "abcdef"
        assert fn("", 0, -1) == ""


class TestStringHelpers:
    def test_join_uses_separator_and_treats_null_as_empty(self):
        assert _fn("join")(["a", "b", None], ",") == "a,b,"
        assert _fn("join")(["a", "b"]) == "ab"
        assert _fn("join")([], ",") == ""

    def test_join_non_list_returns_empty(self):
        assert _fn("join")("abc", ",") == ""
        assert _fn("join")(None, ",") == ""

    def test_startswith_and_endswith(self):
        assert _fn("startswith")("本期任务", "本期") is True
        assert _fn("startswith")("本期任务", "上期") is False
        assert _fn("endswith")("武器（绑）", "（绑）") is True
        assert _fn("endswith")("武器", "（绑）") is False

    def test_prefix_suffix_empty_matches_python_semantics(self):
        assert _fn("startswith")("abc", "") is True
        assert _fn("endswith")("abc", "") is True

    def test_index_of(self):
        assert _fn("index_of")("a/b/c", "/") == 1
        assert _fn("index_of")("abc", "z") == -1
        # 空子串按「没找到」处理，避免与「在下标 0 找到」混淆
        assert _fn("index_of")("abc", "") == -1
        assert _fn("index_of")(None, "a") == -1

    def test_repeat(self):
        assert _fn("repeat")("ab", 3) == "ababab"
        assert _fn("repeat")("ab", 0) == ""
        assert _fn("repeat")("ab", -2) == ""
        assert _fn("repeat")("", 5) == ""

    def test_repeat_rejects_oversized_result(self):
        with pytest.raises(WorkflowUserError, match="结果过长"):
            _fn("repeat")("x", 1_000_001)

    def test_repeat_rejects_non_numeric_count(self):
        with pytest.raises(WorkflowUserError, match="重复次数必须是整数"):
            _fn("repeat")("x", "abc")


# ─── 正则基础能力 ──────────────────────────────────────────


class TestRegex:
    def test_find_returns_group_and_defaults_to_whole_match(self):
        fn = _fn("regex_find")
        assert fn("等级 110 阶", r"等级\s*(\d+)", 1) == "110"
        assert fn("等级 110 阶", r"等级\s*(\d+)") == "等级 110"
        assert fn("无数字", r"(\d+)") == ""

    def test_find_optional_group_not_matched_is_empty(self):
        assert _fn("regex_find")("ac", "a(b)?", 1) == ""

    def test_find_group_out_of_range_raises(self):
        with pytest.raises(WorkflowUserError, match="超出范围"):
            _fn("regex_find")("abc", "a", 1)

    def test_find_all_returns_whole_match_plus_groups(self):
        assert _fn("regex_find_all")("1/2 3/4", r"(\d)/(\d)") == [
            ["1/2", "1", "2"],
            ["3/4", "3", "4"],
        ]
        # 没有捕获组时每项只有整段匹配
        assert _fn("regex_find_all")("a1b22", r"\d+") == [["1"], ["22"]]
        assert _fn("regex_find_all")("abc", r"\d") == []

    def test_find_all_pads_unmatched_optional_groups(self):
        assert _fn("regex_find_all")("ac", "a(b)?") == [["a", ""]]

    def test_replace_supports_literal_and_backreference(self):
        assert _fn("regex_replace")("a1b22c", r"\d+", "#") == "a#b#c"
        assert _fn("regex_replace")("12-34", r"(\d+)-(\d+)", r"\2-\1") == "34-12"

    def test_split_on_multiple_separators(self):
        assert _fn("regex_split")("a, b，c、d", r"[,，、]\s*") == ["a", "b", "c", "d"]

    def test_escape_makes_literal_roundtrip(self):
        raw = "材料(紫)+1.5*"
        escaped = _fn("regex_escape")(raw)
        assert _fn("match")(raw, escaped) is True
        assert _fn("match")(raw + "x", f"^{escaped}$") is False


# ─── 防护边界 ──────────────────────────────────────────────


class TestRegexGuards:
    def test_invalid_pattern_raises_instead_of_returning_false(self):
        with pytest.raises(WorkflowUserError, match="非法正则"):
            _fn("match")("abc", "(")

    @pytest.mark.parametrize("name", ["match", "regex_find", "regex_find_all",
                                      "regex_replace", "regex_split"])
    def test_empty_pattern_raises(self, name):
        fn = _fn(name)
        with pytest.raises(WorkflowUserError, match="不能为空"):
            if name == "regex_replace":
                fn("abc", "", "x")
            else:
                fn("abc", "")

    def test_oversized_pattern_raises(self):
        with pytest.raises(WorkflowUserError, match="正则过长"):
            _fn("regex_find")("abc", "a" * 2001)

    def test_oversized_subject_raises(self):
        with pytest.raises(WorkflowUserError, match="待匹配文本过长"):
            _fn("regex_find")("a" * 20001, "a")

    def test_null_subject_is_treated_as_empty(self):
        assert _fn("regex_find_all")(None, r"\d") == []


# ─── DSL 中的实际用法 ─────────────────────────────────────


def test_regex_functions_work_in_dsl_expressions():
    values = run(
        'eval $level = regex_find($raw, "等级\\s*(\\d+)", 1)\n'
        'eval $ok = match($raw, "^等级")\n',
        {"raw": "等级 110 阶"},
    )
    assert values["level"] == "110"
    assert values["ok"] is True


def test_invalid_regex_fails_loudly_inside_dsl():
    with pytest.raises(WorkflowUserError, match="非法正则"):
        run('eval $ok = match("abc", "(")\n')


# ─── progress_of（纯 DSL 实现）─────────────────────────────


@pytest.fixture(scope="module")
def text_library():
    engine = make_engine(ocr=None)
    engine.load_subcalls(LIB_DIR / "text.wf")
    return engine


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0/1500", {"valid": True, "current": 0, "total": 1500, "hits": 1}),
        ("@1,950/2,500", {"valid": True, "current": 1950, "total": 2500, "hits": 1}),
        ("12 ／ 30", {"valid": True, "current": 12, "total": 30, "hits": 1}),
        ("体力 5/5", {"valid": True, "current": 5, "total": 5, "hits": 1}),
        # 当前值大于总量：候选不合法，与「没读到」同样返回 -1
        ("3/2", {"valid": False, "current": -1, "total": -1, "hits": 0}),
        ("0/0", {"valid": False, "current": -1, "total": -1, "hits": 0}),
        # 两个合法候选：不猜，直接判为无效
        ("1/2 和 3/4", {"valid": False, "current": -1, "total": -1, "hits": 2}),
        # 小数不按进度对解析
        ("1.5/2", {"valid": False, "current": -1, "total": -1, "hits": 0}),
        ("无进度", {"valid": False, "current": -1, "total": -1, "hits": 0}),
        ("", {"valid": False, "current": -1, "total": -1, "hits": 0}),
    ],
)
def test_progress_of(text_library, raw, expected):
    assert text_library.call_subcall("progress_of", [raw]) == expected


def test_progress_of_accepts_null(text_library):
    assert text_library.call_subcall("progress_of", [None]) == {
        "valid": False, "current": -1, "total": -1, "hits": 0,
    }
