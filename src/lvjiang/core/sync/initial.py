"""初始同步 —— 一次性建立本实例与云端的同步状态。

云端按 ``snapshot_count`` 分成三种动作：

- 还没有快照：先占初始化锁。原播种机上传定义、名册、文档，四步都成功
  才建快照（允许空快照）。另一台过期接管时只补快照，不覆盖名册。
- 最新快照是空基线（水位 0、账本也为空）且本机已有总览值：只把本机
  总览补成新基线，不剪切本地用户。
- 已有快照：确认后备份本地，再整库改跟云端。任一步失败把备份搬回来。
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
    profile.pop("document_sync", None)
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


_CONFIG_COPIES = ("session.json", "interface.json")


def _copy_config_files(backup: Path) -> None:
    """session.json / interface.json 只复制，不剪走。还原时整文件覆盖。"""
    backup.mkdir(parents=True, exist_ok=True)
    for name in _CONFIG_COPIES:
        source = SESSION_CONFIG_DIR / name
        if source.is_file():
            shutil.copy2(source, backup / name)


def begin_follow_backup() -> Path:
    """跟随云端前的备份目录。没有可剪切的文件时仍建目录，用来装配置副本。"""
    target = backup_local_profile_data()
    if target is None:
        target = _backup_target()
        target.mkdir(parents=True, exist_ok=True)
    _copy_config_files(target)
    return target


def clear_local_roster() -> None:
    """名册改跟云端之前先清空，避免本地独有名字被追加回来。"""
    from ..config.session import get_session_store

    get_session_store().mutate_node("users", lambda _value: [])


def restore_local_profile_data(backup: Path) -> str:
    """把这次跟随剪走的文件搬回原处。

    成功返回空串并删除备份目录。任何一步失败都保留目录，返回带路径的错误。
    """
    try:
        _restore_into_place(backup)
        clear_local_sync_state()
        set_sync_established(False)
        from ..config.session import get_session_store
        get_session_store().reload()
        shutil.rmtree(backup)
    except Exception as error:  # noqa: BLE001 - 还原失败必须把备份路径交给用户
        logger.exception("[initial sync] 还原本地数据失败")
        return f"还原本地数据失败：{error}；备份仍在 {backup}"
    return ""


def _unlink_file(path: Path) -> None:
    if path.is_file():
        path.unlink()


def _restore_into_place(backup: Path) -> None:
    """先删掉本次拉取留下的半成品，再把备份移回。users/ 不能叠在一起。"""
    from . import registry

    users = Path(USERS_DIR)
    if users.exists():
        shutil.rmtree(users)
    database = SESSION_CONFIG_DIR / "profile.db"
    for suffix in ("", "-wal", "-shm"):
        _unlink_file(Path(str(database) + suffix))
    _unlink_file(SESSION_CONFIG_DIR / "profile.yaml")
    for spec in registry.session_documents().values():
        _unlink_file(SESSION_CONFIG_DIR / spec.rel_path)

    backed_users = backup / "users"
    if backed_users.exists():
        shutil.move(str(backed_users), str(users))
    else:
        users.mkdir(parents=True, exist_ok=True)
    for name in ("profile.db", "profile.yaml"):
        source = backup / name
        if source.exists():
            shutil.move(str(source), str(SESSION_CONFIG_DIR / name))
    for spec in registry.session_documents().values():
        source = backup / spec.rel_path
        if not source.is_file():
            continue
        destination = SESSION_CONFIG_DIR / spec.rel_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        _unlink_file(destination)
        shutil.move(str(source), str(destination))
    for name in _CONFIG_COPIES:
        source = backup / name
        destination = SESSION_CONFIG_DIR / name
        if source.is_file():
            shutil.copy2(source, destination)
        else:
            _unlink_file(destination)


def local_profile_has_entries() -> bool:
    from ..profile.snapshot_sync import local_profile_has_entries as _has

    return _has()


def classify_initial_action(state: dict, *, local_has_entries: bool) -> str:
    """``seed`` / ``follow`` / ``replace_empty``。播种与只补快照在占锁后再分。"""
    count = state.get("snapshot_count")
    if not isinstance(count, int) or count <= 0:
        return "seed"
    snapshot = state.get("snapshot")
    if not isinstance(snapshot, dict):
        return "follow"
    entry_count = snapshot.get("entry_count")
    remote_id = snapshot.get("snapshot_remote_id")
    history = state.get("history")
    placeholder = (
        entry_count == 0 and remote_id == 0
        and (history == 0 or history is None)
        and local_has_entries
    )
    if placeholder:
        return "replace_empty"
    return "follow"


def cloud_has_seed_data(state: dict) -> bool:
    """快照之外是否已经有定义、名册、文档或账本。"""
    if state.get("definition") is True:
        return True
    for key in ("users", "documents", "history"):
        value = state.get(key)
        if isinstance(value, int) and value > 0:
            return True
    return False


def cloud_not_inited(token: str) -> bool | None:
    """云端是否**明确**处于「未初始化 / 已被清空」状态。

    三态语义，绝不含糊：
    - ``True``：服务端明确回 ``not_inited: true``（所有内容表计数为 0）；
    - ``False``：云端有数据；
    - ``None``：问不到（网络/鉴权/响应格式异常）。

    ``None`` 必须按「未知」处理，**不能当成空**——否则一次网络抖动就会把本地
    同步状态清掉，用户被迫重做初始同步。
    """
    try:
        state = post("/api/sync/state", {}, token)
    except SyncError:
        return None
    flag = state.get("not_inited")
    if not isinstance(flag, bool):
        return None                     # 老服务端/异常响应：不认，按未知处理
    return flag


@dataclass
class CloudClearedNotice:
    """云端被清空后本机自动复位的提示（同步流程的一个结果）。"""

    message: str = "云端数据已被清空，请点「初始同步」重新建立同步状态"
    cloud_cleared: bool = True
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def describe(self) -> str:
        return self.message

    def distinct_errors(self) -> list[str]:
        return []


def reset_local_after_cloud_clear() -> str:
    """云端被清空后，把本机退回「未建立同步状态」。"""
    clear_local_sync_state()
    set_sync_established(False)
    logger.info("[sync] 检测到云端已清空，本机同步状态已复位为未建立")
    return str(CloudClearedNotice().message)


def _finish(report: InitialSyncReport) -> InitialSyncReport:
    if not report.errors:
        set_sync_established(True)
        report.established = True
    return report


def _rows(definition, users, documents, overview) -> list[tuple[str, int, int, int]]:
    return [
        ("定义", definition.pushed, definition.pulled, len(definition.errors)),
        ("用户", users.pushed, users.pulled, len(users.errors)),
        ("文档", len(documents.pushed), len(documents.pulled), len(documents.errors)),
        ("总览", overview.snapshots or overview.pushed, overview.pulled, len(overview.errors)),
    ]


def initial_sync(token: str, on_progress=None, stop=None) -> InitialSyncReport:
    """执行一次初始同步；成功返回 ``established=True``。"""
    report = InitialSyncReport()
    try:
        state = post("/api/sync/state", {}, token)
    except SyncError as error:
        report.errors.append(str(error))
        return report
    action = classify_initial_action(
        state, local_has_entries=local_profile_has_entries())
    if action == "replace_empty":
        return _publish_local_baseline(token, report)
    if action == "follow":
        return _follow_cloud(token, state, report, on_progress, stop)
    return _seed_or_repair(token, state, report, on_progress, stop)


def _publish_local_baseline(token: str, report: InitialSyncReport) -> InitialSyncReport:
    """空基线不跟随。用本机总览再写一份快照，本地用户目录保持不动。"""
    from ..profile.snapshot_sync import refresh_snapshot

    snapshot = refresh_snapshot(token, allow_empty=False)
    report.rows = [("总览", snapshot.snapshots, snapshot.pulled, len(snapshot.errors))]
    report.errors = list(snapshot.errors)
    if not report.errors and snapshot.snapshots < 1:
        report.errors.append("快照未建立")
    return _finish(report)


def _seed_or_repair(token: str, state: dict, report: InitialSyncReport,
                    on_progress, stop) -> InitialSyncReport:
    from ..profile.snapshot_sync import refresh_snapshot
    from .definition_sync import sync_definition
    from .engine import sync_documents
    from .users_sync import sync_users

    def progress(data) -> None:
        if on_progress:
            on_progress(dict(data))

    try:
        claim = post("/api/sync/claim_init", {}, token)
    except SyncError as error:
        report.errors.append(str(error))
        return report
    status = claim.get("status")
    if status == "busy":
        report.errors.append("另一台电脑正在建立初始同步，请稍后再试")
        return report
    if status != "owner":
        report.errors.append("云端已由另一台电脑建立，请再点一次「初始同步」")
        return report

    repair_only = cloud_has_seed_data(state) and not claim.get("seed_owner")
    if repair_only:
        snapshot = refresh_snapshot(token, allow_empty=True)
        report.rows = [("总览", snapshot.snapshots, snapshot.pulled, len(snapshot.errors))]
        report.errors = list(snapshot.errors)
        if not report.errors and snapshot.snapshots < 1:
            report.errors.append("快照未建立")
        return _finish(report)

    definition = sync_definition(token, on_progress=progress)
    users = sync_users(token, on_progress=progress, stop=stop)
    documents = sync_documents(token, on_progress=progress, stop=stop, force_full=True)
    report.errors = [*definition.errors, *users.errors, *documents.errors]
    if report.errors:
        # 播种半成品不建快照，原播种机下次仍可重传名册。这里没有剪切本地文件。
        report.rows = [
            ("定义", definition.pushed, definition.pulled, len(definition.errors)),
            ("用户", users.pushed, users.pulled, len(users.errors)),
            ("文档", len(documents.pushed), len(documents.pulled), len(documents.errors)),
            ("总览", 0, 0, 0),
        ]
        return report
    snapshot = refresh_snapshot(token, allow_empty=True)
    report.rows = _rows(definition, users, documents, snapshot)
    report.errors = list(snapshot.errors)
    if not report.errors and snapshot.snapshots < 1:
        report.errors.append("快照未建立")
    return _finish(report)


def _follow_cloud(token: str, state: dict, report: InitialSyncReport,
                  on_progress, stop) -> InitialSyncReport:
    from ..account.service import profile_sync_state, save_profile_sync_state
    from ..profile import repository
    from ..profile.snapshot_sync import pull_and_replay
    from .definition_sync import sync_definition
    from .engine import sync_documents
    from .users_sync import _roster, sync_users

    def progress(data) -> None:
        if on_progress:
            on_progress(dict(data))

    backup = begin_follow_backup()
    report.backup_dir = str(backup)
    try:
        clear_local_sync_state()
        clear_local_roster()
        definition = sync_definition(token, on_progress=progress)
        users = sync_users(token, on_progress=progress, stop=stop)
        documents = sync_documents(token, on_progress=progress, stop=stop)
        # 只拉不推：初始同步以云端为准。回放会触发 profile 引擎物化周期锚点并
        # 写本地历史，那些是云数据的派生，不是本机真实编辑，禁止回推云端。
        profile = pull_and_replay(token, on_progress=progress, stop=stop)
        cloud_users = state.get("users")
        report.rows = [
            ("定义", definition.pushed, definition.pulled, len(definition.errors)),
            ("用户", users.pushed, users.pulled, len(users.errors)),
            ("文档", len(documents.pushed), len(documents.pulled),
             len(documents.errors)),
            ("总览", profile.pushed, profile.pulled, len(profile.errors)),
        ]
        report.errors = [*definition.errors, *users.errors,
                         *documents.errors, *profile.errors]
        if (not report.errors and isinstance(cloud_users, int) and cloud_users > 0
                and not _roster()):
            report.errors.append("云端有用户，但本地名册仍为空")
        if report.errors:
            raise RuntimeError("跟随云端未完成")
        sync_state = profile_sync_state()
        sync_state["local_history_id"] = repository.db_max_history_id()
        save_profile_sync_state(sync_state)
    except Exception as error:  # noqa: BLE001 - 备份已经剪走，失败必须搬回
        if not report.errors:
            report.errors.append(str(error))
        restored = restore_local_profile_data(backup)
        if restored:
            report.errors.append(restored)
        else:
            report.backup_dir = ""
        return report
    return _finish(report)
