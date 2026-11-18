"""关联写入与直接写入共用恢复固化：真实 DSL、SQLite 与历史撤销。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from lvjiang.core.profile import repository, schema, service
from lvjiang.core.profile.periods import get_period_boundary
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


@pytest.fixture
def run_profile(tmp_path, monkeypatch):
    path = tmp_path / "profile.yaml"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(schema, "_PROFILE_PATH", path)
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    schema._config = None
    repository.reset_profile_db()
    engine = make_engine()
    engine.run_username = "tester"

    def run(code):
        engine._exec_body(parse_text(code).body)
        return engine.variables

    yield run
    schema._config = None
    repository.reset_profile_db()


def _declare(run, model, key, definition):
    payload = json.dumps(definition, ensure_ascii=False)
    result = run(f'eval $declared = profile_declare("{model}", "{key}", {payload})')
    assert result["declared"]["ok"]


def _declare_regen_target(run, *, realtime=False):
    definition = (
        {"regen_type": "realtime", "regen_rate_value": 1, "regen_rate_unit": "minute"}
        if realtime else
        {"regen_type": "boundary", "regen_amount": 450,
         "regen_period": "day", "reset_time": "05:00"}
    )
    _declare(run, "regen", "tili", {"label": "资源", "cap": 2500, **definition})
    _declare(run, "stock", "counter", {
        "label": "消耗记账",
        "sync_targets": [{"key": "regen:tili", "ratio": -1}],
    })


def _before_daily_boundary():
    return (get_period_boundary("day", "05:00", datetime.now())
            - timedelta(minutes=1)).isoformat(timespec="seconds")


def _history(model="regen", key="tili"):
    return repository.db_get_history("tester", type_=model, key=key)


def test_dsl_regen_target_materializes_once_and_undo_reverses_only_consumption(run_profile):
    _declare_regen_target(run_profile)
    repository.db_upsert("tester", "regen", "tili", 100, event_at=_before_daily_boundary())

    run_profile('eval profile_inc("counter", 30, "消耗")')

    rows = _history()
    assert [(r.op_type, r.old_value, r.new_value, r.delta_value) for r in rows] == [
        ("set", 550, 520, -30), ("regen", 100, 550, 450),
    ]
    assert rows[0].sync_from == "stock:counter"
    assert rows[1].source == ""
    assert service.profile_history_undo_unavailable_reason(rows[0]) is None
    service.undo_profile_history(rows[0].local_id)
    assert service.profile_read("tester", "tili") == 550

    # 固化后同周期的后续关联写入只落业务行，不能把恢复量重复计入。
    run_profile('eval profile_inc("counter", 10, "消耗")')
    assert service.profile_read("tester", "tili") == 540
    assert [r.op_type for r in _history()] == ["set", "set", "set", "regen"]


def test_realtime_target_uses_materialized_baseline_for_business_delta(run_profile):
    _declare_regen_target(run_profile, realtime=True)
    stamp = (datetime.now() - timedelta(minutes=60)).isoformat(timespec="seconds")
    repository.db_upsert("tester", "regen", "tili", 20, event_at=stamp)

    run_profile('eval profile_inc("counter", 30, "消耗")')

    rows = _history()
    assert [r.op_type for r in rows] == ["set", "regen"]
    assert rows[0].new_value == pytest.approx(50, abs=0.1)
    assert rows[0].delta_value == -30
    for row in rows:
        assert row.old_value + row.delta_value == pytest.approx(row.new_value, abs=1e-9)


def test_target_business_failure_rolls_back_source_regen_and_history(run_profile, monkeypatch):
    _declare_regen_target(run_profile)
    stamp = _before_daily_boundary()
    repository.db_upsert("tester", "regen", "tili", 100, event_at=stamp)
    original = service.db_upsert

    def fail_business(username, model, key, value, **kwargs):
        if model == "regen" and kwargs.get("op_type") == "set":
            raise RuntimeError("目标写入失败")
        return original(username, model, key, value, **kwargs)

    monkeypatch.setattr(service, "db_upsert", fail_business)
    with pytest.raises(RuntimeError, match="目标写入失败"):
        run_profile('eval profile_inc("counter", 30, "消耗")')

    entry = repository.db_read_entry("tester", "regen", "tili")
    assert (entry.value, entry.event_at) == (100, stamp)
    assert repository.db_read_entry("tester", "stock", "counter") is None
    assert _history() == []
    assert _history("stock", "counter") == []


@pytest.mark.parametrize("model", ["stock", "quota"])
def test_other_target_models_keep_their_existing_ledger(run_profile, model):
    _declare(run_profile, model, "target", {"label": "目标", **(
        {"period": "day", "reset_time": "05:00"} if model == "quota" else {})})
    _declare(run_profile, "stock", "counter", {
        "label": "来源", "sync_targets": [{"key": f"{model}:target"}],
    })
    repository.db_upsert("tester", model, "target", 120, event_at=_before_daily_boundary())

    run_profile('eval profile_inc("counter", 30, "增加")')

    rows = _history(model, "target")
    expected = ([("set", 0, 30, 30), ("reset", 120, 0, -120)] if model == "quota"
                else [("set", 120, 150, 30)])
    assert [(r.op_type, r.old_value, r.new_value, r.delta_value) for r in rows] == expected


def test_capped_regen_target_does_not_materialize_on_no_change(run_profile):
    _declare_regen_target(run_profile)
    stamp = _before_daily_boundary()
    repository.db_upsert("tester", "regen", "tili", 2500, event_at=stamp)

    # 来源减少使目标增加；目标已满时没有实际变化，不能新增恢复或业务行。
    repository.db_upsert("tester", "stock", "counter", 30)
    run_profile('eval profile_inc("counter", -10, "返还")')
    assert not _history()
    assert repository.db_read_entry("tester", "regen", "tili").event_at == stamp
