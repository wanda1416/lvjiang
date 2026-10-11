"""在线增量包协调：下载不触碰生效层，安装成功后立即切换缓存。"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loguru import logger

from .member_download import MemberDownload, fetch_member_update
from .protected_remote import ProtectedAccess
from .update_download import PublicDownload, fetch_public_update
from .update_store import UpdateStore

MAX_MANIFEST_BYTES = 256 * 1024
_USER_AGENT = "lvjiang-config-update"


class RemoteConfigError(RuntimeError):
    """下载或安装未完成，原配置继续可用。"""


@dataclass(frozen=True)
class SyncJob:
    config: Path | None = None
    app_version: str = ""
    baseline_sha256: str = ""
    enabled: bool = True
    protected: ProtectedAccess | None = field(default=None, repr=False)
    member_access_known: bool = True
    app_ids: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.config is None


@dataclass(frozen=True)
class SyncResult:
    job: SyncJob
    public: PublicDownload | None = None
    member: MemberDownload | None = None
    errors: tuple[str, ...] = ()
    performed: bool = True
    validated_sha256: str = ""
    local_stamp: tuple[tuple[str, int, int], ...] = ()
    candidate: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False, compare=False)


def _local_stamp(root: Path) -> tuple[tuple[str, int, int], ...]:
    """只对实际配置取印记，任务写日志或 Git 更新不能让候选校验作废。"""
    from .runtime_snapshot import configuration_roots
    files: list[Path] = []
    for name in configuration_roots(root.parent / "system"):
        source = root / name
        if source.is_dir():
            files.extend(path for path in source.rglob("*") if path.is_file())
        elif source.is_file():
            files.append(source)
    return tuple((path.relative_to(root).as_posix(), stat.st_mtime_ns, stat.st_size)
                 for path in sorted(files) for stat in (path.stat(),))


def _fetch_bytes(url: str, *, max_bytes: int, timeout: float,
                 etag: str = "") -> tuple[bytes, str, bool]:
    """受限 HTTPS 下载；不把失败当成空清单或撤回。"""
    from urllib.parse import urlparse
    if urlparse(url).scheme != "https":
        raise RemoteConfigError("在线内容必须通过 HTTPS 下载")
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    if etag:
        request.add_header("If-None-Match", etag)
    try:
        with urlopen(request, timeout=timeout) as response:
            if urlparse(response.geturl()).scheme != "https":
                raise RemoteConfigError("拒绝非 HTTPS 重定向")
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise RemoteConfigError(f"响应体超过 {max_bytes} 字节")
            return payload, response.headers.get("ETag", ""), False
    except HTTPError as exc:
        if exc.code == 304:
            return b"", etag, True
        raise RemoteConfigError(f"请求失败 HTTP {exc.code}: {url}") from exc
    except RemoteConfigError:
        raise
    except Exception as exc:
        raise RemoteConfigError(f"请求失败: {url} ({exc})") from exc


def is_enabled() -> bool:
    from . import load_user_config
    network = load_user_config().network
    return bool(network.remote_config) and not network.offline


def _installed_store() -> UpdateStore | None:
    from ..update import get_version
    from ..update_bundle import series
    from .resolver import get_resolver
    resolver = get_resolver()
    # 开发工作区不允许在线安装器覆盖 git 管理的配置。
    if resolver.is_dev_mode():
        return None
    path = resolver.system_dir.parent / "system-baseline.json"
    if not path.is_file():
        return None
    identity = json.loads(path.read_text(encoding="utf-8"))
    version = get_version()
    if identity.get("app_series") != series(version):
        raise ValueError("预置配置与当前应用版本不匹配，请重新安装完整应用")
    return UpdateStore(path.parent, version, identity["sha256"])


def get_config_version() -> int:
    store = _installed_store()
    if store is None:
        return 0
    from ..update_bundle import file_hashes, read_tree
    with store.locked():
        state = store._state()
        if state.get("files") != file_hashes(read_tree(store.system)):
            return 0
        return int(state.get("revision", 0))


def get_last_synced_at() -> str:
    store = _installed_store()
    if store is None:
        return ""
    return str(store._state().get("installed_at", ""))


def recover_installation() -> None:
    """启动时只恢复未提交的安装事务；不提升旧 remote 暂存目录。"""
    store = _installed_store()
    if store is not None:
        store.recover()
        from .protected_remote import authorized
        member = store._state().get("member", {})
        invalid = member.get("files") and not authorized(member)
        if invalid:
            # 在读取场景/工作流之前撤下失效会员内容，不依赖网络是否可用。
            store.install(member_files={}, member_metadata={},
                          validate=lambda _path: None, activate=lambda *_args: None)
        else:
            store.install(validate=lambda _path: None, activate=lambda *_args: None)


def build_sync_job() -> SyncJob:
    from ...apps import get_registered_app_ids
    from ..update import get_version
    from .protected_remote import snapshot
    store = _installed_store()
    if store is None:
        return SyncJob()
    try:
        access = snapshot()
        known = True
    except (ValueError, OSError) as error:
        logger.warning("[在线配置] 无法确定会员授权，保留当前内容: {}", error)
        access, known = None, False
    return SyncJob(config=store.config, app_version=get_version(),
                   baseline_sha256=store.baseline_sha256, enabled=is_enabled(),
                   protected=access, member_access_known=known,
                   app_ids=get_registered_app_ids())


def run_sync(job: SyncJob, *, timeout: float = 10.0) -> SyncResult:
    """后台只下载完整候选，两个来源的传输失败相互隔离。"""
    if job.is_empty:
        return SyncResult(job, performed=False)
    config = job.config
    assert config is not None
    public = None
    member = None
    errors = []
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    try:
        store.prune_archives()
    except (OSError, ValueError) as error:
        logger.warning("[在线配置] 归档暂未清理，继续本轮同步: {}", error)
    if job.enabled:
        try:
            public = fetch_public_update(job.app_version, fetch=_fetch_bytes, timeout=timeout,
                                         cache_index=lambda raw: store.cache_index("public", raw))
        except Exception as error:
            errors.append(f"公开更新未获取，保留已安装版本: {error}")
    if not job.enabled:
        member = MemberDownload({}, {})
    elif job.member_access_known:
        try:
            member = fetch_member_update(job.protected, job.app_version, fetch=_fetch_bytes, timeout=timeout,
                                         cache_index=lambda raw: store.cache_index("member", raw))
        except Exception as error:
            errors.append(f"会员更新未获取，保留已安装版本: {error}")
    if public is None and member is None:
        return SyncResult(job, errors=tuple(errors))
    from ..update_bundle import read_tree, tree_digest
    from .update_activation import validate_candidate
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    stamp = _local_stamp(config / "local")
    validated = ""
    candidate = None
    def retain(owner):
        nonlocal candidate
        candidate = owner
    def validate(path):
        nonlocal validated
        validate_candidate(path, config / "local", job.app_ids)
        validated = tree_digest(read_tree(path))
    store.install(public.manifest if public else None, public.archive if public else None,
                  manifest_json=public.manifest_json if public and public.manifest_json else None,
                  index_snapshots=_index_snapshots(public, member),
                  reset_public=not job.enabled or bool(public is not None and public.manifest is None),
                  member_files=member.files if member else None,
                  member_metadata=member.metadata if member else None,
                  preview=True, retain_candidate=retain, validate=validate, activate=lambda *_: None)
    if stamp != _local_stamp(config / "local"):
        raise RemoteConfigError("校验期间本地配置已变化，请重新同步")
    return SyncResult(job, public, member, tuple(errors), validated_sha256=validated,
                      local_stamp=stamp, candidate=candidate)


def apply_outcome(result: SyncResult) -> tuple[str, ...]:
    """主线程原子安装并刷新；不修改用户 local、session 或活动选择。"""
    from .protected_remote import authorized, snapshot
    from .update_activation import activate_configuration
    job = result.job
    for message in result.errors:
        logger.info("[在线配置] {}", message)
    if not result.performed or job.config is None:
        return ()
    config = job.config
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    member = result.member
    enabled = is_enabled()
    try:
        access = snapshot()
        if (access.code_hash if access else None) != (job.protected.code_hash if job.protected else None):
            member = None
    except (ValueError, OSError):
        member = None
    # 下载期间用户可能切换授权；不能把旧授权的包安装给新上下文。
    if member is not None and member.files and not authorized(member.metadata):
        logger.info("[在线配置] 授权已变化，忽略本次会员候选")
        member = None
    if not enabled:
        member = MemberDownload({}, {})
    elif member is None and job.member_access_known:
        state = store._state().get("member", {})
        if state.get("files") and not authorized(state):
            member = MemberDownload({}, {})
    if result.public is None and member is None and enabled:
        return ()
    if member is not None and member.encrypted:
        from .protected_bundle import valid_key_id
        key_id = member.metadata.get("key_id")
        if not isinstance(key_id, str) or not valid_key_id(key_id):
            raise ValueError("会员归档标识非法")
        store.cache_member(member.metadata, member.manifest_json, member.encrypted)
    public = result.public if enabled else None
    from ..update_bundle import read_tree, tree_digest
    def validate(path):
        if tree_digest(read_tree(path)) != result.validated_sha256 or _local_stamp(config / "local") != result.local_stamp:
            raise RemoteConfigError("候选或本地配置已变化，保留现有配置，请重新同步")
    changed = store.install(
        public.manifest if public else None, public.archive if public else None,
        manifest_json=public.manifest_json if public and public.manifest_json else None,
        index_snapshots=_index_snapshots(public, member),
        reset_public=not enabled or bool(public is not None and public.manifest is None),
        member_files=member.files if member is not None else None,
        member_metadata=member.metadata if member is not None else None,
        prepared=result.candidate,
        validate=validate,
        activate=activate_configuration)
    if changed:
        logger.info("[在线配置] 已生效：{} 个文件；运行中的任务继续使用启动快照", len(changed))
    return changed


def sync_now(*, timeout: float = 10.0) -> SyncResult:
    result = run_sync(build_sync_job(), timeout=timeout)
    apply_outcome(result)
    return result


def _index_snapshots(public: PublicDownload | None, member: MemberDownload | None) -> dict[str, bytes]:
    return {channel: download.index_json for channel, download in (("public", public), ("member", member))
            if download is not None and download.index_json}
