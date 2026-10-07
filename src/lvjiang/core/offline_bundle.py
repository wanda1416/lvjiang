"""PC→Android 的配置快照；不传输 Python 源码，不回写 PC 数据。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .._version import __version__
from ..apps import get_registered_app_ids, load_app
from .config.resolver import ConfigResolver
from .layout_config import load_layout_doc
from .user_config import is_valid_username

FORMAT_VERSION = 1
MAX_BUNDLE_BYTES = 256 << 20
# 同步契约按消费者登记；新增目录默认不下发，不能递归复制整个配置层。
_LAYER_PATTERNS = (
    "app.yaml", "ocr.yaml", "ocr_rules.yaml", "scenes.yaml", "layouts.yaml",
    "workflows/**/*.wf", "scenes/*.yaml",
    "references/*.yaml", "references/**/*.png", "templates/**/*.png",
    "maps/*/map.yaml", "maps/*/*.png",
)
_SESSION_FILES = ("session.json", "interface.json", "profile.yaml")
_DATABASE_FILES = ("profile.db", "daily_history.db", "tuning_history.db")


def _configuration_files(config: Path, layout_scope: set[str]) -> Iterator[Path]:
    hooks = [load_app(name) for name in get_registered_app_ids()]
    patterns = (*_LAYER_PATTERNS, *(
        pattern for app in hooks for pattern in app.offline_configuration_patterns))
    for layer in ("system", "local", "remote"):
        root = config / layer
        paths = {path for pattern in patterns for path in root.glob(pattern)}
        for layout in layout_scope:
            paths.update((root / "layouts" / layout).glob("*.json"))
        yield from sorted(paths)
    session = config / "session"
    yield from (session / name for name in _SESSION_FILES)
    yield from sorted((session / "users").glob("*.json"))
    for app in hooks:
        if app.offline_session_files is not None:
            yield from (session / name for name in app.offline_session_files())


def build_offline_bundle(root: Path, destination: Path, *, username: str, layout: str) -> dict:
    """构造配置副本和 SQLite backup 快照；不修改 PC 的活动选择或数据库。"""
    if not is_valid_username(username) or not layout:
        raise ValueError("请选择有效执行用户和安卓布局")
    config = root / "config"
    from .config.interface import InterfaceStore
    InterfaceStore(config / "session/interface.json")
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
        snapshots = []
        for name in _DATABASE_FILES:
            database = config / "session" / name
            snapshot = Path(temp) / name
            if not database.is_file() or database.is_symlink():
                continue
            # SQLite 的连接上下文只处理事务，不释放文件句柄。
            # 必须在读取快照和清理临时目录前关闭，Windows 不允许删除打开的 DB。
            with (closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source,
                  closing(sqlite3.connect(snapshot)) as target):
                source.backup(target)
            snapshots.append(snapshot)
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def add(name: str, data: bytes) -> None:
                manifest["files"][name] = hashlib.sha256(data).hexdigest()
                archive.writestr(name, data)

            for path in _configuration_files(config, layout_scope):
                rel = path.relative_to(config)
                if (not path.is_file() or path.is_symlink()
                        or any(part.startswith(".") for part in rel.parts)):
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
            for snapshot in snapshots:
                add("config/session/" + snapshot.name, snapshot.read_bytes())
            # 不携带 PC 旧的同步状态；此记录只属于这次单向快照。
            summary = {key: value for key, value in manifest.items() if key != "files"}
            add("config/session/offline.json", json.dumps(summary, ensure_ascii=False).encode())
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    if destination.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("同步包超过 256 MiB，请移除无关的大文件")
    return summary


def install_offline_bundle(
    archive_path: Path, root: Path, *, on_applied: Callable[[], None] | None = None,
    preserve_task_params: bool = True,
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
        for name in _DATABASE_FILES:
            database = staged_config / "session" / name
            if not database.exists():
                continue
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError(f"数据库快照校验失败：{name}")
        if preserve_task_params and target.exists():
            _preserve_task_parameters(target, staged_config)
        # 此标记只记录 APK 官方代次，与是否覆盖用户参数无关。
        marker = target / "session/preset.json"
        if marker.is_file():
            shutil.copyfile(marker, staged_config / "session/preset.json")
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


def _preserve_task_parameters(old: Path, incoming: Path) -> None:
    """在校验后的暂存区按任务整体保留；不拷贝 DB 或用户运行 session。"""
    old_path = old / "session/session.json"
    if not old_path.is_file():
        return
    old_session = json.loads(old_path.read_text(encoding="utf-8"))
    new_path = incoming / "session/session.json"
    new_session = json.loads(new_path.read_text(encoding="utf-8"))
    old_shared = old_session.get("wf_configs", {})
    new_shared = new_session.get("wf_configs", {})
    if not isinstance(old_shared, dict) or not isinstance(new_shared, dict):
        raise ValueError("任务通用配置格式不正确，无法保留手机参数")
    new_session["wf_configs"] = {**new_shared, **old_shared}
    names = list(new_session.get("users", []))
    new_users = incoming / "session/users"
    new_users.mkdir(exist_ok=True)
    for username in old_session.get("users", []):
        if not isinstance(username, str) or not is_valid_username(username):
            continue
        source = old / "session/users" / f"{username}.json"
        if not source.is_file():
            continue
        destination = new_users / f"{username}.json"
        phone = json.loads(source.read_text(encoding="utf-8"))
        phone_params = phone.get("workflow_params", {})
        if not isinstance(phone_params, dict):
            raise ValueError("手机用户任务参数格式不正确，无法保留")
        if destination.exists():
            pc = json.loads(destination.read_text(encoding="utf-8"))
            pc_params = pc.get("workflow_params", {})
            if not isinstance(pc_params, dict):
                raise ValueError("同步用户任务参数格式不正确")
            pc["workflow_params"] = {**pc_params, **phone_params}
            destination.write_text(json.dumps(pc, ensure_ascii=False), encoding="utf-8")
        else:
            shutil.copyfile(source, destination)
        if username not in names:
            names.append(username)
    new_session["users"] = names
    active = old_session.get("actives", {}).get("user")
    if active in names and (new_users / f"{active}.json").is_file():
        new_session.setdefault("actives", {})["user"] = active
    new_path.write_text(json.dumps(new_session, ensure_ascii=False), encoding="utf-8")
