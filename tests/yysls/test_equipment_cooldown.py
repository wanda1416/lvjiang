from datetime import datetime, timedelta, timezone

from lvjiang.apps.yysls.core.equipment_cooldown import next_cooldown_expiry

NOW = datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc)


def _expiry(previous, *, carryover=True) -> datetime:
    return datetime.fromisoformat(next_cooldown_expiry(
        previous, days=5, carryover=carryover, now=NOW))


def test_new_cooldown_starts_from_now_without_expired_progress():
    assert _expiry("") == NOW + timedelta(days=5)


def test_expired_progress_reduces_the_next_cooldown():
    previous = NOW - timedelta(days=1)

    assert _expiry(previous.isoformat()) == previous + timedelta(days=5)


def test_carryover_can_be_disabled():
    previous = NOW - timedelta(days=1)

    assert _expiry(previous.isoformat(), carryover=False) == (
        NOW + timedelta(days=5))


def test_carryover_keeps_at_most_one_ready_reset():
    previous = NOW - timedelta(days=7)

    carried = _expiry(previous.isoformat())
    assert carried == NOW
    assert _expiry(carried.isoformat()) == NOW + timedelta(days=5)


def test_active_cooldown_restarts_from_now():
    previous = NOW + timedelta(days=2)

    assert _expiry(previous.isoformat()) == NOW + timedelta(days=5)
