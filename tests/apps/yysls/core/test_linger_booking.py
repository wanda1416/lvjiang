"""入账链路：写账本的参数口径与「同步记账」开关。

这里守三条容易悄悄错的约定：默认**不**记账、库存目标不能触发 sync_targets
（``niaoniao`` 挂着八音窍的账），以及开关是按用户存的。
"""
from __future__ import annotations

import pytest

from lvjiang.apps.yysls.core.linger import booking


class _FakeInterfaceStore:
    def __init__(self) -> None:
        self.nodes: dict[str, object] = {}

    def get_node(self, key: str, default=None):
        return self.nodes.get(key, default)

    def mutate_node(self, key: str, fn):
        self.nodes[key] = fn(self.nodes.get(key))
        return self.nodes[key]


@pytest.fixture
def fake_interface(monkeypatch):
    store = _FakeInterfaceStore()
    monkeypatch.setattr(booking, "get_interface_store", lambda: store)
    return store


def test_write_delta_never_triggers_sync_targets_for_stock(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(booking, "profile_action", lambda *a, **kw: calls.append(kw))

    assert booking.write_delta(
        "alice", "stock:niaoniao", 3, "侠迹：河西卷六·太平归处") is None

    assert calls == [{
        "delta": 3,
        "source": "侠迹：河西卷六·太平归处",
        "trigger_sync": False,
    }]


def test_write_delta_keeps_sync_for_quota_targets(monkeypatch) -> None:
    """配额目标本身就是那条购买账，同步目标该走还得走。"""
    calls: list[dict] = []
    monkeypatch.setattr(booking, "profile_action", lambda *a, **kw: calls.append(kw))

    booking.write_delta("alice", "quota:niaoniao_of_week", 1, "百珍商店")

    assert calls[0]["trigger_sync"] is True


def test_write_delta_reports_failure_instead_of_raising(monkeypatch) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("目标已锁定")

    monkeypatch.setattr(booking, "profile_action", boom)

    assert booking.write_delta("alice", "stock:niaoniao", 1, "以武会友") == "目标已锁定"


def test_write_delta_skips_zero(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(booking, "profile_action", lambda *a, **kw: calls.append(kw))

    assert booking.write_delta("alice", "stock:niaoniao", 0, "以武会友") is None
    assert calls == []


def test_booking_is_off_by_default(fake_interface) -> None:
    """默认必须是「只记录」：误点一下不能把账本改了。"""
    assert booking.booking_enabled("alice") is False


def test_booking_switch_is_per_user(fake_interface) -> None:
    booking.set_booking_enabled("alice", True)
    assert booking.booking_enabled("alice") is True
    assert booking.booking_enabled("bob") is False

    booking.set_booking_enabled("alice", False)
    assert booking.booking_enabled("alice") is False
    # 关掉后不留下空字典，界面状态保持干净
    assert "linger_booking" not in (fake_interface.nodes.get("ui_state") or {})


def test_booking_switch_survives_dirty_state(fake_interface) -> None:
    fake_interface.nodes["ui_state"] = {"linger_booking": {"alice": True}}

    assert booking.booking_enabled("alice") is True
    fake_interface.nodes["ui_state"] = "broken"
    assert booking.booking_enabled("alice") is False


def test_periodic_is_derived_from_target_model() -> None:
    assert booking.is_periodic("quota:niaoniao_of_week") is True
    assert booking.is_periodic("stock:niaoniao") is False
    assert booking.split_target("quota:niaoniao_of_week") == ("quota", "niaoniao_of_week")
    assert booking.split_target("niaoniao") == ("stock", "niaoniao")
