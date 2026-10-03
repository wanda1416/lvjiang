from __future__ import annotations

from types import SimpleNamespace

from lvjiang.ui.batch import batch_report as report_module
from lvjiang.ui.batch.batch_report import BatchReport
from lvjiang.ui.main.run_control import RunControlMixin


class _LogText:
    def append(self, _message: str) -> None:
        pass


def test_workflow_result_filename_contains_task_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.constants.OUTPUT_DIR", tmp_path)
    host = SimpleNamespace(
        _current_engine=SimpleNamespace(run_username="用户甲"),
        log_text=_LogText(),
    )

    path = RunControlMixin._save_workflow_result(
        host, "daily", {"ok": True}, task_run_id="run-123")

    assert path is not None
    assert "run-123" in path.name
    assert path.parent == tmp_path / "用户甲"


def test_batch_report_filename_contains_batch_run_id(tmp_path, monkeypatch):
    monkeypatch.setattr(report_module, "BATCH_REPORT_DIR", tmp_path)
    report = BatchReport(
        "配置", [("daily", "日常")], {}, batch_run_id="batch-456")
    report.start_batch()
    report.start_entry("用户甲", "用户甲")
    report.end_entry()
    report.end_batch()

    path = report.write()

    assert path is not None
    assert "batch-456" in path.name
