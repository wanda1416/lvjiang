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
from .json_files import formatted_json, formatted_json_bytes
from .layout_config import load_layout_doc
from .paths import (
    config_directory,
    get_config_local_dir,
    get_config_system_dir,
    get_config_updates_dir,
    session_directory,
)
from .user_config import is_valid_username

FORMAT_VERSION = 3
MAX_BUNDLE_BYTES = 256 << 20
# 同步契约按消费者登记；新增目录默认不下发，不能递归复制整个配置层。
_LAYER_PATTERNS = (
    "app.yaml", "ocr.yaml", "ocr_rules.yaml", "scenes.yaml", "layouts.yaml",
    "workflows/**/*.wf", "scenes/*.yaml",
    "references/*.yaml", "references/**/*.png", "templates/**/*.png",
    "maps/*/map.yaml", "maps/*/*.png",
)
_SESSION_FILES = ("session.json", "interface.json", "profile.yaml", "batch.json")
_DATABASE_FILES = ("profile.db",)


def _database_files() -> tuple[str, ...]:
    return tuple(dict.fromkeys((*_DATABASE_FILES, *(
        name for app_id in get_registered_app_ids()
        for name in load_app(app_id).offline_database_files))))


def _configuration_files(config: Path, layout_scope: set[str]) -> Iterator[tuple[Path, Path]]:
    hooks = [load_app(name) for name in get_registered_app_ids()]
    patterns = (*_LAYER_PATTERNS, *(
        pattern for app in hooks for pattern in app.offline_configuration_patterns))
    for layer, root in (("system", get_config_system_dir(config)), ("local", get_config_local_dir(config))):
        paths = {path for pattern in patterns for path in root.glob(pattern)}
        for layout in layout_scope:
            paths.update((root / "layouts" / layout).glob("*.json"))
        from .license_files import is_private_license_file
        yield from ((Path(layer) / path.relative_to(root), path)
                    for path in sorted(paths) if not is_private_license_file(path.name))
    session = session_directory(config)
    yield from ((Path("session") / name, session / name) for name in _SESSION_FILES)
    yield from ((Path("session/users") / path.name, path) for path in sorted((session / "users").glob("*.json")))
    for app in hooks:
        if app.offline_session_files is not None:
            yield from ((Path("session") / name, session / name) for name in app.offline_session_files())


def build_offline_bundle(root: Path, destination: Path, *, username: str, layout: str) -> dict:
    """构造配置副本和 SQLite backup 快照；不修改 PC 的活动选择或数据库。"""
    if not is_valid_username(username) or not layout:
        raise ValueError("请选择有效执行用户和安卓布局")
    config = config_directory(root)
    # PC 已安装的有效 system 直接作为手机离线快照，local 保持独立覆盖层。
    from .config.interface import InterfaceStore
    InterfaceStore(session_directory(config) / "interface.json")
    layout_doc = load_layout_doc(ConfigResolver(config_dir=config))
    entries = layout_doc["layouts"]
    if layout not in entries:
        raise ValueError("所选同步布局不存在")
    layout_scope = {layout}
    if entries[layout].get("extends"):
        layout_scope.add(entries[layout]["extends"])
    if not (session_directory(config) / "users" / f"{username}.json").is_file():
        raise ValueError("执行用户资料不存在")
    manifest: dict[str, Any] = {
        "format": FORMAT_VERSION, "app_version": __version__,
        "username": username, "layout": layout,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": {},
    }
    with tempfile.TemporaryDirectory(prefix="lvjiang-offline-db-") as temp:
        snapshots = []
        for name in _database_files():
            database = session_directory(config) / name
            snapshot = Path(temp) / name
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            if not database.is_file() or database.is_symlink():
                continue
            # SQLite 的连接上下文只处理事务，不释放文件句柄。
            # 必须在读取快照和清理临时目录前关闭，Windows 不允许删除打开的 DB。
            with (closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source,
                  closing(sqlite3.connect(snapshot)) as target):
                source.backup(target)
            snapshots.append((name, snapshot))
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            def add(name: str, data: bytes) -> None:
                manifest["files"][name] = hashlib.sha256(data).hexdigest()
                archive.writestr(name, data)

            for rel, path in _configuration_files(config, layout_scope):
                if (not path.is_file() or path.is_symlink()
                        or any(part.startswith(".") for part in rel.parts)):
                    continue
                data = path.read_bytes()
                if rel.as_posix() in {"system/layouts.yaml", "local/layouts.yaml"}:
                    doc = yaml.safe_load(data) or {}
                    layouts = doc.get("layouts") or {}
                    doc["layouts"] = {key: value for key, value in layouts.items() if key in layout_scope}
                    deleted = [key for key in layouts.get("__deleted__", []) if key in layout_scope]
                    if deleted:
                        doc["layouts"]["__deleted__"] = deleted
                    data = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).encode("utf-8")
                if rel.as_posix() == "session/interface.json":
                    # 手机端只认「当前在用哪个」这一层：直接给到同步清单里的
                    # 执行用户与布局，环境固定为安卓。
                    interface = json.loads(data)
                    interface.setdefault("actives", {}).update(
                        user=username, layout=layout, env="android")
                    data = formatted_json_bytes(interface)
                add("config/" + rel.as_posix(), data)
            for name, snapshot in snapshots:
                add("config/session/" + name, snapshot.read_bytes())
            # 不携带 PC 旧的同步状态；此记录只属于这次单向快照。
            summary = {key: value for key, value in manifest.items() if key != "files"}
            add("config/session/offline.json", formatted_json_bytes(summary))
            archive.writestr("manifest.json", formatted_json_bytes(manifest))
    if destination.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError("同步包超过 256 MiB，请移除无关的大文件")
    return summary


def install_offline_bundle(
    archive_path: Path, root: Path, *, on_applied: Callable[[], None] | None = None,
    preserve_task_params: bool = True, preserve_batch_config: bool = True,
) -> dict:
    """校验后交换 config 目录；保留上一份备份，交换失败恢复原配置。"""
    from .access import require_primary_write
    require_primary_write()
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".offline-stage-", dir=root))
    target = config_directory(root)
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
                        or rel.parts[1] not in {"system", "local", "session"}
                        or rel.as_posix() != name):
                    raise ValueError("同步包包含非法路径")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("同步包文件校验失败")
                path = staging.joinpath(*rel.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        staged_config = staging / "config"
        incoming_interface = staged_config / "session/interface.json"
        interface_data = (json.loads(incoming_interface.read_text(encoding="utf-8"))
                          if incoming_interface.is_file() else {})
        actives = (interface_data.get("actives", {})
                   if isinstance(interface_data, dict) else {})
        if (actives.get("user") != manifest["username"]
                or actives.get("layout") != manifest["layout"]):
            raise ValueError("同步包活动选择与清单不一致")
        for database in (staged_config / "session").rglob("*.db"):
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError(f"数据库快照校验失败：{database.name}")
        # 历史不属于参数快照；包括旧包残留也不得覆盖手机记录。
        from .history_transfer import history_channels
        for channel in history_channels().values():
            relative = Path("session") / channel.database
            incoming = staged_config / relative
            incoming.unlink(missing_ok=True)
            existing = target / relative
            if existing.is_file():
                incoming.parent.mkdir(parents=True, exist_ok=True)
                with (closing(sqlite3.connect(existing.resolve().as_uri() + "?mode=ro", uri=True)) as source,
                      closing(sqlite3.connect(incoming)) as history_destination):
                    source.backup(history_destination)
        for name in ("output", "history-transfer"):
            existing = target / "session" / name
            if existing.is_dir():
                shutil.copytree(existing, staged_config / "session" / name)
        if preserve_task_params and target.exists():
            _preserve_task_parameters(target, staged_config)
        if preserve_batch_config and target.exists():
            mobile_batch = session_directory(target) / "batch.json"
            if mobile_batch.is_file():
                shutil.copyfile(mobile_batch, staged_config / "session/batch.json")
        # 本次批量选择属于手机运行草稿，电脑仅同步配置组定义。
        old_interface = session_directory(target) / "interface.json"
        mobile_interface = json.loads(old_interface.read_text(encoding="utf-8")) if old_interface.is_file() else {}
        # 上一步可能刚把手机端选定的执行用户写回暂存界面状态，这里必须重新
        # 读盘后再补批量草稿，否则会用调用前读到的旧内容把它覆盖掉。
        if incoming_interface.is_file():
            interface_data = json.loads(incoming_interface.read_text(encoding="utf-8"))
            if not isinstance(interface_data, dict):
                interface_data = {}
        interface_data.setdefault("actives", actives)
        interface_data["batch"] = mobile_interface.get("batch", {})
        incoming_interface.write_text(formatted_json(interface_data), encoding="utf-8")
        # 手机目前不主动在线更新。PC 快照替换 system，但不能删除 APK 的原始
        # 预置归档；它不成为新 r0，也不携带 PC 的更新身份、会员凭据或归档。
        for name in ("updates",):
            existing = get_config_updates_dir(target)
            if existing.is_dir():
                shutil.copytree(existing, staged_config / name)
            elif existing.is_file():
                (staged_config / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(existing, staged_config / name)
        baseline = session_directory(target) / "baseline.json"
        if baseline.is_file():
            destination = session_directory(staged_config) / "baseline.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(baseline, destination)
        # 此标记只记录 APK 官方代次，与是否覆盖用户参数无关。
        marker = session_directory(target) / "preset.json"
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
        from .config.content import rebuild_content
        from .config.resolver import ConfigResolver
        rebuild_content(ConfigResolver(config_dir=target, dev_mode=False))
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
    old_path = session_directory(old) / "session.json"
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
        source = session_directory(old) / "users" / f"{username}.json"
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
            destination.write_text(formatted_json(pc), encoding="utf-8")
        else:
            shutil.copyfile(source, destination)
        if username not in names:
            names.append(username)
    new_session["users"] = names
    new_path.write_text(formatted_json(new_session), encoding="utf-8")
    # 手机端已经选定的执行用户属于本机选择，不随 PC 快照覆盖。
    old_interface_path = session_directory(old) / "interface.json"
    old_actives = (json.loads(old_interface_path.read_text(encoding="utf-8")).get("actives", {})
                   if old_interface_path.is_file() else {})
    active = old_actives.get("user") if isinstance(old_actives, dict) else None
    if active in names and (new_users / f"{active}.json").is_file():
        new_interface_path = incoming / "session/interface.json"
        new_interface = (json.loads(new_interface_path.read_text(encoding="utf-8"))
                         if new_interface_path.is_file() else {})
        if not isinstance(new_interface, dict):
            new_interface = {}
        new_interface.setdefault("actives", {})["user"] = active
        new_interface_path.write_text(formatted_json(new_interface), encoding="utf-8")
