"""quota 周期归零：读时派生，真实写入时才固化成 reset + set 两行。

语义收敛的目标是同一条：``reset`` 和 ``regen`` 都是**由定义与时间推导出的派生
状态**，读取时现算、不写库；只有真实业务写入才把当前值固化进账本。这样周一
05:00 跨周期时，引擎跑不跑、设备在不在线、读多少次都不会产生任何 reset 记录，
只有真正写了一次配额才落 ``reset``(旧值→0) + ``set``(0→新值) 两行。

每个用例都在保护一条会再次触发的契约：
- 跨周期读多次不落任何东西（否则又变回「每端每周期刷一批 reset」）；
- 跨周期写入的账本行满足 ``new = old + delta``（否则回放相加、撤销、
  按来源聚合全错，2026-11-15 心力事故的同一类坏账）；
- 当前值已经是 0 时不落 0→0 的 reset（只留一条 set）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def quota_env(tmp_path: Path, monkeypatch) -> None:
    from lvjiang.core.profile import repository, schema

    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "quota": [
                    {"key": "weekly", "label": "周常", "period": "week",
                     "reset_day": 1, "reset_time": "05:00"},
                    {"key": "daily", "label": "日常", "period": "day",
                     "reset_time": "05:00"},
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(schema, "_PROFILE_PATH", profile_path)
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    schema._config = None
    repository.reset_profile_db()


def _seed(key: str, value: float, *, minutes_ago: int | None = None,
          days_ago: int | None = None) -> str:
    """种一行 quota 并返回它的 event_at。"""
    from lvjiang.core.profile.repository import db_upsert

    delta = timedelta(days=days_ago or 0, minutes=minutes_ago or 0)
    stamp = (datetime.now() - delta).isoformat(timespec="seconds")
    db_upsert("u", "quota", key, value, event_at=stamp)
    return stamp


def _history(key: str) -> list:
    from lvjiang.core.profile.repository import db_get_history

    return db_get_history("u", type_="quota", key=key)


def _entry(key: str):
    from lvjiang.core.profile.repository import db_read_entry

    return db_read_entry("u", "quota", key)


def _last_boundary(period: str, reset_day: int = 0) -> datetime:
    from lvjiang.core.profile.periods import get_period_boundary

    return get_period_boundary(period, "05:00", datetime.now(), reset_day)


# ─── 读时派生 ────────────────────────────────────────────────


def test_read_across_boundary_returns_zero_without_touching_db(quota_env) -> None:
    from lvjiang.core.profile.service import profile_read

    stamp = _seed("weekly", 120, days_ago=8)

    assert profile_read("u", "weekly") == 0
    assert profile_read("u", "weekly") == 0
    assert profile_read("u", "weekly") == 0

    entry = _entry("weekly")
    assert (entry.value, entry.event_at) == (120.0, stamp)
    assert _history("weekly") == []


def test_read_inside_current_period_keeps_stored_value(quota_env) -> None:
    from lvjiang.core.profile.service import profile_read

    _seed("daily", 30, minutes_ago=1)

    assert profile_read("u", "daily") == 30


def test_boundary_flip_is_exact_to_the_second(quota_env) -> None:
    """05:00:00 这一秒就是新周期：前一天 23:59:59 仍是旧值，05:00:00 必须归零。"""
    from lvjiang.core.profile.periods import quota_current_value
    from lvjiang.core.profile.rows import Entry
    from lvjiang.core.profile.schema import get_profile_config

    kd = get_profile_config().get_key("daily", model_type="quota")
    entry = Entry(username="u", type="quota", key="daily", value=30,
                  event_at="2026-11-17T23:59:59")

    assert quota_current_value(entry, kd, now=datetime(2026, 11, 17, 23, 59, 59)) == 30
    assert quota_current_value(entry, kd, now=datetime(2026, 11, 18, 4, 59, 59)) == 30
    assert quota_current_value(entry, kd, now=datetime(2026, 11, 18, 5, 0, 0)) == 0


def test_profile_read_all_uses_derived_value(quota_env) -> None:
    from lvjiang.core.profile.service import profile_read_all

    _seed("weekly", 120, days_ago=8)
    _seed("daily", 30, minutes_ago=1)

    payload = profile_read_all("u")
    assert payload["quota"]["weekly"]["value"] == 0
    assert payload["quota"]["daily"]["value"] == 30


# ─── 真实写入：两行固化 ──────────────────────────────────────


def test_cross_period_write_materializes_reset_then_set(quota_env) -> None:
    from lvjiang.core.profile.service import profile_action

    _seed("weekly", 120, days_ago=8)
    boundary = _last_boundary("week", 1)

    profile_action("u", "weekly", set_value=30, source="周常")

    # db_get_history 按 local_id 降序：records[0] 是最后落的业务行。
    records = _history("weekly")
    assert [row.op_type for row in records] == ["set", "reset"]
    set_row, reset_row = records
    # 归零行：值 0、event_at 是周期边界、source 留空（机制不是业务）
    assert (reset_row.old_value, reset_row.new_value) == (120.0, 0.0)
    assert reset_row.delta_value == -120.0
    assert reset_row.source == ""
    assert reset_row.event_at == boundary.isoformat(timespec="seconds")
    # 业务行从 0 起算，源头是业务语境
    assert (set_row.old_value, set_row.new_value, set_row.delta_value) == (0.0, 30.0, 30.0)
    assert set_row.source == "周常"
    assert _entry("weekly").value == 30

    for row in records:
        assert row.old_value + row.delta_value == pytest.approx(row.new_value, abs=1e-9)


def test_same_period_write_does_not_materialize_reset(quota_env) -> None:
    from lvjiang.core.profile.service import profile_action

    _seed("daily", 30, minutes_ago=1)

    profile_action("u", "daily", delta=5, source="日常")

    records = _history("daily")
    assert [row.op_type for row in records] == ["set"]
    assert records[0].old_value == pytest.approx(30)
    assert _entry("daily").value == 35


def test_zero_value_does_not_write_zero_to_zero_reset(quota_env) -> None:
    """当前值已经是 0：不强行加一条 0→0 的 reset，直接写业务行。"""
    from lvjiang.core.profile.service import profile_action

    _seed("daily", 0, days_ago=3)

    profile_action("u", "daily", set_value=30, source="日常")

    records = _history("daily")
    assert [row.op_type for row in records] == ["set"]
    assert (records[0].old_value, records[0].new_value) == (0.0, 30.0)
    assert records[0].delta_value == pytest.approx(30)


def test_write_that_changes_nothing_records_nothing(quota_env) -> None:
    """跨周期但目标值等于派生值：既没有 reset 也没有 set。"""
    from lvjiang.core.profile.service import profile_action

    stamp = _seed("weekly", 120, days_ago=8)

    profile_action("u", "weekly", set_value=0, source="周常")

    assert _history("weekly") == []
    entry = _entry("weekly")
    assert (entry.value, entry.event_at) == (120.0, stamp)


# ─── CAS：期望值是库里那一行，不是派生值 ──────────────────────


def test_trigger_event_uses_derived_baseline_across_period(quota_env, monkeypatch) -> None:
    """变更脚本看到的是本周期基线（0），不是库里的旧周期满值。

    与账本一致：归零单独成一条 reset 行（120→0），业务行是 0→30。阈值类脚本比的是
    old/new，若这里塞进旧周期的 120，跨周期第一次写入会被误判成「下降」。
    """
    from lvjiang.core.profile import triggers
    from lvjiang.core.profile.service import profile_action

    captured = []
    monkeypatch.setattr(triggers, "submit_profile_trigger", captured.append)
    _seed("weekly", 120, days_ago=8)

    profile_action("u", "weekly", set_value=30, source="周常")

    assert [event.op_type for event in captured] == ["set"]
    event = captured[0]
    set_row = _history("weekly")[0]
    assert set_row.op_type == "set"
    assert (event.old_value, event.new_value, event.delta) == (
        set_row.old_value, set_row.new_value, set_row.delta_value)
    assert (event.old_value, event.new_value) == (0.0, 30.0)


def test_cas_write_uses_stored_value_not_derived_zero(quota_env) -> None:
    from lvjiang.core.profile.repository import db_read_entry
    from lvjiang.core.profile.service import profile_action

    _seed("weekly", 120, days_ago=8)
    snapshot = db_read_entry("u", "quota", "weekly")

    # 派生值是 0，但 CAS 必须按库里的 120 比对，否则这笔写入永远报冲突。
    profile_action("u", "weekly", set_value=30, source="周常",
                   current_value=0.0, expected_entry=snapshot, use_cas=True)

    assert [row.op_type for row in _history("weekly")] == ["set", "reset"]
    assert _history("weekly")[1].old_value == 120.0


def test_cas_still_rejects_when_another_writer_moved_the_row(quota_env) -> None:
    from lvjiang.core.profile.repository import db_read_entry, db_upsert
    from lvjiang.core.profile.service import ProfileWriteConflict, profile_action

    _seed("weekly", 120, days_ago=8)
    snapshot = db_read_entry("u", "quota", "weekly")
    db_upsert("u", "quota", "weekly", 200)          # 另一个进程刚写过

    with pytest.raises(ProfileWriteConflict):
        profile_action("u", "weekly", set_value=30, source="周常",
                       expected_entry=snapshot, use_cas=True)


# ─── profile_observe ────────────────────────────────────────


def test_observe_across_boundary_writes_reset_then_set(quota_env) -> None:
    from lvjiang.core.profile.service import profile_observe

    _seed("weekly", 800, days_ago=8)

    result = profile_observe("u", "weekly", 30, source="OCR 观测")

    assert result == {"accepted": True, "value": 30.0, "reason": "updated"}
    assert [row.op_type for row in _history("weekly")] == ["set", "reset"]
    assert _entry("weekly").value == 30


def test_observe_zero_across_boundary_writes_nothing(quota_env) -> None:
    """跨周期观测到 0：派生值本来就是 0，不落 reset 也不落 set。"""
    from lvjiang.core.profile.service import profile_observe

    stamp = _seed("weekly", 800, days_ago=8)

    result = profile_observe("u", "weekly", 0)

    assert result == {"accepted": True, "value": 0.0, "reason": "unchanged"}
    assert _history("weekly") == []
    entry = _entry("weekly")
    assert (entry.value, entry.event_at) == (800.0, stamp)


def test_observe_same_period_still_rejects_regression(quota_env) -> None:
    from lvjiang.core.profile.service import profile_observe

    _seed("daily", 80, minutes_ago=1)

    result = profile_observe("u", "daily", 8)

    assert result == {"accepted": False, "value": 80.0, "reason": "regressed"}
    assert _history("daily") == []


# ─── 跨设备回放：归零行带边界时间，不会压掉新周期的真实写入 ──


def test_late_reset_row_does_not_override_newer_write(quota_env) -> None:
    """他端补写的 05:00 归零行不会压掉本端 06:00 之后落的真实写入。

    归零行的 ``event_at`` 取周期边界而不是执行墙钟，正是为了这个场景：
    PC 周一 08:00 才把「周日 120 → 0」补进账本，也不能把手机 06:00 写的 30 顶回 0。
    """
    from lvjiang.core.profile import snapshot_sync
    from lvjiang.core.profile.rows import RemoteOp

    _seed("daily", 30, minutes_ago=1)

    op = RemoteOp(
        remote_id=1, instance_id="other-device", local_id=1, username="u",
        type="quota", key="daily", op="reset", value=0.0,
        happened_at=_last_boundary("day").isoformat(timespec="seconds"),
        op_id="late-reset",
    )
    assert snapshot_sync._apply_op(op) is False
    assert _entry("daily").value == 30


def test_reset_row_lands_on_device_still_in_last_period(quota_env) -> None:
    """落后设备还停在旧周期：他端的归零行照常落地，本端不必自己写一条。"""
    from lvjiang.core.profile import snapshot_sync
    from lvjiang.core.profile.rows import RemoteOp

    _seed("daily", 120, days_ago=2)

    op = RemoteOp(
        remote_id=1, instance_id="other-device", local_id=1, username="u",
        type="quota", key="daily", op="reset", value=0.0,
        happened_at=_last_boundary("day").isoformat(timespec="seconds"),
        op_id="fresh-reset",
    )
    assert snapshot_sync._apply_op(op) is True
    assert _entry("daily").value == 0


# ─── 显示层与引擎：不写库也要当场归零 ────────────────────────


def test_overview_cell_and_total_use_derived_value(quota_env) -> None:
    """桌面总览的单元格与合计行必须和 DSL 读到同一个数（跨周期都是 0）。"""
    from lvjiang.core.profile.models import QuotaKeyDef
    from lvjiang.core.profile.schema import get_profile_config
    from lvjiang.ui.profile.cell_formatting import (
        format_profile_cell,
        sum_profile_column,
    )

    _seed("weekly", 120, days_ago=8)
    kd = get_profile_config().get_key("weekly", model_type="quota")
    assert isinstance(kd, QuotaKeyDef)
    data = {"quota": {"weekly": _entry("weekly")}}

    assert format_profile_cell(kd, "quota", data) == ("0", "")
    assert sum_profile_column(kd, "quota", {"u": data}) == "0"


def test_engine_tick_does_not_write_reset(quota_env) -> None:
    """引擎只刷新界面，不写库：周一跨周期时数据库与账本都保持原样。"""
    from types import SimpleNamespace

    from lvjiang.core.profile.engine import ProfileProcessor
    from lvjiang.core.profile.repository import db_upsert

    stamp = _seed("weekly", 120, days_ago=8)
    db_upsert("u", "quota", "daily", 0)             # 条目都在，避免走首次初始化

    notified: list[str] = []
    processor = ProfileProcessor(
        SimpleNamespace(list_users=lambda: ["u"]),
        notify_updated=notified.append,
    )
    processor._last_clean = 1e18

    processor.tick()

    entry = _entry("weekly")
    assert (entry.value, entry.event_at) == (120.0, stamp)
    assert _history("weekly") == []
    # 派生值从 120 变成 0 → 通知界面重画一次；第二轮没有新变化就不再通知。
    assert notified == ["u"]

    processor.tick()
    assert notified == ["u"]


def test_next_boundary_delay_is_within_one_period(quota_env) -> None:
    from lvjiang.core.profile.engine import seconds_to_next_quota_boundary
    from lvjiang.core.profile.schema import get_profile_config

    now = datetime(2026, 11, 18, 12, 0)             # 周三 12:00
    delay = seconds_to_next_quota_boundary(get_profile_config(), now)

    assert delay is not None
    assert delay == pytest.approx(17 * 3600)        # 次日 05:00
