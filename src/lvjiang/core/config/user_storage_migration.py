"""旧用户与批量表格到用户资料模型的一次性迁移。"""
from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from loguru import logger

from ..fs_util import atomic_write_text
from .session import CURRENT_SESSION_VERSION, DEFAULT_SESSION_VERSION

_VALID_USERNAME = re.compile(r"^[\w一-鿿-]{1,32}$")


def _is_user_document(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(data, dict) and data.get("document_type") == "lvjiang.user"
    except Exception:
        return False


def _clean_text(value) -> str:
    return "" if value is None else str(value).strip()


def _legacy_users(document: dict) -> tuple[list[str], dict[str, dict]]:
    order: list[str] = []
    metadata: dict[str, dict] = {}
    raw_users = document.get("users", [])
    if not isinstance(raw_users, list):
        return order, metadata
    for raw in raw_users:
        if isinstance(raw, str):
            username = raw
            legacy = {}
        elif isinstance(raw, dict):
            username = _clean_text(raw.get("name") or raw.get("username"))
            legacy = raw
        else:
            continue
        if not username or not _VALID_USERNAME.fullmatch(username):
            continue
        if username not in order:
            order.append(username)
        metadata[username] = {
            "created_at": _clean_text(legacy.get("created_at")),
            "avatar": _clean_text(legacy.get("avatar")),
            "attributes": {},
        }
    return order, metadata


def _merge_attribute(metadata: dict[str, dict], username: str, key: str, value) -> None:
    text = _clean_text(value)
    if not text:
        return
    target = metadata.setdefault(username, {
        "created_at": "", "avatar": "", "attributes": {},
    })["attributes"]
    previous = target.get(key)
    if previous and previous != text:
        logger.warning(
            f"用户 {username} 的迁移属性 {key} 存在冲突，保留 {previous!r}，"
            f"忽略 {text!r}；原始值仍在迁移备份中"
        )
        return
    target[key] = text


def _convert_batch(document: dict, order: list[str], metadata: dict[str, dict]) -> dict:
    raw_batch = document.get("batch", {})
    if not isinstance(raw_batch, dict):
        return {"configs": {}, "active_config": "", "script_ids": []}
    enabled_by_config = raw_batch.get("enabled_rows", {})
    if not isinstance(enabled_by_config, dict):
        enabled_by_config = {}
    converted_configs = {}
    raw_configs = raw_batch.get("configs", {})
    if not isinstance(raw_configs, dict):
        raw_configs = {}
    for config_name, raw_config in raw_configs.items():
        if not isinstance(raw_config, dict):
            continue
        # 已是新格式时只做规范化，不再次解释为旧表格。
        if isinstance(raw_config.get("usernames"), list):
            usernames = [
                value for value in raw_config["usernames"]
                if isinstance(value, str) and value
            ]
        else:
            rows = raw_config.get("rows", [])
            rows = rows if isinstance(rows, list) else []
            user_column = _clean_text(raw_config.get("user_column")) or "role"
            flags = enabled_by_config.get(config_name, [])
            flags = flags if isinstance(flags, list) else []
            usernames = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                username = _clean_text(row.get(user_column))
                if not username or not _VALID_USERNAME.fullmatch(username):
                    continue
                if username not in order:
                    order.append(username)
                metadata.setdefault(username, {
                    "created_at": "", "avatar": "", "attributes": {},
                })
                for key in ("account", "role", "role_index", "tail"):
                    _merge_attribute(metadata, username, key, row.get(key))
                enabled = bool(flags[index]) if index < len(flags) else True
                if enabled and username not in usernames:
                    usernames.append(username)
        converted_configs[str(config_name)] = {
            "name": _clean_text(raw_config.get("name")) or str(config_name),
            "usernames": list(dict.fromkeys(usernames)),
            "workflows": deepcopy(raw_config.get("workflows", {})),
        }
    return {
        "configs": converted_configs,
        "active_config": _clean_text(raw_batch.get("active_config")),
        "script_ids": deepcopy(raw_batch.get("script_ids", [])),
    }


def _move_legacy_sessions(users_dir: Path, usernames: list[str]) -> None:
    for username in usernames:
        source = users_dir / f"{username}.json"
        target = users_dir / f"{username}.session.json"
        if not source.exists() or _is_user_document(source):
            continue
        if target.exists():
            backup = users_dir / f"{username}.legacy-session.json"
            if not backup.exists():
                shutil.copy2(source, backup)
            source.unlink()
            logger.warning(f"用户 {username} 已有新 Session，旧文件保留为 {backup.name}")
        else:
            source.replace(target)


def _write_metadata(users_dir: Path, order: list[str], metadata: dict[str, dict]) -> None:
    from ..user_config import User, load_user_metadata, save_user_metadata

    for username in order:
        legacy = metadata.get(username, {})
        existing = load_user_metadata(username, users_dir)
        if existing is None:
            existing = User(
                name=username,
                created_at=legacy.get("created_at") or datetime.now().isoformat(),
                avatar=legacy.get("avatar", ""),
            )
        elif not existing.created_at and legacy.get("created_at"):
            existing.created_at = legacy["created_at"]
        if not existing.avatar and legacy.get("avatar"):
            existing.avatar = legacy["avatar"]
        for key, value in legacy.get("attributes", {}).items():
            if not existing.attributes.get(key):
                existing.attributes[key] = value
        save_user_metadata(existing, users_dir)


def migrate_user_storage(store, users_dir: Path) -> bool:
    """将低于 v2 的 session 数据升级到 v2。"""
    users_dir.mkdir(parents=True, exist_ok=True)
    store.reload()
    document = store.snapshot()
    version = document.get("version", DEFAULT_SESSION_VERSION)
    version = version if type(version) is int else DEFAULT_SESSION_VERSION
    if version >= CURRENT_SESSION_VERSION:
        return False

    from ..access import is_readonly
    if is_readonly():
        raise RuntimeError("用户数据尚未迁移，请先启动主实例完成升级")

    backup = store.path.with_name(
        f"{store.path.stem}.pre-v{CURRENT_SESSION_VERSION}.json"
    )
    if store.path.exists() and not backup.exists():
        atomic_write_text(
            backup, store.path.read_text(encoding="utf-8"),
            prefix=f".{store.path.stem}_migration_backup_",
        )

    order, metadata = _legacy_users(document)
    converted_batch = _convert_batch(document, order, metadata)
    _move_legacy_sessions(users_dir, order)
    _write_metadata(users_dir, order, metadata)

    def commit(latest: dict) -> None:
        latest["users"] = order
        latest["batch"] = converted_batch
        latest["version"] = CURRENT_SESSION_VERSION
        latest.pop("migrations", None)

    store.mutate_document(commit)
    logger.info(f"用户数据迁移完成: {len(order)} 个用户")
    return True
