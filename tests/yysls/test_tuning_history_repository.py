"""调律历史数据库、迁移和顺序语义。"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from scripts.analyze_tuning_affixes import parse_history_db

from lvjiang.apps.yysls.tuning_history.models import (
    TuningEquipmentResult,
    TuningRunSummary,
)
from lvjiang.apps.yysls.tuning_history.repository import (
    CURRENT_VERSION,
    TuningHistoryRepository,
)
from lvjiang.apps.yysls.tuning_history.session import TuningRunSession


def _run(run_id="run-1", started_at="2026-09-01T01:00:00+00:00"):
    return TuningRunSummary(
        run_id=run_id, started_at=started_at, finished_at="",
        username="小明", status="running", stop_reason="",
        selected_slots=("chest",), rule_snapshot=({"key": "rule"},),
    )


def _item(seq: int, *, tuning_started_at="2026-09-01T01:01:00+00:00"):
    return TuningEquipmentResult(
        equipment_id=seq, slot_key="chest", name=f"装备{seq}", type="胸甲",
        level=110, quality="gold", initial_affixes=({"name": "会心率"},),
        final_affixes=({"name": "会心率", "value": 10},),
        final_rating="excellent", rounds=1, result="tuned", reason="保留",
        raw_status="done", scanned_at="2026-09-01T01:00:30+00:00",
        tuning_started_at=tuning_started_at,
        finished_at="2026-09-01T01:02:00+00:00",
        round_details=({"round_no": 1},),
    )


def test_schema_is_versioned_and_reopen_is_idempotent(tmp_path):
    path = tmp_path / "history.db"
    repo = TuningHistoryRepository(path)
    assert repo.schema_version() == CURRENT_VERSION
    assert TuningHistoryRepository(path).schema_version() == CURRENT_VERSION
    conn = sqlite3.connect(path)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"schema_version", "tuning_runs", "tuning_equipment",
            "telemetry_deliveries"} <= tables


def test_results_round_trip_preserves_processing_order(tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    repo.create_run(_run())
    repo.save_equipment("run-1", _item(2), event_id="event-2")
    repo.save_equipment("run-1", _item(1), event_id="event-1")

    results = repo.get_results("run-1")

    assert [item.equipment_id for item in results] == [1, 2]
    assert results[0].initial_affixes[0]["name"] == "会心率"
    assert results[0].round_details[0]["round_no"] == 1


def test_only_recent_unreported_telemetry_is_selected(tmp_path):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    repo = TuningHistoryRepository(tmp_path / "history.db")
    repo.create_run(_run())
    recent = (now - timedelta(days=6)).isoformat()
    old = (now - timedelta(days=8)).isoformat()
    recent_id = repo.save_equipment(
        "run-1", _item(1, tuning_started_at=recent), event_id="recent")
    repo.save_equipment(
        "run-1", _item(2, tuning_started_at=old), event_id="old")

    pending = repo.pending_telemetry(now=now)

    assert [row["event_id"] for row in pending] == ["recent"]
    repo.mark_reported([recent_id], at=now.isoformat())
    assert repo.pending_telemetry(now=now) == []


def test_equipment_without_tuning_has_no_delivery(tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    repo.create_run(_run())
    item = _item(1, tuning_started_at="")
    repo.save_equipment("run-1", item, event_id=None)
    assert repo.pending_telemetry() == []


def test_delete_run_cascades_equipment_and_telemetry_state(tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    repo.create_run(_run())
    repo.save_equipment("run-1", _item(1), event_id="event-1")

    assert repo.delete_run("run-1")

    assert repo.get_run("run-1") is None
    assert repo.get_results("run-1") == ()
    assert repo.pending_telemetry() == []
    assert not repo.delete_run("run-1")


def test_affix_analyzer_reads_structured_history_in_processing_order(tmp_path):
    path = tmp_path / "history.db"
    repo = TuningHistoryRepository(path)
    repo.create_run(_run())
    for sequence_id, affix in ((2, "精准率"), (1, "会心率")):
        item = replace(
            _item(sequence_id),
            initial_affixes=({"name": "外功攻击"},),
            round_details=({
                "round_no": 1,
                "new_affix_data": {"name": affix},
            },),
            telemetry_final_rating="excellent",
        )
        repo.save_equipment(
            "run-1", item, event_id=f"event-{sequence_id}")

    records = parse_history_db(path)

    assert [record["name"] for record in records] == ["装备1", "装备2"]
    assert records[0]["affixes"] == ["外功攻击", "会心率"]
    assert records[0]["rating"] == "优秀"


def test_history_session_persists_reset_boundary_as_two_records(tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    session = TuningRunSession(
        repo, username="小明", selected_slots=["chest"],
        clock=lambda: "2026-09-01T01:00:00+00:00",
    )
    session.consume("slot_entered", "chest", "胸甲")
    session.consume("equipment_started", {
        "name": "流星甲", "type": "胸甲", "level": 110,
        "quality": "gold", "affixes": [{"name": "会心率"}],
    })
    session.consume("operation_updated", {"phase": "material"})
    session.consume("equipment_reset", {
        "name": "流星甲", "type": "胸甲", "level": 110,
        "quality": "gold", "before_affixes": [{"name": "会心率"}],
        "after_affixes": [{"name": "会心率"}], "resets_used": 1,
    })
    session.consume("equipment_finished", {
        "name": "流星甲", "rounds": 0, "status": "done",
        "final_affixes": [{"name": "会心率"}],
    })
    session.finish(status="completed")

    results = repo.get_results(session.run_id)
    assert [item.equipment_id for item in results] == [1, 2]
    assert [item.name for item in results] == [
        "流星甲（重置前）", "流星甲（重置后）"]
