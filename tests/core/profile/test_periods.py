from datetime import datetime

import pytest

from lvjiang.core.profile.periods import (
    get_period_boundary,
    get_profile_period,
    register_profile_period,
)


def test_standard_periods_are_registered():
    assert get_profile_period("day") is not None
    assert get_profile_period("week") is not None
    assert get_profile_period("month") is not None


def test_plugin_period_is_used_by_global_resolver():
    name = "test_event_boundary"
    expected = datetime(2026, 8, 1, 5)
    register_profile_period(name, lambda _time, _now, _day: expected)
    assert get_period_boundary(name, "05:00", datetime(2026, 8, 29), 0) == expected


def test_duplicate_period_registration_is_rejected():
    name = "test_duplicate_boundary"
    def resolver(_time, now, _day):
        return now

    register_profile_period(name, resolver)
    with pytest.raises(ValueError, match="已注册"):
        register_profile_period(name, resolver)


def test_unknown_period_is_rejected():
    with pytest.raises(ValueError, match="未注册"):
        get_period_boundary("missing_period", "05:00", datetime(2026, 8, 29))
