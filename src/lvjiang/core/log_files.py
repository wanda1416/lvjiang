"""桌面端主日志的归档与保留。

当天日志由 ``__main__._configure_logging()`` 的 loguru FileSink 直接写到
``logs/lvjiang_YYYY-MM-DD.log``（文件名来自路径里的 ``{time}`` 占位符，跨午夜
自动换新文件）。本模块在启动时把已经过去、且仍在保留期内的日志按
``logs/logs/YYYY-MM/`` 归位，并删除更早的主日志。归档目录不在 FileSink
的匹配范围内，所以保留期要在这里自己执行，不能再交给 loguru 的 retention。

归档只在启动时触发一次：跨午夜运行时旧文件会暂时留在 ``logs/`` 根，下次启动
搬走，内容不受影响。
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

#: 主日志文件名，例如 lvjiang_2026-10-08.log
_LOG_NAME = re.compile(r"^lvjiang_(\d{4})-(\d{2})-(\d{2})\.log$")

#: 含当天在内保留的日历天数。早于这个窗口的根目录文件和月归档都删除。
LOG_RETENTION_DAYS = 7


def _log_date(name: str) -> date | None:
    """从主日志文件名解析日期；不符合命名或日期非法返回 None。"""
    match = _LOG_NAME.match(name)
    if match is None:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _expired(day: date, today: date) -> bool:
    """``day`` 是否已经超出 7 天保留窗口。

    日志在当天 00:00 换新文件。今天是 10 月 9 日时，10 月 2 日及更早过期，
    10 月 3 日到 9 日保留。
    """
    return day <= today - timedelta(days=LOG_RETENTION_DAYS)


def archive_path(logs_dir: Path, day: date) -> Path:
    """某天日志的归档路径：``logs/logs/YYYY-MM/lvjiang_YYYY-MM-DD.log``"""
    return logs_dir / "logs" / f"{day:%Y-%m}" / f"lvjiang_{day:%Y-%m-%d}.log"


def _merge_into(src: Path, dst: Path) -> None:
    """把 ``src`` 归入 ``dst``。

    目标不存在时直接改名。目标已存在时先写成同目录临时文件再替换，
    成功之后才删除源文件。源内容已经是目标末尾时只删除源文件，避免
    上次替换成功但删除失败后，下一次启动把同一段再追加一遍。
    """
    try:
        src_bytes = src.read_bytes()
    except FileNotFoundError:
        return
    if not dst.exists():
        try:
            os.replace(src, dst)
        except FileNotFoundError:
            return
        return
    dst_bytes = dst.read_bytes()
    if dst_bytes.endswith(src_bytes):
        src.unlink(missing_ok=True)
        return
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=f".{dst.name}.", suffix=".merging")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(dst_bytes + src_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, dst)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)


def _remove_empty_parents(path: Path, stop: Path) -> None:
    """从 ``path`` 的父目录往上删空目录，停在 ``stop``（不含）。"""
    current = path.parent
    stop = stop.resolve()
    while current.resolve() != stop and current.is_dir():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def archive_old_logs(logs_dir: Path, today: date | None = None) -> list[Path]:
    """把保留期内、早于 ``today`` 的主日志搬进按月归档目录，并删除过期日志。

    Args:
        logs_dir: 主日志所在目录（``<root>/logs``）。
        today: 判定基准，缺省为本地当天；显式传入便于测试。

    Returns:
        实际写入的归档目标路径。过期文件被删除，不出现在结果里。
        ``logs_dir`` 不存在时为空列表。
    """
    if today is None:
        today = date.today()

    archived: list[Path] = []
    if not logs_dir.is_dir():
        return archived

    for path in sorted(logs_dir.glob("lvjiang_*.log")):
        if not path.is_file():
            continue
        day = _log_date(path.name)
        if day is None or day >= today:
            continue
        if _expired(day, today):
            path.unlink()
            continue
        target = archive_path(logs_dir, day)
        target.parent.mkdir(parents=True, exist_ok=True)
        _merge_into(path, target)
        archived.append(target)

    archive_root = logs_dir / "logs"
    if archive_root.is_dir():
        for path in sorted(archive_root.glob("*/*.log")):
            if not path.is_file():
                continue
            day = _log_date(path.name)
            if day is None or not _expired(day, today):
                continue
            path.unlink()
            _remove_empty_parents(path, logs_dir)
    return archived
