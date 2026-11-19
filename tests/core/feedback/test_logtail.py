"""日志片段采集的回归测试。

保护的真实场景：一次任务失败后用户勾选「附加最近日志」。这里出错会以两种方式
伤害用户——上传了一个空/错天的日志（作者无法定位），或把整份 7 天日志塞进请求
（超过服务端单值上限，最后 413）。两种都只在提交那一刻暴露，所以必须测。
"""
from __future__ import annotations

import gzip
from datetime import date

import pytest

from lvjiang.core.feedback import logtail
from lvjiang.core.feedback.limits import MAX_ATTACHMENT_BYTES


@pytest.fixture()
def logs(tmp_path, monkeypatch):
    """把日志根目录指向临时目录（常量在导入期算好，这里直接替换函数）。"""
    root = tmp_path / "logs"
    root.mkdir()
    monkeypatch.setattr(logtail, "log_dir", lambda: root)
    return root


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_available_days_reads_root_and_archive(logs):
    _write(logs / "lvjiang_2026-11-19.log", "today")
    _write(logs / "logs" / "2026-11" / "lvjiang_2026-11-18.log", "yesterday")
    # 超出 7 天窗口的不出现：保留期之外的文件本来也已被归档逻辑删除
    _write(logs / "lvjiang_2026-01-01.log", "ancient")
    days = logtail.available_days(today=date(2026, 11, 19))
    assert days == [date(2026, 11, 19), date(2026, 11, 18)]


def test_excerpt_keeps_tail_and_marks_truncation(logs):
    body = "\n".join(f"line {index}" for index in range(5000))
    _write(logs / "lvjiang_2026-11-19.log", body)
    excerpt = logtail.read_excerpt(date(2026, 11, 19), max_lines=100)
    assert excerpt is not None
    assert excerpt.truncated is True
    lines = excerpt.text.splitlines()
    assert lines[0].startswith("# 日志已截断")
    assert "line 4999" in excerpt.text      # 尾部保留
    assert "line 0\n" not in excerpt.text   # 头部丢弃


def test_excerpt_without_logs_returns_none(logs):
    assert logtail.read_excerpt(date(2026, 11, 19)) is None
    assert logtail.available_days(today=date(2026, 11, 19)) == []


def test_byte_truncation_and_gzip_stay_under_attachment_limit(logs):
    """1.5MB 原文截断后，gzip 仍要稳稳落在服务端单件上限内。"""
    line = "2026-11-19 09:00:01 | INFO | 识别结果 命中 坐标 (1234, 567)\n"
    _write(logs / "lvjiang_2026-11-19.log", line * 60_000)
    excerpt = logtail.read_excerpt(date(2026, 11, 19))
    assert excerpt is not None
    assert excerpt.truncated is True
    packed = gzip.compress(excerpt.text.encode("utf-8"), compresslevel=6)
    assert len(packed) < MAX_ATTACHMENT_BYTES
    # 截断点不能留下半行：以说明行开头之后必须都是完整日志行
    assert excerpt.text.splitlines()[1].startswith("2026-11-19 09:00:01")


def test_excerpt_is_redacted(logs):
    _write(logs / "lvjiang_2026-11-19.log",
           r"读取 C:\Users\alice\lvjiang\config\session\account.json" + "\n")
    excerpt = logtail.read_excerpt(date(2026, 11, 19))
    assert excerpt is not None
    assert "alice" not in excerpt.text
