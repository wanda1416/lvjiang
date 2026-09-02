"""最近七天调律历史的匿名投影与上报回写。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.telemetry.history_source import SOURCE
from lvjiang.apps.yysls.tuning_history.models import (
    TuningEquipmentResult,
    TuningRunSummary,
)
from lvjiang.apps.yysls.tuning_history.repository import TuningHistoryRepository
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import consent


def test_recent_history_projects_anonymously_and_marks_reported(
    tmp_path, monkeypatch,
):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        constants, "SESSION_PATH",
        tmp_path / "config" / "session" / "session.json")
    monkeypatch.setattr(
        "lvjiang.core.telemetry.consent._is_dev_build", lambda: False)
    reset_session_store()
    consent.record_consent_choice(True)

    repo = TuningHistoryRepository()
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    run = TuningRunSummary(
        run_id="run", started_at=now, finished_at="", username="不可上传用户名",
        status="running", stop_reason="", config_snapshot={
            "game_config_customized": False,
        },
    )
    repo.create_run(run)
    game = get_game_config()
    equip_type = game.get_weapon_types()[0]
    affix_name = game.get_normal_affix_names()[0]
    item = TuningEquipmentResult(
        equipment_id=1, slot_key="main_weapon", name="不可上传装备名",
        type=equip_type, level=110, quality="gold",
        initial_affixes=({"name": affix_name, "cap_pct": 50.0},),
        final_affixes=({"name": affix_name, "cap_pct": 60.0},),
        final_rating="excellent", rounds=1, result="tuned", reason="保留",
        tuning_started_at=now, finished_at=now,
        round_details=({
            "new_affix_data": {"name": affix_name, "cap_pct": 60.0},
            "food_used": "", "affix_count": 2, "resets": 0,
        },),
        tuning_mode="normal", telemetry_stop_reason="completed",
        telemetry_final_rating="excellent", resets=0,
    )
    equipment_id = repo.save_equipment("run", item, event_id="1" * 32)

    batches = SOURCE.collect(200)

    assert len(batches) == 1
    event = batches[0].events[0]
    assert event["event_id"] == "1" * 32
    assert event["version"] == 2
    assert event["rolls"][0]["affix"] == affix_name
    blob = str(event)
    assert "不可上传用户名" not in blob
    assert "不可上传装备名" not in blob

    SOURCE.mark_reported((equipment_id,))
    assert repo.pending_telemetry() == []
    reset_session_store()


def _seed_one(tmp_path, monkeypatch):
    """铺一条合法的待上报记录，返回 (repo, equipment_id)。"""
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(
        constants, "SESSION_PATH",
        tmp_path / "config" / "session" / "session.json")
    monkeypatch.setattr(
        "lvjiang.core.telemetry.consent._is_dev_build", lambda: False)
    reset_session_store()
    consent.record_consent_choice(True)

    repo = TuningHistoryRepository()
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    repo.create_run(TuningRunSummary(
        run_id="run", started_at=now, finished_at="", username="u",
        status="running", stop_reason="",
        config_snapshot={"game_config_customized": False}))
    game = get_game_config()
    affix_name = game.get_normal_affix_names()[0]
    item = TuningEquipmentResult(
        equipment_id=1, slot_key="main_weapon", name="装备",
        type=game.get_weapon_types()[0], level=110, quality="gold",
        initial_affixes=({"name": affix_name, "cap_pct": 50.0},),
        final_affixes=({"name": affix_name, "cap_pct": 60.0},),
        final_rating="excellent", rounds=1, result="tuned", reason="保留",
        tuning_started_at=now, finished_at=now,
        round_details=({
            "new_affix_data": {"name": affix_name, "cap_pct": 60.0},
            "food_used": "", "affix_count": 2, "resets": 0,
        },),
        tuning_mode="normal", telemetry_stop_reason="completed",
        telemetry_final_rating="excellent", resets=0,
    )
    return repo, repo.save_equipment("run", item, event_id="2" * 32)


def test_projection_exception_stays_retryable(tmp_path, monkeypatch):
    """投影抛异常 != 数据不合规，不能永久 rejected。

    _project 依赖 get_game_config()，一次配置加载失败就把整批还在七天窗口内
    的会话判死刑太狠——那些数据本身是好的。
    """
    repo, equipment_id = _seed_one(tmp_path, monkeypatch)

    def boom(*_args, **_kwargs):
        raise RuntimeError("游戏配置暂时不可用")

    monkeypatch.setattr(
        "lvjiang.apps.yysls.telemetry.history_source._project", boom)
    assert SOURCE.collect(200) == ()

    # 仍是 unreported，只是记了一次尝试用于退避。
    rows = repo.pending_telemetry()
    assert [int(r["equipment_id"]) for r in rows] == [equipment_id]
    assert rows[0]["state"] == "unreported"
    assert int(rows[0]["attempt_count"]) == 1
    # 记了 last_attempt_at，退避才有依据（见下一个用例）。
    assert rows[0]["last_attempt_at"]


def test_retry_backoff_prevents_head_of_line_blocking(tmp_path, monkeypatch):
    """退避窗口内不再取同一条，避免坏行按 eligible_at 排头占满 LIMIT。"""
    repo, equipment_id = _seed_one(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    repo.mark_attempted(
        [equipment_id],
        at=now.isoformat(timespec="milliseconds"), schema_version=2)

    # 刚尝试过 → 退避窗口内取不到。
    assert repo.pending_telemetry(retry_after_hours=12) == []
    # 不要求退避时照常取得到，说明过滤只由 retry_after_hours 控制。
    assert len(repo.pending_telemetry()) == 1
    # 退避到期后重新可取。
    assert len(repo.pending_telemetry(
        now=now + timedelta(hours=13), retry_after_hours=12)) == 1


def test_schema_violation_is_rejected_permanently(tmp_path, monkeypatch):
    """_project 主动返回 None 才是"确定不合规"，重试多少次都一样，判死刑。"""
    repo, _equipment_id = _seed_one(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "lvjiang.apps.yysls.telemetry.history_source._project",
        lambda *_a, **_kw: None)

    assert SOURCE.collect(200) == ()
    assert repo.pending_telemetry() == []

