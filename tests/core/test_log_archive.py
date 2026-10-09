"""主日志归档：只搬早于当天的日志，当天与无关文件保持原位。"""
from datetime import date
from pathlib import Path

from lvjiang.core.log_files import archive_old_logs


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_archives_previous_days_grouped_by_month(tmp_path):
    logs = tmp_path / "logs"
    _write(logs / "lvjiang_2026-10-08.log", "八号\n")
    _write(logs / "lvjiang_2026-10-03.log", "三号\n")

    archived = archive_old_logs(logs, today=date(2026, 10, 9))

    oct_target = logs / "logs" / "2026-10" / "lvjiang_2026-10-08.log"
    early_target = logs / "logs" / "2026-10" / "lvjiang_2026-10-03.log"
    assert sorted(archived) == sorted([oct_target, early_target])
    assert oct_target.read_text(encoding="utf-8") == "八号\n"
    assert early_target.read_text(encoding="utf-8") == "三号\n"
    assert not (logs / "lvjiang_2026-10-08.log").exists()
    assert not (logs / "lvjiang_2026-10-03.log").exists()


def test_leaves_today_and_unrelated_files_in_place(tmp_path):
    logs = tmp_path / "logs"
    untouched = [
        logs / "lvjiang_2026-10-09.log",   # 当天，正在写入
        logs / "lvjiang.log",              # 无日期，不符合命名
        logs / "other_2026-10-01.log",     # 非主日志
        logs / "lvjiang_2026-13-32.log",   # 日期非法
    ]
    for path in untouched:
        _write(path, "x\n")

    assert archive_old_logs(logs, today=date(2026, 10, 9)) == []
    for path in untouched:
        assert path.exists()
    assert not (logs / "logs").exists()


def test_appends_into_existing_archive_file(tmp_path):
    logs = tmp_path / "logs"
    target = logs / "logs" / "2026-10" / "lvjiang_2026-10-08.log"
    _write(target, "旧归档\n")
    _write(logs / "lvjiang_2026-10-08.log", "新内容\n")

    archived = archive_old_logs(logs, today=date(2026, 10, 9))

    assert archived == [target]
    assert target.read_text(encoding="utf-8") == "旧归档\n新内容\n"
    assert not (logs / "lvjiang_2026-10-08.log").exists()


def test_missing_directory_is_noop(tmp_path):
    assert archive_old_logs(tmp_path / "logs", today=date(2026, 10, 9)) == []


def test_deletes_logs_older_than_retention(tmp_path):
    logs = tmp_path / "logs"
    expired_root = logs / "lvjiang_2026-10-02.log"
    expired_archive = logs / "logs" / "2026-09" / "lvjiang_2026-09-30.log"
    _write(expired_root, "过期\n")
    _write(expired_archive, "更早\n")
    _write(logs / "lvjiang_2026-10-03.log", "仍保留\n")

    archived = archive_old_logs(logs, today=date(2026, 10, 9))

    kept = logs / "logs" / "2026-10" / "lvjiang_2026-10-03.log"
    assert archived == [kept]
    assert kept.read_text(encoding="utf-8") == "仍保留\n"
    assert not expired_root.exists()
    assert not expired_archive.exists()
    assert not (logs / "logs" / "2026-09").exists()


def test_does_not_append_source_already_present_in_archive(tmp_path):
    logs = tmp_path / "logs"
    target = logs / "logs" / "2026-10" / "lvjiang_2026-10-08.log"
    _write(target, "旧归档\n新内容\n")
    _write(logs / "lvjiang_2026-10-08.log", "新内容\n")

    archived = archive_old_logs(logs, today=date(2026, 10, 9))

    assert archived == [target]
    assert target.read_text(encoding="utf-8") == "旧归档\n新内容\n"
    assert not (logs / "lvjiang_2026-10-08.log").exists()
