"""用户总览页面结构的独立上传 / 下载。

与 ``profile.yaml`` 定义不同，页面结构**不进常规同步**：不入 manifest，不被
启动同步或定时同步调用，只有用户点「上传页面结构 / 下载页面结构」时才动作。

远端记录的「写入来源」由服务端按认证会话填写（``profile_layout`` 表的
``origin_*`` 三列），客户端只读它来判断「远端是不是别的实例写的」——文件本身
不带机器信息，避免拷贝或编辑后出处失真。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from ..account.identity import stable_instance_id
from ..profile import layout
from .client import SyncConflict, SyncError, post

_ROUTE = "/api/sync/profile/layout"

#: 结构文件再大也不该超过这个量级；超了说明文件已不是「结构」。
_MAX_PAYLOAD_BYTES = 4 * 1024 * 1024


class LayoutConflict(Exception):
    """需要用户确认才能继续：远端来自别的实例，或上传期间被改写。"""

    def __init__(self, remote: RemoteLayout | None, message: str = ""):
        super().__init__(message or "远端已有其它设备写入的页面结构")
        self.remote = remote


@dataclass(frozen=True)
class RemoteLayout:
    """云端页面结构的元信息（payload 按需解压）。"""

    sha256: str
    updated_at: str = ""
    origin_instance_id: str = ""
    origin_name: str = ""
    platform: str = ""
    size: int = 0
    payload_base64: str = ""
    zipped: bool = False

    @property
    def short_sha(self) -> str:
        return self.sha256[:8]

    @property
    def origin_label(self) -> str:
        return self.origin_name or "未记录设备"

    def is_from_this_instance(self) -> bool:
        return bool(self.origin_instance_id) and self.origin_instance_id == stable_instance_id()

    def describe(self) -> str:
        """一行中文摘要，供确认框直接展示。"""
        parts = [f"来源 {self.origin_label}"]
        if self.updated_at:
            parts.append(f"时间 {self.updated_at}")
        if self.size:
            parts.append(f"{self.size} 字节")
        parts.append(f"摘要 {self.short_sha}")
        return "，".join(parts)

    def decode(self) -> bytes:
        """解出原始结构文件字节（服务端原样搬运，解压在这一侧）。"""
        raw = base64.b64decode(self.payload_base64 or "")
        if self.zipped:
            raw = gzip.decompress(raw)
        return raw


@dataclass(frozen=True)
class UploadOutcome:
    """上传结果；``unchanged`` 表示云端与本地内容已经一致。"""

    status: str
    remote: RemoteLayout | None = None


def _parse(remote: object) -> RemoteLayout | None:
    if not isinstance(remote, dict):
        return None
    sha = remote.get("sha256")
    if not isinstance(sha, str) or not sha:
        return None
    return RemoteLayout(
        sha256=sha,
        updated_at=str(remote.get("updated_at") or ""),
        origin_instance_id=str(remote.get("origin_instance_id") or ""),
        origin_name=str(remote.get("origin_name") or ""),
        platform=str(remote.get("platform") or ""),
        size=int(remote.get("size") or 0),
        payload_base64=str(remote.get("payload_base64") or ""),
        zipped=bool(remote.get("zipped")),
    )


def fetch_remote(token: str) -> RemoteLayout | None:
    """读取云端页面结构元信息；云端尚无结构时返回 ``None``。"""
    return _parse(post(_ROUTE, {}, token).get("layout"))


def local_sha256(path: Path | None = None) -> str | None:
    target = path or layout.layout_path()
    if not target.exists():
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest()


def local_document(path: Path | None = None) -> layout.ProfileLayout:
    """本机结构（缺失时是空结构，不落盘）。"""
    return layout.load(path=path)


def upload(token: str, *, force: bool = False, path: Path | None = None) -> UploadOutcome:
    """把本机结构传到云端。

    ``force=False`` 时，若远端存在且不是本实例写的，抛 :class:`LayoutConflict`
    让界面先问「是否覆盖」；确认后带 ``force=True`` 再来一次。并发写入由
    ``expected_sha`` 拦住：远端在确认期间变了就再抛一次冲突，绝不静默覆盖。
    """
    target = path or layout.layout_path()
    if not target.exists():
        raise SyncError("本机还没有页面结构，先在总览里添加分组再上传")
    payload_bytes = target.read_bytes()
    if len(payload_bytes) > _MAX_PAYLOAD_BYTES:
        raise SyncError("页面结构文件异常过大，已放弃上传")

    remote = fetch_remote(token)
    local_sha = hashlib.sha256(payload_bytes).hexdigest()
    if remote is not None and remote.sha256 == local_sha:
        return UploadOutcome("unchanged", remote)
    if remote is not None and not force and not remote.is_from_this_instance():
        raise LayoutConflict(remote)

    body = {
        "payload_base64": base64.b64encode(gzip.compress(payload_bytes)).decode(),
        "sha256": local_sha,
    }
    if remote is not None:
        body["expected_sha"] = remote.sha256
    try:
        result = post(_ROUTE, body, token)
    except SyncConflict as conflict:
        detail = conflict.remote if isinstance(conflict.remote, dict) else {}
        raise LayoutConflict(
            _parse(detail.get("layout")), "云端页面结构在你确认期间已被改写"
        ) from None
    logger.info("页面结构已上传：{}（{} 字节）", local_sha[:8], len(payload_bytes))
    return UploadOutcome("uploaded", _parse(result.get("layout")) or remote)


def download(token: str, *, remote: RemoteLayout | None = None) -> tuple[RemoteLayout, Path | None]:
    """把云端结构下载到本机。

    ``remote`` 已由界面取过一次时直接复用，避免「确认框显示一份、实际下载另一份」。

    返回 ``(远端元信息, 备份路径)``。先解析校验再动文件：远端内容坏了、版本
    比本程序新、或不是合法结构时抛 ``ValueError`` / ``SyncError``，本机文件
    保持原样。
    """
    current = remote if remote is not None else fetch_remote(token)
    if current is None:
        raise SyncError("云端还没有页面结构")
    try:
        raw = current.decode()
    except (ValueError, OSError) as exc:
        raise ValueError(f"云端页面结构无法解码：{exc}") from exc
    doc = layout.parse_bytes(raw)
    backup = layout.backup_current(reason="before-download")
    layout.save(doc)
    logger.info(
        "页面结构已下载：{}（{} 个分组），原结构备份: {}",
        current.short_sha, len(doc.overview.groups), backup or "无",
    )
    return current, backup
