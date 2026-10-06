"""PC→Android 的配置快照；不传输 Python 源码，不回写 PC 数据。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .._version import __version__
from .config.resolver import ConfigResolver
from .layout_config import load_layout_doc
from .user_config import is_valid_username

FORMAT_VERSION = 1
MAX_BUNDLE_BYTES = 256 << 20
_EXCLUDED_DIRS = {".git", ".locks", ".lock", "__pycache__", "diagnostics", "output", "avatars"}


def build_offline_bundle(root: Path, destination: Path, *, username: str, layout: str) -> dict:
    """构造配置副本和 SQLite backup 快照；不修改 PC 的活动选择或数据库。"""
    if not is_valid_username(username) or not layout:
        raise ValueError("请选择有效执行用户和安卓布局")
    config = root / "config"
    layout_doc = load_layout_doc(ConfigResolver(
        system_dir=config / "system", local_dir=config / "local", remote_dir=config / "remote",
    ))
    entries = layout_doc["layouts"]
    if layout not in entries:
        raise ValueError("所选同步布局不存在")
    layout_scope = {layout}
    if entries[layout].get("extends"):
        layout_scope.add(entries[layout]["extends"])
    if not (config / "session/users" / f"{username}.json").is_file():
        raise ValueError("执行用户资料不存在")
    manifest: dict[str, Any] = {
        "format": FORMAT_VERSION, "app_version": __version__,
        "username": username, "layout": layout,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {},
    }
    with tempfile.TemporaryDirectory(prefix="lvjiang-offline-db-") as temp:
        database = config / "session/profile.db"
        snapshot = Path(temp) / "profile.db"
        if database.is_file():
            # SQLite 的连接上下文只处理事务，不释放文件句柄。
            # 必须在读取快照和清理临时目录前关闭，Windows 不允许删除打开的 DB。
            with (closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source,
                  closing(sqlite3.connect(snapshot)) as target):
                source.backup(target)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def add(name: str, data: bytes) -> None:
                manifest["files"][name] = hashlib.sha256(data).hexdigest()
                archive.writestr(name, data)

            for path in sorted(config.rglob("*")):
                rel = path.relative_to(config)
                if (not path.is_file() or path.is_symlink()
                        or any(part in _EXCLUDED_DIRS or part.startswith(".") for part in rel.parts)
                        or rel.parts[0] not in {"system", "local", "remote", "session"}
                        or path.name.endswith((".lock", ".tmp", ".db-wal", ".db-shm", ".db-journal"))
                        or rel.as_posix() in {"session/profile.db", "session/offline.json", "local/license.txt"}):
                    continue
                if len(rel.parts) >= 3 and rel.parts[1] == "layouts" and rel.parts[2] not in layout_scope:
                    continue
                data = path.read_bytes()
                if rel.as_posix() in {"system/layouts.yaml", "local/layouts.yaml", "remote/layouts.yaml"}:
                    doc = yaml.safe_load(data) or {}
                    layouts = doc.get("layouts") or {}
                    doc["layouts"] = {key: value for key, value in layouts.items() if key in layout_scope}
                    deleted = [key for key in layouts.get("__deleted__", []) if key in layout_scope]
                    if deleted:
                        doc["layouts"]["__deleted__"] = deleted
                    data = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).encode("utf-8")
                if rel.as_posix() == "session/session.json":
                    session = json.loads(data)
                    session.setdefault("actives", {}).update(user=username, layout=layout)
                    session.setdefault("settings", {})["env"] = "android"
                    data = json.dumps(session, ensure_ascii=False).encode("utf-8")
                add("config/" + rel.as_posix(), data)
            if snapshot.is_file():
                add("config/session/profile.db", snapshot.read_bytes())
            # 不携带 PC 旧的同步状态；此记录只属于这次单向快照。
            summary = {key: value for key, value in manifest.items() if key != "files"}
            add("config/session/offline.json", json.dumps(summary, ensure_ascii=False).encode())
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    if destination.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("同步包超过 256 MiB，请移除无关的大文件")
    return summary


def install_offline_bundle(
    archive_path: Path, root: Path, *, on_applied: Callable[[], None] | None = None,
) -> dict:
    """校验后交换 config 目录；保留上一份备份，交换失败恢复原配置。"""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".offline-stage-", dir=root))
    target = root / "config"
    previous = root / "offline-backup/config"
    swapped = False
    try:
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT_VERSION or manifest.get("app_version") != __version__:
                raise ValueError("PC 与 APK 运行版本不一致，请先更新 APK")
            if not is_valid_username(manifest.get("username", "")) or not manifest.get("layout"):
                raise ValueError("同步包缺少有效执行用户或布局")
            files = manifest["files"]
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != {"manifest.json", *files}:
                raise ValueError("同步包文件清单不一致")
            if sum(info.file_size for info in archive.infolist()) > MAX_BUNDLE_BYTES:
                raise ValueError("同步包展开后超过 256 MiB")
            for name, digest in files.items():
                rel = PurePosixPath(name)
                if (rel.is_absolute() or ".." in rel.parts or "\\" in name
                        or len(rel.parts) < 3 or rel.parts[0] != "config"
                        or rel.parts[1] not in {"system", "local", "remote", "session"}
                        or rel.as_posix() != name):
                    raise ValueError("同步包包含非法路径")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("同步包文件校验失败")
                path = staging.joinpath(*rel.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        staged_config = staging / "config"
        session = json.loads((staged_config / "session/session.json").read_text(encoding="utf-8"))
        if (session.get("actives", {}).get("user") != manifest["username"]
                or session.get("actives", {}).get("layout") != manifest["layout"]):
            raise ValueError("同步包活动选择与清单不一致")
        database = staged_config / "session/profile.db"
        if database.exists():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Profile 数据库快照校验失败")
        old = staging / "old-config"
        if target.exists():
            target.rename(old)
        try:
            staged_config.rename(target)
            swapped = True
        except OSError:
            if old.exists():
                old.rename(target)
            raise
        if on_applied is not None:
            on_applied()
        if old.exists():
            previous.parent.mkdir(exist_ok=True)
            if previous.exists():
                shutil.rmtree(previous)  # 仅轮换本功能拥有的上一份配置备份。
            old.rename(previous)
        return {key: value for key, value in manifest.items() if key != "files"}
    except Exception:
        old = staging / "old-config"
        if swapped and old.exists():
            target.rename(staging / "failed-config")
            old.rename(target)
            swapped = False
        raise
    finally:
        # 交换完成后这里只包含暂存内容；失败时原目录已恢复。
        if not swapped and (staging / "old-config").exists() and not target.exists():
            (staging / "old-config").rename(target)
        shutil.rmtree(staging)
