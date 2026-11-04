"""初始同步 —— 一次性建立本实例与云端的同步状态。

规则（0.15.10 起）：

- 云端为空：本机是第一个实例，上传四通道（定义/用户/文档/总览）并创建
  初始快照；不备份本地。
- 云端有数据：本机本地 ``users/``、``profile.db``、``profile.yaml`` 先整体
  移到 ``config/session/_backup-initialsync-<时间戳>/``，再拉远端四通道。
  云端有数据必然有快照（数据只能经初始同步进云），所以总览按快照回放。
- 成功后才置 ``sync_established=true``；此后「立即同步/自动同步」走常规
  管线，不再做任何查重或备份。
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from ...constants import SESSION_CONFIG_DIR, USERS_DIR
from ..account.service import account_config_path, load_profile
from ..json_files import formatted_json
from .client import SyncError, post


@dataclass
class InitialSyncReport:
    established: bool = False
    backup_dir: str = ""
    errors: list[str] = field(default_factory=list)
    rows: list[tuple[str, int, int, int]] = field(default_factory=list)

    def describe(self) -> str:
        parts = [f"{name} 上行{up} 下行{down}" + (f" 失败{fail}" if fail else "")
                 for name, up, down, fail in self.rows]
        text = "，".join(parts) or "无改动"
        if self.backup_dir:
            text += f"；本地已备份到 {self.backup_dir}"
        return text

    def distinct_errors(self) -> list[str]:
        seen: list[str] = []
        for error in self.errors:
            text = str(error).strip()
            if text and text not in seen:
                seen.append(text)
        return seen


def sync_established() -> bool:
    return bool(load_profile().get("sync_established"))


def _save_profile(profile: dict) -> None:
    path = account_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(formatted_json(profile), encoding="utf-8")


def set_sync_established(value: bool) -> None:
    profile = load_profile()
    profile["sync_established"] = value
    _save_profile(profile)


def clear_local_sync_state() -> None:
    """清空本机全部同步水位与签名（不动登录态、E2E 密钥、本地数据）。"""
    profile = load_profile()
    profile.pop("users_sync", None)
    profile.pop("profile_sync", None)
    _save_profile(profile)
    logger.info("[initial sync] 本地同步水位已清除")


def _backup_target() -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = SESSION_CONFIG_DIR / f"_backup-initialsync-{stamp}"
    suffix = 1
    while target.exists():
        target = SESSION_CONFIG_DIR / f"_backup-initialsync-{stamp}-{suffix}"
        suffix += 1
    return target


def _session_document_sources() -> list[Path]:
    """注册的账户级同步文档（如 yysl/play_styles.json）的本地路径。"""
    from . import registry

    sources: list[Path] = []
    for spec in registry.session_documents().values():
        path = SESSION_CONFIG_DIR / spec.rel_path
        if path.is_file():
            sources.append(path)
    return sources


def backup_local_profile_data() -> Path | None:
    """把本地 ``users/``、``profile.db``、``profile.yaml`` 与注册的会话级
    同步文档移入备份目录；移完重建空的 ``users/``，profile.db / profile.yaml
    与文档由后续拉取重建。目标与文档都不存在时返回 None（不做空备份）。
    """
    sources = [Path(USERS_DIR), SESSION_CONFIG_DIR / "profile.db",
               SESSION_CONFIG_DIR / "profile.yaml"]
    session_docs = _session_document_sources()
    if not any(source.exists() for source in sources) and not session_docs:
        return None
    target = _backup_target()
    target.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if source.exists():
            shutil.move(str(source), str(target / source.name))
            logger.info("[initial sync] 已备份 {} → {}", source.name, target)
    for source in session_docs:
        relative = source.relative_to(SESSION_CONFIG_DIR)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        logger.info("[initial sync] 已备份会话文档 {} → {}", relative, destination)
    Path(USERS_DIR).mkdir(parents=True, exist_ok=True)
    return target


def initial_sync(token: str, on_progress=None, stop=None) -> InitialSyncReport:
    """执行一次初始同步；成功返回 ``established=True``。"""
    from ..profile.snapshot_sync import pull_and_replay, refresh_snapshot
    from .definition_sync import sync_definition
    from .engine import sync_documents
    from .users_sync import sync_users

    report = InitialSyncReport()

    def progress(data) -> None:
        if on_progress:
            on_progress(dict(data))

    try:
        state = post("/api/sync/state", {}, token)
    except SyncError as error:
        report.errors.append(str(error))
        return report

    if state.get("empty"):
        definition = sync_definition(token, on_progress=progress)
        users = sync_users(token, on_progress=progress, stop=stop)
        documents = sync_documents(token, on_progress=progress, stop=stop)
        snapshot = refresh_snapshot(token)
        report.rows = [
            ("定义", definition.pushed, definition.pulled, len(definition.errors)),
            ("用户", users.pushed, users.pulled, len(users.errors)),
            ("文档", len(documents.pushed), len(documents.pulled),
             len(documents.errors)),
            ("总览", snapshot.snapshots, snapshot.pulled, len(snapshot.errors)),
        ]
        report.errors = [*definition.errors, *users.errors,
                         *documents.errors, *snapshot.errors]
    else:
        report.backup_dir = str(backup_local_profile_data() or "")
        clear_local_sync_state()
        definition = sync_definition(token, on_progress=progress)
        users = sync_users(token, on_progress=progress, stop=stop)
        documents = sync_documents(token, on_progress=progress, stop=stop)
        # 只拉不推：初始同步以云端为准。回放会触发 profile 引擎物化周期锚点并
        # 写本地历史，那些是云数据的派生，不是本机真实编辑，禁止回推云端。
        profile = pull_and_replay(token, on_progress=progress, stop=stop)
        from ..account.service import profile_sync_state, save_profile_sync_state
        from ..profile import repository
        state = profile_sync_state()
        state["local_history_id"] = repository.db_max_history_id()
        save_profile_sync_state(state)
        report.rows = [
            ("定义", definition.pushed, definition.pulled, len(definition.errors)),
            ("用户", users.pushed, users.pulled, len(users.errors)),
            ("文档", len(documents.pushed), len(documents.pulled),
             len(documents.errors)),
            ("总览", profile.pushed, profile.pulled, len(profile.errors)),
        ]
        report.errors = [*definition.errors, *users.errors,
                         *documents.errors, *profile.errors]

    if not report.errors:
        set_sync_established(True)
        report.established = True
    return report
