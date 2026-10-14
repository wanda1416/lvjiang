"""终态历史单向归集：声明式表范围、稳定业务键去重及原相对路径附件。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .._version import __version__
from .fs_util import atomic_write_bytes
from .json_files import formatted_json_bytes

MAX_BYTES = 256 << 20
FORMAT = 1
_IMPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class HistoryTable:
    name: str
    keys: tuple[str, ...]
    selection: str
    paths: tuple[str, ...] = ()
    omitted: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoryChannel:
    key: str
    label: str
    database: str
    initialize: Callable[[Path], Any]
    tables: tuple[HistoryTable, ...]
    unattributed: str = ""


def history_channels() -> dict[str, HistoryChannel]:
    from ..apps import get_registered_app_ids, load_app
    from .daily_history import TaskHistoryRepository

    batches = "status != 'running' AND finished_at != '' AND target_id='ondevice' AND target_kind='android'"
    tasks = ("source='ondevice' AND status != 'running' AND finished_at != '' "
             f"AND (batch_run_id IS NULL OR batch_run_id='' OR batch_run_id IN (SELECT batch_run_id FROM batch_runs WHERE {batches}))")
    channels = {"tasks": HistoryChannel("tasks", "任务历史", "daily_history.db", TaskHistoryRepository, (
        HistoryTable("batch_runs", ("batch_run_id",), batches, ("report_path",)),
        HistoryTable("task_runs", ("task_run_id",), tasks, ("result_path",)),
    ))}
    for app_id in get_registered_app_ids():
        factory = load_app(app_id).history_channels
        if factory is not None:
            for channel in factory():
                if channel.key in channels:
                    raise ValueError(f"重复的历史类别：{channel.key}")
                channels[channel.key] = channel
    return channels


def _attachment(root: Path, name: str, *, legacy: bool = False) -> tuple[str, Path]:
    # 旧调律记录曾存绝对路径，仅允许将本实例内的路径规范化。
    path = Path(name)
    if path.is_absolute() and legacy:
        name = path.resolve().relative_to(root.resolve()).as_posix()
    relative = PurePosixPath(name)
    if (not name or relative.is_absolute() or ".." in relative.parts or "\\" in name
            or relative.as_posix() != name
            or not (name.startswith("logs/daily/") or name.startswith("logs/batch/")
                    or name.startswith("logs/tuning/") or name.startswith("config/session/output/"))):
        raise ValueError(f"历史附件路径不在允许范围：{name}")
    path = root.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("历史附件不能指向客户端目录外")
    return name, path


def _rows(root: Path, channel: HistoryChannel) -> dict[str, list[dict]]:
    path = root / "config/session" / channel.database
    if not path.is_file():
        return {table.name: [] for table in channel.tables}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        return {table.name: [dict(row) for row in conn.execute(
            f'SELECT * FROM "{table.name}" WHERE {table.selection}',
            {"root_prefix": root.resolve().as_posix() + "/%"})]
            for table in channel.tables}


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(192 << 10):
            digest.update(chunk)
    return digest.hexdigest()


def export_history(root: Path, destination: Path, selected: list[str]) -> dict:
    channels = history_channels()
    if not selected or len(set(selected)) != len(selected) or set(selected) - {*channels, "logs"}:
        raise ValueError("请选择有效的回传类别")
    payload: dict[str, Any] = {"format": FORMAT, "app_version": __version__, "selected": selected,
                               "channels": {}, "files": {}, "missing": [], "unattributed": {}}
    files: dict[str, Path] = {}
    for key, channel in channels.items():
        if key not in selected and not (key == "tasks" and "logs" in selected):
            continue
        tables = _rows(root, channel)
        database = root / "config/session" / channel.database
        if channel.unattributed and database.is_file():
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                count = conn.execute(channel.unattributed, {
                    "root_prefix": root.resolve().as_posix() + "/%"}).fetchone()[0]
            if count:
                payload["unattributed"][key] = count
        for table in channel.tables:
            for row in tables[table.name]:
                for column in table.omitted:
                    row.pop(column, None)
                columns = list(table.paths) if key in selected else []
                if key == "tasks" and table.name == "task_runs" and "logs" in selected:
                    columns.append("log_path")
                for column in columns:
                    if row.get(column):
                        name, path = _attachment(root, row[column], legacy=True)
                        row[column] = name
                        if path.is_file():
                            files[name] = path
                        else:
                            payload["missing"].append(name)
                # 未选日志时仍规范化记录引用，但不携带日志文件。
                if key == "tasks" and row.get("log_path"):
                    row["log_path"] = _attachment(root, row["log_path"], legacy=True)[0]
        if key in selected:
            payload["channels"][key] = tables
    total = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            size = path.stat().st_size
            total += size
            if total > MAX_BYTES:
                raise ValueError("回传附件超过 256 MiB，请分别回传历史和日志")
            digest = hashlib.sha256()
            with path.open("rb") as source, archive.open(name, "w") as target:
                while chunk := source.read(192 << 10):
                    digest.update(chunk)
                    target.write(chunk)
            payload["files"][name] = digest.hexdigest()
        manifest = formatted_json_bytes(payload)
        if total + len(manifest) > MAX_BYTES:
            raise ValueError("回传包超过 256 MiB")
        archive.writestr("manifest.json", manifest)
    if destination.stat().st_size > MAX_BYTES:
        raise ValueError("回传包超过 256 MiB")
    return {"size": destination.stat().st_size, "sha256": _file_digest(destination)}


def _merge_channel(root: Path, channel: HistoryChannel, tables: dict) -> int:
    if set(tables) != {table.name for table in channel.tables}:
        raise ValueError(f"{channel.label} 表范围不一致，请更新两端程序")
    path = root / "config/session" / channel.database
    channel.initialize(path)
    count = 0
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for table in channel.tables:
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table.name}")')
                       if row[1] not in table.omitted]
            where = " AND ".join(f'"{key}"=?' for key in table.keys)
            for row in tables[table.name]:
                if set(row) != set(columns):
                    raise ValueError(f"{channel.label} 字段不一致，请更新两端程序")
                for column in (*table.paths, *(('log_path',) if 'log_path' in columns else ())):
                    if row[column]:
                        _attachment(root, row[column])
                existing = conn.execute(f'SELECT * FROM "{table.name}" WHERE {where}',
                                        [row[key] for key in table.keys]).fetchone()
                if existing is not None:
                    if any(existing[column] != row[column] for column in columns):
                        raise ValueError(f"{channel.label} 相同记录 ID 内容不同，已保留 PC 原记录")
                    continue
                names = ",".join(f'"{column}"' for column in columns)
                placeholders = ",".join("?" for _ in columns)
                conn.execute(f'INSERT INTO "{table.name}" ({names}) VALUES ({placeholders})',
                             [row[column] for column in columns])
                count += 1
    return count


def import_history(root: Path, archive_path: Path, selected: list[str]) -> dict:
    from .access import require_primary_write
    require_primary_write()
    channels = history_channels()
    if not selected or set(selected) - {*channels, "logs"}:
        raise ValueError("回传类别不合法")
    result: dict[str, Any] = {"imported": {}, "files": 0, "missing": [], "errors": {}}
    with _IMPORT_LOCK, zipfile.ZipFile(archive_path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_BYTES:
            raise ValueError("回传包展开后超过 256 MiB")
        payload = json.loads(archive.read("manifest.json"))
        if payload.get("format") != FORMAT or payload.get("app_version") != __version__:
            raise ValueError("PC 与 APK 历史协议或运行版本不一致，请更新两端程序")
        if set(payload["selected"]) != set(selected) or set(payload["channels"]) != set(selected) - {"logs"}:
            raise ValueError("回传类别与请求不一致")
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {"manifest.json", *payload["files"]}:
            raise ValueError("回传文件清单不一致")
        # 校验整个包及同名文件，之后才落盘；不替换任何已有不同内容。
        for name, digest in payload["files"].items():
            _, path = _attachment(root, name)
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"回传附件校验失败：{name}")
            if path.exists() and _file_digest(path) != digest:
                raise ValueError(f"同名附件内容不同，已保留 PC 原文件：{name}")
        for name in payload["files"]:
            _, path = _attachment(root, name)
            if not path.exists():
                atomic_write_bytes(path, archive.read(name), prefix="history-")
                result["files"] += 1
        # 各类别独立事务：插件历史失败不回滚已完成的任务历史，重试自然去重。
        for key, tables in payload["channels"].items():
            try:
                result["imported"][key] = _merge_channel(root, channels[key], tables)
            except (ValueError, sqlite3.Error) as exc:
                result["errors"][key] = str(exc)
        result["missing"] = payload["missing"]
        result["unattributed"] = payload["unattributed"]
    return result
