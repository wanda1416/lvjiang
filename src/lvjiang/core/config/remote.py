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
    candidate: tempfile.TemporaryDirectory[str] | None = field(default=None, repr=False, compare=False)


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
    from ..access import is_readonly
    if is_readonly():
        return None  # 安装及归档写入只属于主节点。
    from ..paths import session_directory
    from ..update import get_version
    from ..update_bundle import series
    from .resolver import get_resolver
    resolver = get_resolver()
    # 开发工作区不允许在线安装器覆盖 git 管理的配置。
    if resolver.is_dev_mode():
        return None
    config = resolver.config_dir
    path = session_directory(config) / "baseline.json"
    if not path.is_file():
        logger.debug("在线配置基底记录不存在，跳过在线恢复；请使用完整安装包补齐 {}", path)
        return None
    try:
        identity = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.debug("在线配置基底记录不可读，保留当前配置并跳过在线恢复：{}", error)
        return None
    if not isinstance(identity, dict) or not isinstance(identity.get("sha256"), str) or not identity["sha256"]:
        logger.debug("在线配置基底记录缺少摘要，保留当前配置并跳过在线恢复：{}", path)
        return None
    version = get_version()
    if identity.get("app_series") != series(version):
        logger.debug("在线配置基底版本与应用版本不匹配，保留当前配置并跳过在线恢复")
        return None
    return UpdateStore(config, version, identity["sha256"])


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
                          activate=lambda *_args: None)
        else:
            store.install(activate=lambda *_args: None)


def prepare_task_installation() -> None:
    """任务前处理安装恢复及会员到期；不决定任务快照是否启用。"""
    try:
        store = _installed_store()
        if store is None:
            return
        with store.locked():
            store._recover()
            member = store._state().get("member", {})
        if member.get("files"):
            from .protected_remote import authorized
            if not authorized(member):
                store.install(member_files={}, member_metadata={}, activate=lambda *_args: None)
    except (OSError, ValueError) as error:
        logger.debug("任务前安装状态暂不可恢复，仍加载当前磁盘配置：{}", error)


def build_sync_job() -> SyncJob:
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
                   protected=access, member_access_known=known)


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
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    candidate = None
    def retain(owner):
        nonlocal candidate
        candidate = owner
    store.install(public.manifest if public else None, public.archive if public else None,
                  manifest_json=public.manifest_json if public and public.manifest_json else None,
                  index_snapshots=_index_snapshots(public, member),
                  reset_public=not job.enabled or bool(public is not None and public.manifest is None),
                  member_files=member.files if member else None,
                  member_metadata=member.metadata if member else None,
                  preview=True, retain_candidate=retain, activate=lambda *_: None)
    return SyncResult(job, public, member, tuple(errors), candidate=candidate)


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
    changed = store.install(
        public.manifest if public else None, public.archive if public else None,
        manifest_json=public.manifest_json if public and public.manifest_json else None,
        index_snapshots=_index_snapshots(public, member),
        reset_public=not enabled or bool(public is not None and public.manifest is None),
        member_files=member.files if member is not None else None,
        member_metadata=member.metadata if member is not None else None,
        prepared=result.candidate,
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
