"""core/numbers：宽松与严格两种数值转换语义不可互换。"""
import pytest

from lvjiang.apps.yysls.core.numbers import strict_float, to_float, to_int


@pytest.mark.parametrize("value, expected", [
    (12, 12.0), (12.5, 12.5), ("12", 12.0), ("12.5", 12.5),
    (None, 0.0), ("", 0.0), (0, 0.0), ("abc", 0.0), ([], 0.0),
])
def test_to_float_is_lenient(value, expected):
    assert to_float(value) == expected


def test_to_float_default_only_on_failure():
    assert to_float("abc", default=-1.0) == -1.0
    assert to_float(None, default=-1.0) == 0.0    # 空值走 float(0)，不算解析失败
    assert to_float("3", default=-1.0) == 3.0


@pytest.mark.parametrize("value, expected", [
    (110, 110), (110.9, 110), ("110", 110), (None, 0), ("", 0),
    ("110.5", 0), ("abc", 0),
])
def test_to_int_is_lenient(value, expected):
    assert to_int(value) == expected


@pytest.mark.parametrize("value", ["12", "12.5", None, "", True, False, [], {}])
def test_strict_float_rejects_non_numbers(value):
    assert strict_float(value) is None
    assert strict_float(value, 0.0) == 0.0


def test_strict_float_accepts_numbers():
    assert strict_float(12) == 12.0
    assert strict_float(12.5) == 12.5
    assert strict_float(0) == 0.0
