"""调律历史接入宿主历史回传：yysls 声明的 HistoryChannel 的端到端行为。

宿主机制（声明式表范围、去重、附件、独立类别）由
``tests/core/test_history_transfer.py`` 用假插件覆盖；这里只验证调律历史自己的
声明：表范围、遥测队列不随回传重建、报告附件目录 ``logs/tuning/``。
"""
import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime

import pytest

from lvjiang.apps import yysls
from lvjiang.apps.yysls.core.tuning_history.models import (
    TuningEquipmentResult,
    TuningRunSummary,
)
from lvjiang.apps.yysls.core.tuning_history.repository import TuningHistoryRepository
from lvjiang.core.daily_history import (
    BatchRunSession,
    TaskHistoryRepository,
    TaskRunSession,
)
from lvjiang.core.history_transfer import export_history, import_history
from tests.fake_plugin import use_registered_hooks


@pytest.fixture
def histories(tmp_path, monkeypatch):
    use_registered_hooks(monkeypatch, yysls.hooks)
    phone = tmp_path / "phone"
    pc = tmp_path / "pc"
    monkeypatch.setattr("lvjiang.constants.PROJECT_ROOT", phone)
    repo = TaskHistoryRepository(phone / "config/session/daily_history.db")
    batch = BatchRunSession(config_name="手机批次", input_snapshot={}, repository=repo,
                            target_id="ondevice", target_kind="android")
    task = TaskRunSession(username="手机用户", task_id="daily", task_name="任务", task_scope="daily",
                          params={}, source="ondevice", repository=repo, batch_run_id=batch.batch_run_id,
                          started_at=datetime(2026, 1, 1), target_id="ondevice", target_kind="android",
                          log_root=phone / "logs/daily")
    task.log_path.write_text("手机日志\n", encoding="utf-8")
    result = phone / "logs/output/手机用户/2026-01/01/result.json"
    result.parent.mkdir(parents=True)
    result.write_text('{"结果": 1}\n', encoding="utf-8")
    task.finish(status="completed", result_path=result)
    report = phone / "logs/batch/report.md"
    report.parent.mkdir(parents=True)
    report.write_text("批次报告", encoding="utf-8")
    batch.finish(status="completed", report_path=report)
    tuning = TuningHistoryRepository(phone / "config/session/tuning_history.db")
    doc = phone / "logs/tuning/report.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("调律说明", encoding="utf-8")
    summary = TuningRunSummary(run_id="tuning-phone", started_at="2026-01-01T00:00:00+00:00",
                               finished_at="2026-01-01T00:01:00+00:00", username="手机用户",
                               status="completed", stop_reason="完成", task_run_id=task.task_run_id,
                               target_id="ondevice", target_kind="android", markdown_path=str(doc))
    tuning.create_run(summary)
    tuning.save_equipment(summary.run_id, TuningEquipmentResult(
        equipment_id=1, slot_key="weapon", name="示例", type="weapon", level=1, quality="普通",
        final_affixes=(), final_rating="A", rounds=2, result="tuned", reason="完成",
        tuning_started_at=summary.started_at), event_id="phone-event")
    # 旧下发带来的 PC 记录不能作为手机独立执行历史回传。
    record = repo.list_task_runs()[0]
    repo.create_task_run(replace(record, task_run_id="pc-only", source="single", batch_run_id=""))
    yield phone, pc, task, batch



def test_roundtrip_keeps_details_and_paths_without_repeating_telemetry(histories, tmp_path):
    phone, pc, task, batch = histories
    archive = tmp_path / "history.zip"
    selected = ["tasks", "yysls.tuning", "logs"]
    export_history(phone, archive, selected)
    first = import_history(pc, archive, selected)
    assert not first["errors"] and not first["missing"]
    assert first["imported"] == {"tasks": 2, "yysls.tuning": 2}
    assert first["files"] == 4
    again = import_history(pc, archive, selected)
    assert again["imported"] == {"tasks": 0, "yysls.tuning": 0}
    assert again["files"] == 0
    records = TaskHistoryRepository(pc / "config/session/daily_history.db").list_task_runs()
    assert [r.task_run_id for r in records] == [task.task_run_id]
    assert records[0].batch_run_id == batch.batch_run_id
    assert (pc / records[0].log_path).read_text(encoding="utf-8") == "手机日志\n"
    assert json.loads((pc / records[0].result_path).read_text(encoding="utf-8")) == {"结果": 1}
    with closing(sqlite3.connect(pc / "config/session/tuning_history.db")) as conn:
        assert conn.execute("SELECT sequence_id, rounds FROM tuning_equipment").fetchall() == [(1, 2)]
        assert conn.execute("SELECT COUNT(*) FROM telemetry_deliveries").fetchone()[0] == 0
        assert conn.execute("SELECT markdown_path FROM tuning_runs").fetchone()[0] == "logs/tuning/report.md"
    assert not (pc / "config/session/users").exists()
    assert not (pc / "config/session/profile.db").exists()


def test_unfinished_batch_waits_and_conflicting_category_does_not_block_others(histories, tmp_path):
    phone, pc, _, batch = histories
    archive = tmp_path / "history.zip"
    with closing(sqlite3.connect(phone / "config/session/daily_history.db")) as conn, conn:
        conn.execute("UPDATE batch_runs SET status='running', finished_at='' WHERE batch_run_id=?", (batch.batch_run_id,))
    export_history(phone, archive, ["tasks", "yysls.tuning"])
    outcome = import_history(pc, archive, ["tasks", "yysls.tuning"])
    assert outcome["imported"]["tasks"] == 0
    assert outcome["imported"]["yysls.tuning"] == 2
    batch.finish(status="completed")
    export_history(phone, archive, ["tasks", "yysls.tuning"])
    with closing(sqlite3.connect(pc / "config/session/tuning_history.db")) as conn, conn:
        conn.execute("UPDATE tuning_runs SET stop_reason='PC 保留'")
    outcome = import_history(pc, archive, ["tasks", "yysls.tuning"])
    assert outcome["imported"]["tasks"] == 2
    assert "yysls.tuning" in outcome["errors"]


def test_tuning_channel_declares_its_attachment_prefix(tmp_path):
    """宿主只认识自己的附件落点；调律报告目录由调律 HistoryChannel 自己声明。"""
    from lvjiang.apps.yysls.core.tuning_history.transfer import (
        history_channels as tuning,
    )
    from lvjiang.core.history_transfer import _attachment, attachment_prefixes

    host_only = attachment_prefixes({})
    assert "logs/tuning/" not in host_only
    with pytest.raises(ValueError):
        _attachment(tmp_path, "logs/tuning/report.md", host_only)

    declared = {channel.key: channel for channel in tuning()}
    prefixes = attachment_prefixes(declared)
    assert "logs/tuning/" in prefixes
    assert _attachment(tmp_path, "logs/tuning/report.md", prefixes)[0] == "logs/tuning/report.md"


def test_plugin_hooks_expose_tuning_history_channel():
    """插件通过 AppHooks.history_channels 声明调律历史类别。"""
    assert yysls.hooks.history_channels is not None
    assert [channel.key for channel in yysls.hooks.history_channels()] == ["yysls.tuning"]
