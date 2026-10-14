"""历史回传保护数据归属、去重、附件完整性与独立选择。"""
import json
import sqlite3
import zipfile
from contextlib import closing
from dataclasses import replace
from datetime import datetime

import pytest

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


@pytest.fixture
def histories(tmp_path, monkeypatch):
    from lvjiang import apps
    from lvjiang.apps import yysls
    monkeypatch.setattr(apps, "get_registered_app_ids", lambda: ("yysls",))
    monkeypatch.setattr(apps, "load_app", lambda _: yysls.hooks)
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
    result = phone / "config/session/output/手机用户/2026-01/01/result.json"
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


def test_logs_are_independent_and_missing_files_are_reported(histories, tmp_path):
    phone, pc, task, _ = histories
    archive = tmp_path / "logs.zip"
    # 单独执行在库中以 NULL 表示没有所属批次，不能被终态查询漏掉。
    with closing(sqlite3.connect(phone / "config/session/daily_history.db")) as conn, conn:
        conn.execute("UPDATE task_runs SET batch_run_id=NULL WHERE task_run_id=?", (task.task_run_id,))
    export_history(phone, archive, ["logs"])
    assert import_history(pc, archive, ["logs"])["files"] == 1
    assert not (pc / "config/session/daily_history.db").exists()
    task.log_path.unlink()
    export_history(phone, archive, ["tasks", "logs"])
    outcome = import_history(pc, archive, ["tasks", "logs"])
    assert len(outcome["missing"]) == 1
    assert outcome["imported"]["tasks"] == 2


def test_same_path_different_content_never_overwrites_pc(histories, tmp_path):
    phone, pc, task, _ = histories
    target = pc / task.log_path.relative_to(phone)
    target.parent.mkdir(parents=True)
    target.write_text("PC 原内容", encoding="utf-8")
    archive = tmp_path / "history.zip"
    export_history(phone, archive, ["tasks", "logs"])
    with pytest.raises(ValueError, match="同名附件"):
        import_history(pc, archive, ["tasks", "logs"])
    assert target.read_text(encoding="utf-8") == "PC 原内容"
    assert not (pc / "config/session/daily_history.db").exists()


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


def test_archive_rejects_unrequested_category_and_path_escape(histories, tmp_path):
    phone, pc, _, _ = histories
    archive = tmp_path / "history.zip"
    export_history(phone, archive, ["logs"])
    with pytest.raises(ValueError, match="类别"):
        import_history(pc, archive, ["tasks"])
    with zipfile.ZipFile(archive) as package:
        payload = json.loads(package.read("manifest.json"))
    payload["files"] = {"../outside.txt": "invalid"}
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", json.dumps(payload))
        package.writestr("../outside.txt", "bad")
    with pytest.raises(ValueError, match="路径"):
        import_history(pc, archive, ["logs"])
    assert not (pc.parent / "outside.txt").exists()


def test_rpc_retry_and_cancel_do_not_import_partial_history(histories, monkeypatch):
    from lvjiang.core.android.history_transfer import pull_history
    from lvjiang.core.ondevice import history_transfer as device
    from lvjiang.core.ondevice import task_runner

    _, pc, _, _ = histories
    monkeypatch.setattr("lvjiang.core.ondevice.plugins.ensure_loaded", lambda: None)
    monkeypatch.setattr(task_runner, "is_running", lambda: False)

    class Agent:
        status = {"history_protocol": 1}
        fail_chunk = True

        def call(self, operation, timeout=None, **params):
            if operation == "history_chunk" and self.fail_chunk:
                raise ConnectionError("模拟断线")
            response = json.loads(device.request(operation.removeprefix("history_"), json.dumps(params)))
            if not response["ok"]:
                raise RuntimeError(response["error"])
            return response, b""

    agent = Agent()
    with pytest.raises(ConnectionError):
        pull_history(agent, pc, ["tasks"])
    assert not (pc / "config/session/daily_history.db").exists()
    agent.fail_chunk = False
    with pytest.raises(RuntimeError, match="取消"):
        pull_history(agent, pc, ["tasks"], cancelled=lambda: True)
    assert not (pc / "config/session/daily_history.db").exists()
    assert pull_history(agent, pc, ["tasks"])["imported"]["tasks"] == 2
    assert pull_history(agent, pc, ["tasks"])["imported"]["tasks"] == 0
    monkeypatch.setattr(task_runner, "is_running", lambda: True)
    with pytest.raises(RuntimeError, match="结束手机任务"):
        pull_history(agent, pc, ["tasks"])
