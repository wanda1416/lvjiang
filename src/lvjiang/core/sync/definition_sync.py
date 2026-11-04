"""profile 定义同步 —— profile.yaml 走 definition 专用表（不是文档）。

- 上行：本地文件 sha ≠ 云端 sha 即推送（携带 ``expected_sha`` 乐观锁，
  冲突时拉云端新版本就地落盘——定义以最后写入者为准）。
- 内容未变整个跳过（不产生任何云端写入）。
- 下行：本地文件缺失时从云端拉回（正常情况本地永远存在）。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from ..account.service import profile_sync_state, save_profile_sync_state
from .client import SyncError, post


@dataclass
class DefinitionSyncReport:
    pushed: int = 0
    pulled: int = 0
    errors: list[str] = field(default_factory=list)


def _local_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sync_definition(token: str, yaml_path: Path | None = None,
                    on_progress=None) -> DefinitionSyncReport:
    """一轮定义同步：内容签名一致时零请求。"""
    if yaml_path is None:
        from ...constants import SESSION_CONFIG_DIR

        yaml_path = SESSION_CONFIG_DIR / "profile.yaml"
    report = DefinitionSyncReport()
    if on_progress:
        on_progress({"step": "definition", "detail": "检查"})
    state = profile_sync_state()

    if not yaml_path.is_file():
        # 本地无定义（初始同步备份后 / 全新实例）：从远端拉回并重载 schema。
        _pull_to_local(token, yaml_path, report)
        return report

    local_sha = _local_sha256(yaml_path)
    if local_sha == state.get("definition_sha"):
        return report          # 与上次成功同步一致：跳过

    try:
        remote = post("/api/sync/profile/definition", {}, token).get("definition")
    except SyncError as error:
        report.errors.append(str(error))
        return report

    remote_sha = remote.get("sha256") if isinstance(remote, dict) else None
    if remote_sha == local_sha:
        state["definition_sha"] = local_sha      # 云端已是同内容（他机推过）
        save_profile_sync_state(state)
        return report

    try:
        body = {"payload_base64": base64.b64encode(gzip.compress(yaml_path.read_bytes())).decode(),
                "sha256": local_sha, "expected_sha": remote_sha}
        post("/api/sync/profile/definition", body, token)
        state["definition_sha"] = local_sha
        save_profile_sync_state(state)
        report.pushed = 1
        if on_progress:
            on_progress({"step": "definition", "detail": "已上传",
                         "pushed": 1})
    except SyncError as error:
        # 乐观锁冲突（他机刚推）或网络失败：拉云端版本落盘，以云端为准
        report.errors.append(str(error))
        _pull_to_local(token, yaml_path, report)
    return report


def _pull_to_local(token: str, yaml_path: Path, report: DefinitionSyncReport) -> None:
    try:
        remote = post("/api/sync/profile/definition", {}, token).get("definition")
        if not isinstance(remote, dict):
            return
        raw = base64.b64decode(remote["payload_base64"])
        if remote.get("zipped"):
            raw = gzip.decompress(raw)
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_bytes(raw)
        state = profile_sync_state()
        state["definition_sha"] = remote.get("sha256") or _local_sha256(yaml_path)
        save_profile_sync_state(state)
        # 定义必须**先提交到内存**再进入账本回放：同一同步周期里，回放端
        # 若拿着旧 schema 处理新 key（改名场景），引擎会把新 key 当未知项
        # 忽略、UI 不显示——文件新内存旧正是状态不一致的来源。
        from ..profile.schema import reload_profile_config
        reload_profile_config()
        report.pulled = 1
        logger.info("[definition] 已拉取云端定义（本地让位，schema 已重载）")
    except (SyncError, OSError, ValueError) as error:
        report.errors.append(str(error))
