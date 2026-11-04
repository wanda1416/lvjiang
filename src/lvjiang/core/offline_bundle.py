"""PC→Android 的配置快照；不传输 Python 源码，不回写 PC 数据。"""

from __future__ import annotations

import hashlib
import json
import os
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
    session_directory,
)
from .user_config import is_valid_username

FORMAT_VERSION = 3
MAX_BUNDLE_BYTES = 256 << 20
# 同步契约按消费者登记；新增目录默认不下发，不能递归复制整个配置层。
_LAYER_PATTERNS = (
    "app.yaml", "ocr.yaml", "scenes.yaml", "layouts.yaml",
    "workflows/**/*.wf", "scenes/*.yaml",
    "references/*.yaml", "references/**/*.png", "templates/**/*.png",
    "maps/*/map.yaml", "maps/*/*.png",
)
_SESSION_FILES = ("session.json", "interface.json", "profile.yaml", "batch.json")
_DATABASE_FILES = ("profile.db",)

#: 配置目录里**由本机独占**的文件（相对 ``config/``）：实例出生标识、登录令牌、
#: 账号名等本机登录态。它们不属于同步契约，打包侧不得收进清单，安装侧一个字节
#: 都不碰——否则一次 PC→手机同步就会把登录态连根拔掉。
LOCAL_ONLY_CONFIG_FILES: tuple[str, ...] = (
    "session/instance.id",
    "session/account_token",
    "session/account.json",
)
#: 同步域里按**镜像**处理的层：包中没有的文件与空目录要先删掉，否则上游删除或
#: 移动过的脚本 / 场景 / 布局会永远留在手机上（.wf 换个目录就变成两份同 id 脚本）。
MIRRORED_LAYERS: tuple[str, ...] = ("system", "local")


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
    leaked = sorted(name for name in manifest["files"]
                    if name.startswith("config/")
                    and name[len("config/"):] in LOCAL_ONLY_CONFIG_FILES)
    if leaked:
        raise ValueError(f"同步清单不得包含本机独占文件：{', '.join(leaked)}")
    return summary


def _read_json(path: Path) -> dict:
    """读 JSON 对象；缺失或损坏时按空字典处理（合并逻辑能继续跑）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_bytes(destination: Path, data: bytes) -> None:
    """单文件原子替换：先写同名临时文件再 rename。

    不做整体事务——同步本身可重复执行，失败重来一次即可；这里只保证单个文件
    不会出现"写了一半"的中间态。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".incoming")
    temporary.write_bytes(data)
    os.replace(temporary, destination)


def _write_json(destination: Path, data: dict) -> None:
    _write_bytes(destination, formatted_json(data).encode("utf-8"))


def _apply_database(destination: Path, source: Path) -> None:
    """SQLite 不能用文件覆盖（目标库可能正被打开）：用 backup 还原过去。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as origin,
          closing(sqlite3.connect(destination)) as target):
        origin.backup(target)


def _mirror_layers(target: Path, files: dict[str, str]) -> None:
    """``system`` / ``local`` 按镜像处理：删掉包中没有的文件与空目录。

    这是整目录交换唯一真正需要的能力——上游删除或移动过的 ``.wf``、场景、布局
    必须在这里消失，否则手机上会留着幽灵副本（同 id 脚本两份、删掉的场景仍加载）。
    """
    managed = set(files)
    for layer in MIRRORED_LAYERS:
        layer_root = target / layer
        if not layer_root.is_dir():
            continue
        for path in sorted(layer_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            relative = path.relative_to(target).as_posix()
            if path.is_dir():
                if not any(path.iterdir()):
                    path.rmdir()
            elif f"config/{relative}" not in managed:
                path.unlink()


def _write_mirrored_layers(target: Path, staged: Path, files: dict[str, str]) -> None:
    """把包里的 ``system`` / ``local`` 文件逐个写到本机（镜像删除由调用方先做）。"""
    for name in files:
        relative = PurePosixPath(name)
        if len(relative.parts) < 3 or relative.parts[1] not in MIRRORED_LAYERS:
            continue
        source = staged.joinpath(*relative.parts[1:])
        destination = target.joinpath(*relative.parts[1:])
        _write_bytes(destination, source.read_bytes())


def _apply_session(target: Path, staged: Path, files: dict[str, str], *,
                   username: str, layout: str,
                   preserve_task_params: bool, preserve_batch_config: bool) -> None:
    """``session`` 只按清单覆盖：同目录里不在清单的文件一律不动。

    清单内的四类合并语义与以前一致：
    - ``users/*.json``：包内覆盖；保留任务参数时把手机参数并进同名用户，
      不保留时删掉包里没有的手机用户文件（否则名册里没有的幽灵用户会留着）；
    - ``session.json``：名册与任务通用参数按保留开关合并；
    - ``interface.json``：actives 跟清单，``batch`` 草稿永远属于手机；
    - ``batch.json``：明确保留手机本次选择时整份不写。
    """
    from .history_transfer import history_channels

    session_target = session_directory(target)
    session_staged = staged / "session"
    history_names = {channel.database for channel in history_channels().values()}
    phone_session = _read_json(session_target / "session.json")
    phone_interface = _read_json(session_target / "interface.json")

    # 1) 其余清单文件（profile.yaml、offline.json、*.db…）直接覆盖
    for name in files:
        relative = PurePosixPath(name)
        if len(relative.parts) < 3 or relative.parts[1] != "session":
            continue
        if relative.parts[2] in {"session.json", "interface.json", "batch.json", "users"}:
            continue
        if relative.name in history_names:
            continue                      # 历史不属于参数快照
        source = staged.joinpath(*relative.parts[1:])
        destination = target.joinpath(*relative.parts[1:])
        if destination.suffix == ".db":
            _apply_database(destination, source)
        else:
            _write_bytes(destination, source.read_bytes())

    # 2) 用户资料
    staged_users = session_staged / "users"
    incoming_users: set[str] = set()
    if staged_users.is_dir():
        for path in sorted(staged_users.glob("*.json")):
            incoming_users.add(path.name)
            data = _read_json(path)
            if preserve_task_params:
                phone_params = _read_json(session_target / "users" / path.name).get("workflow_params")
                if isinstance(phone_params, dict):
                    merged = dict(data.get("workflow_params") or {})
                    merged.update(phone_params)
                    data["workflow_params"] = merged
            _write_json(session_target / "users" / path.name, data)
    if not preserve_task_params and (session_target / "users").is_dir():
        for path in sorted((session_target / "users").glob("*.json")):
            if path.name not in incoming_users:
                path.unlink()

    # 3) session.json：名册与任务通用参数
    incoming_session = _read_json(session_staged / "session.json")
    if incoming_session:
        names = list(incoming_session.get("users") or [])
        if preserve_task_params:
            for name in phone_session.get("users") or []:
                if name not in names and (session_target / "users" / f"{name}.json").is_file():
                    names.append(name)
            shared = dict(incoming_session.get("wf_configs") or {})
            phone_shared = phone_session.get("wf_configs")
            if isinstance(phone_shared, dict):
                shared.update(phone_shared)
            incoming_session["wf_configs"] = shared
        incoming_session["users"] = names
        _write_json(session_target / "session.json", incoming_session)

    # 4) interface.json：actives 跟清单，批量草稿属于手机
    incoming_interface = _read_json(session_staged / "interface.json")
    if incoming_interface:
        actives = incoming_interface.get("actives")
        if not isinstance(actives, dict):
            actives = {}
        actives.update(user=username, layout=layout, env="android")
        phone_actives = phone_interface.get("actives")
        if preserve_task_params and isinstance(phone_actives, dict):
            active_user = phone_actives.get("user")
            if (isinstance(active_user, str)
                    and (session_target / "users" / f"{active_user}.json").is_file()):
                actives["user"] = active_user
        incoming_interface["actives"] = actives
        batch = phone_interface.get("batch")
        incoming_interface["batch"] = batch if isinstance(batch, dict) else {}
        _write_json(session_target / "interface.json", incoming_interface)

    # 5) batch.json：明确保留手机的本次选择时，手机已有那份就不动；
    #    手机还没有时仍然落地包里的配置组定义（否则第一次同步拿不到批量配置）。
    staged_batch = session_staged / "batch.json"
    phone_batch = session_target / "batch.json"
    if staged_batch.is_file() and (not preserve_batch_config or not phone_batch.is_file()):
        _write_bytes(phone_batch, staged_batch.read_bytes())


def install_offline_bundle(
    archive_path: Path, root: Path, *, on_applied: Callable[[], None] | None = None,
    preserve_task_params: bool = True, preserve_batch_config: bool = True,
) -> dict:
    """把同步包按文件覆盖到本机配置（不再整目录交换）。

    写入策略分域：
    - ``system`` / ``local``：**镜像**（先删包中没有的文件与空目录，再逐文件写）；
    - ``session``：**只覆盖清单内的文件**，同目录其他文件保留；
    - ``LOCAL_ONLY_CONFIG_FILES``（实例标识 / 登录令牌 / 账号名）：一个字节都不碰。

    不做整份备份与回滚：同步是可重复操作，失败重跑一次即可。SQLite 仍走
    ``backup`` 到目标库，那是因为目标库可能正被打开，不是为了回滚。
    """
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = config_directory(root)
    staging = Path(tempfile.mkdtemp(prefix=".offline-stage-", dir=root))
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
            guarded = {f"config/{name}" for name in LOCAL_ONLY_CONFIG_FILES}
            if guarded & set(files):
                raise ValueError("同步包包含本机独占文件，已拒绝安装")
            for name, digest in files.items():
                relative = PurePosixPath(name)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in name
                        or len(relative.parts) < 3 or relative.parts[0] != "config"
                        or relative.parts[1] not in {"system", "local", "session"}
                        or relative.as_posix() != name):
                    raise ValueError("同步包包含非法路径")
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("同步包文件校验失败")
                path = staging.joinpath(*relative.parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        staged = staging / "config"
        incoming_interface = staged / "session/interface.json"
        interface_data = _read_json(incoming_interface)
        actives = interface_data.get("actives") if isinstance(interface_data, dict) else {}
        actives = actives if isinstance(actives, dict) else {}
        if (actives.get("user") != manifest["username"]
                or actives.get("layout") != manifest["layout"]):
            raise ValueError("同步包活动选择与清单不一致")
        for database in (staged / "session").rglob("*.db"):
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as conn:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError(f"数据库快照校验失败：{database.name}")
        # ▲ 校验全部通过，从这里开始才动本机配置 ▲
        _mirror_layers(target, files)
        _write_mirrored_layers(target, staged, files)
        _apply_session(target, staged, files, username=manifest["username"],
                       layout=manifest["layout"],
                       preserve_task_params=preserve_task_params,
                       preserve_batch_config=preserve_batch_config)
        from .config.content import rebuild_content
        rebuild_content(ConfigResolver(config_dir=target, dev_mode=False))
        if on_applied is not None:
            on_applied()
        return {key: value for key, value in manifest.items() if key != "files"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
