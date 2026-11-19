"""日志片段：从最近若干天的日志里取尾部，脱敏后作为附件。

为什么是「尾部若干行」而不是整份日志文件：

* 一次任务的问题现场就在最近几百行里，整份 7 天日志既大又几乎全是无关内容；
* 单值要落进 D1（900KB 硬线），1.5MB 原文读进来、取尾部 2000 行、再 gzip，
  实际通常只有 100~200KB，稳定落在上限内；
* 截断必须**显式告知**：文件头写一行说明，作者才知道自己看到的不完整。

只读，不改任何一个日志文件。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .limits import LOG_DAY_WINDOW, LOG_TAIL_LINES, LOG_TAIL_MAX_BYTES
from .redact import redact_text

_LOG_NAME = re.compile(r"^lvjiang_(\d{4})-(\d{2})-(\d{2})\.log$")


@dataclass(frozen=True)
class LogExcerpt:
    """一段可供提交的日志。"""

    day: date
    name: str
    text: str
    truncated: bool
    bytes_read: int


def log_dir() -> Path:
    """主日志目录（``<root>/logs``，跨午夜归档到其下的 ``logs/YYYY-MM/``）。"""
    from ...constants import PROJECT_ROOT
    return PROJECT_ROOT / "logs"


def _day_of(path: Path) -> date | None:
    match = _LOG_NAME.match(path.name)
    if match is None:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def available_days(*, window: int = LOG_DAY_WINDOW, today: date | None = None) -> list[date]:
    """最近 ``window`` 天内实际存在的日志日期，倒序（最新在前）。"""
    today = today or date.today()
    root = log_dir()
    days: set[date] = set()
    directories = [root]
    archive_root = root / "logs"
    if archive_root.is_dir():
        directories.extend(path for path in archive_root.iterdir() if path.is_dir())
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in directory.glob("lvjiang_*.log"):
            day = _day_of(path)
            if day is not None and today - timedelta(days=window) < day <= today:
                days.add(day)
    return sorted(days, reverse=True)


def path_for(day: date) -> Path | None:
    """某天日志的实际路径（先看根目录，再看按月归档）。"""
    root = log_dir()
    direct = root / f"lvjiang_{day:%Y-%m-%d}.log"
    if direct.is_file():
        return direct
    archived = root / "logs" / f"{day:%Y-%m}" / direct.name
    return archived if archived.is_file() else None


def read_excerpt(
    day: date | None = None,
    *,
    max_lines: int = LOG_TAIL_LINES,
    max_bytes: int = LOG_TAIL_MAX_BYTES,
) -> LogExcerpt | None:
    """取某天（缺省为最新一天）日志的尾部片段并脱敏。

    Returns:
        片段对象；当天没有日志（或路径不可读）时返回 ``None``——调用方据此把
        「附日志」勾选框降级为不可用，而不是提交一个空附件。
    """
    if day is None:
        days = available_days()
        if not days:
            return None
        day = days[0]
    path = path_for(day)
    if path is None:
        return None
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                raw = handle.read(max_bytes)
                # 掐掉被截断的残余首行，避免半行看起来像识别错误
                newline = raw.find(b"\n")
                if newline >= 0:
                    raw = raw[newline + 1:]
                truncated = True
            else:
                raw = handle.read()
                truncated = False
    except OSError:
        return None

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        truncated = True
    body = redact_text("\n".join(lines))
    if truncated:
        body = (f"# 日志已截断：仅保留最近 {max_lines} 行（当天共 {size} 字节）\n"
                + body)
    return LogExcerpt(day=day, name=f"lvjiang_{day:%Y-%m-%d}.log",
                      text=body, truncated=truncated, bytes_read=len(raw))
