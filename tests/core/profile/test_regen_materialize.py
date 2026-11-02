"""regen 写入必须先固化 tick，再写 set/delta：每行都满足 new = old + delta。"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def regen_env(tmp_path: Path, monkeypatch) -> None:
    from lvjiang.core.profile import repository, schema

    profile_path = tmp_path / "profile.yaml"
    profile_path.write_text(
        yaml.safe_dump(
            {
                "regen": [
                    {"key": "xinli", "label": "心力", "regen_type": "realtime",
                     "regen_rate_value": 0.125, "regen_rate_unit": "minute", "cap": 600},
                    {"key": "tili", "label": "体力", "regen_type": "boundary",
                     "regen_amount": 450, "regen_period": "day",
                     "reset_time": "05:00", "cap": 2500},
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


def _history(model: str, key: str) -> list[dict]:
    from lvjiang.core.profile.repository import db_get_history

    return db_get_history("u", type_=model, key=key)


def _rewind(key: str, *, minutes: int) -> None:
    """把 updated_at 拨回过去，模拟这段时间的自动恢复。"""
    from lvjiang.core.profile.repository import db_read_entry, db_upsert

    entry = db_read_entry("u", "regen", key)
    stamp = (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    db_upsert("u", "regen", key, entry["value"], updated_at=stamp)


def test_realtime_regen_write_materializes_tick_before_delta(regen_env):
    from lvjiang.core.profile.service import profile_action, profile_read

    profile_action("u", "xinli", set_value=19, source="初始化")
    _rewind("xinli", minutes=60)          # 0.125/分钟 → 60 分钟恢复 7.5

    profile_action("u", "xinli", delta=-5, source="镇守")

    records = _history("regen", "xinli")
    latest, tick = records[0], records[1]
    assert tick["change_type"] == "tick"
    assert (tick["old_value"], tick["new_value"]) == (19.0, 26.0)
    assert tick["delta_value"] == 7.0
    assert latest["change_type"] == "delta"
    assert (latest["old_value"], latest["new_value"], latest["delta_value"]) == (26.0, 21.0, -5.0)
    for record in (tick, latest):
        assert record["new_value"] - record["old_value"] == record["delta_value"]
    # 小数进度必须保留：写入前是 26.5 点，消耗 20 后应是 6.5 而不是 6.0，
    # 否则每次消耗都会把「距下一点的进度」重置成 0（最多白丢 1 点）。
    remaining = profile_read("u", "xinli")
    assert 21.4 <= remaining < 21.6, remaining   # 26.5 − 5


def test_boundary_regen_write_materializes_tick_before_delta(regen_env):
    from lvjiang.core.profile.service import profile_action

    profile_action("u", "tili", set_value=100, source="初始化")
    _rewind("tili", minutes=60 * 30)      # 跨过 05:00 边界 → +450

    profile_action("u", "tili", delta=-30, source="副本")

    records = _history("regen", "tili")
    latest, tick = records[0], records[1]
    assert tick["change_type"] == "tick"
    assert tick["delta_value"] == 450.0
    assert latest["change_type"] == "delta"
    assert (latest["old_value"], latest["new_value"], latest["delta_value"]) == (550.0, 520.0, -30.0)
    for record in (tick, latest):
        assert record["new_value"] - record["old_value"] == record["delta_value"]


def test_realtime_regen_cas_write_survives_materialization(regen_env):
    """UI 带 CAS 快照写入时，固化不能把这个快照判成「其他进程更新过」。"""
    from lvjiang.core.profile.repository import db_read_entry
    from lvjiang.core.profile.service import profile_action, profile_read

    profile_action("u", "xinli", set_value=19, source="初始化")
    _rewind("xinli", minutes=60)
    stale = db_read_entry("u", "regen", "xinli")      # 界面打开时读到的快照

    profile_action("u", "xinli", delta=-20, source="镇守",
                   use_cas=True, expected_entry=stale)

    assert int(profile_read("u", "xinli")) == 6       # 19 + 7 恢复 − 20
    assert [r["change_type"] for r in _history("regen", "xinli")[:2]] == ["delta", "tick"]


def test_realtime_regen_progress_is_conserved_across_many_writes(regen_env):
    """800 分钟里穿插多次写入，最终心力必须稳定增加 100，不产生累计误差。

    心力 0.125/分钟：800 分钟正好 100 点。每次写入都要先把小数进度固化进 updated_at，
    若某次写入把锚点重置到 now（丢到多的小数点），几十次写入就会累计丢几个点。
    """
    # 直接种一行 0 值（set_value=0 因为「值未变」不会落库）
    from lvjiang.core.profile.repository import db_upsert
    from lvjiang.core.profile.service import profile_action, profile_read

    db_upsert("u", "regen", "xinli", 0,
              updated_at=datetime.now().isoformat(timespec="seconds"))
    rounds = 20
    for _ in range(rounds):
        _rewind("xinli", minutes=800 // rounds)      # 每轮过去 40 分钟 = 5 点
        profile_action("u", "xinli", delta=1, source="消耗")
        profile_action("u", "xinli", delta=-1, source="返还")   # 净零，只为触发写入

    final = profile_read("u", "xinli")
    # 800 分钟 = 100 点；容差只留给时间戳按秒截断（每次 <0.01 点）。
    assert abs(final - 100.0) < 0.2, final
    for record in _history("regen", "xinli"):
        assert record["new_value"] - record["old_value"] == pytest.approx(
            record["delta_value"], abs=1e-6), record
