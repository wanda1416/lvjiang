from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace

from lvjiang.core.daily_history import (
    TaskHistoryRepository,
    TaskRunSession,
    resolve_history_path,
)
from lvjiang.ui.batch import batch_report as report_module
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.batch.batch_runner import BatchWorker
from lvjiang.ui.main.run_control import RunControlMixin


class _LogText:
    def append(self, _message: str) -> None:
        pass


def test_workflow_result_filename_contains_task_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.constants.OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("lvjiang.constants.PROJECT_ROOT", tmp_path)
    started_at = datetime(2025, 12, 31, 23, 59)
    repository = TaskHistoryRepository(tmp_path / "history.db")
    task = TaskRunSession(
        username="用户甲", task_id="daily", task_name="日常",
        task_scope="daily", params={}, source="single", task_run_id="run-123",
        started_at=started_at, repository=repository, log_root=tmp_path / "logs",
    )
    with task.capture_logs():
        pass
    context = SimpleNamespace(
        engine=SimpleNamespace(run_username="用户甲"),
        metadata={"output_started_at": started_at},
    )
    host = SimpleNamespace(
        _current_engine=SimpleNamespace(run_username="用户甲"),
        log_text=_LogText(),
    )

    path = RunControlMixin._save_workflow_result(
        host, "daily", {"ok": True}, task_run_id="run-123", run_context=context)

    assert path is not None
    assert "run-123" in path.name
    assert path.parent == tmp_path / "用户甲" / "2025-12" / "31"
    assert task.log_path.parent == tmp_path / "logs" / "用户甲" / "2025-12" / "31"
    task.finish(status="completed", result_path=path)
    record = repository.list_task_runs()[0]
    assert resolve_history_path(record.result_path).read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
    assert resolve_history_path(record.log_path).is_file()
    # 旧目录的历史仍由数据库中的原路径打开，不要求搬迁或全目录扫描。
    old_result = tmp_path / "用户甲" / "legacy.json"
    old_result.write_text("{}", encoding="utf-8")
    old_log = tmp_path / "logs" / "用户甲" / "legacy.log"
    old_log.write_text("旧日志", encoding="utf-8")
    repository.create_task_run(replace(
        record, task_run_id="legacy", result_path="用户甲/legacy.json",
        log_path="logs/用户甲/legacy.log",
    ))
    old_record = next(item for item in repository.list_task_runs()
                      if item.task_run_id == "legacy")
    assert resolve_history_path(old_record.result_path).read_text(encoding="utf-8") == "{}"
    assert resolve_history_path(old_record.log_path).read_text(encoding="utf-8") == "旧日志"


def test_batch_result_uses_task_start_date(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.constants.OUTPUT_DIR", tmp_path)
    path = BatchWorker._save_result(
        "用户甲", SimpleNamespace(id="daily"), {"ok": True},
        datetime(2025, 12, 31, 23, 59),
    )
    assert path.parent == tmp_path / "用户甲" / "2025-12" / "31"
    assert path.is_file()


def test_batch_report_filename_contains_batch_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(report_module, "BATCH_REPORT_DIR", tmp_path)
    report = BatchReport(
        "配置", [("daily", "日常")], {}, batch_run_id="batch-456")
    report.start_batch()
    report._start_time = datetime(2025, 12, 31, 23, 59)
    report.start_entry("用户甲", "用户甲")
    report.end_entry()
    report.end_batch()

    path = report.write()

    assert path is not None
    assert "batch-456" in path.name
    assert path.parent == tmp_path / "2025-12" / "31"
