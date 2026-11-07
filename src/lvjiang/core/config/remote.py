"""在线增量包协调：后台只下载并暂存，安装只在进程读取配置之前进行。"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loguru import logger

from ..update_bundle import digest
from .member_download import MemberDownload, fetch_member_update, install_staged_member
from .protected_remote import ProtectedAccess
from .update_download import PublicDownload, fetch_public_update
from .update_store import UpdateStore

MAX_MANIFEST_BYTES = 256 * 1024
_USER_AGENT = "lvjiang-config-update"
#: 后台下载的单次读取超时与总预算。启动不再等它，所以比同步时代宽一些。
BACKGROUND_TIMEOUT = 30.0
DOWNLOAD_CHUNK_BYTES = 64 * 1024


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


def _fetch_bytes(url: str, *, max_bytes: int, timeout: float,
                 etag: str = "", cancelled: Callable[[], bool] | None = None,
                 ) -> tuple[bytes, str, bool]:
    """受限 HTTPS 下载；不把失败当成空清单或撤回。

    分块读并在块之间检查 ``cancelled``：只靠 socket 超时无法在应用退出时
    让后台线程及时停下，而对端正常发包时单次 read 会一直阻塞。
    """
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
            chunks: list[bytes] = []
            received = 0
            while True:
                if cancelled is not None and cancelled():
                    raise RemoteConfigError("在线配置下载已取消")
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise RemoteConfigError(f"响应体超过 {max_bytes} 字节")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("ETag", ""), False
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
    from ..telemetry.consent import is_offline_mode
    return bool(network.remote_config) and not is_offline_mode()


def _installed_store() -> UpdateStore | None:
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
    return int(store._state().get("revision", 0))


def get_last_synced_at() -> str:
    store = _installed_store()
    if store is None:
        return ""
    return str(store._state().get("installed_at", ""))


def recover_installation() -> None:
    """回滚未提交事务，并撤下未授权的会员层。不下载。"""
    store = _installed_store()
    if store is None:
        return
    store.recover()
    from .protected_remote import authorized
    member = store._state().get("member", {})
    if member.get("files") and not authorized(member):
        # 在读取场景/工作流之前撤下失效会员内容，不依赖网络是否可用。
        store.install(member_files={}, member_metadata={}, activate=lambda *_args: None)


def prepare_startup_configuration() -> None:
    """读取场景与布局之前：先回滚，再安装上次后台下载好的候选。

    不再联网：下载由启动后的后台线程完成，下载到的内容留到下次启动生效。
    开发模式、没有基底时保留当前磁盘配置；此处还没有场景注册表，
    安装不刷新 OCR、模板或插件缓存。
    """
    recover_installation()
    install_pending()


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


def run_sync(job: SyncJob, *, timeout: float = 10.0,
             cancelled: Callable[[], bool] | None = None) -> SyncResult:
    """只下载完整候选，两个来源的传输失败相互隔离。

    ``cancelled`` 让后台线程能在应用退出时中断正在进行的传输。
    """
    if job.is_empty:
        return SyncResult(job, performed=False)
    fetch = _fetch_bytes if cancelled is None else partial(_fetch_bytes, cancelled=cancelled)
    config = job.config
    assert config is not None
    public = None
    member = None
    errors = []
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    state = store._state()
    local_public = int(state.get("revision", 0))
    local_member = int(state.get("member_index_revision", 0))
    try:
        store.prune_archives()
    except (OSError, ValueError) as error:
        logger.warning("[在线配置] 归档暂未清理，继续本轮同步: {}", error)
    if job.enabled:
        try:
            public = fetch_public_update(job.app_version, fetch=fetch, timeout=timeout,
                                         known_revision=local_public,
                                         cache_index=lambda raw: store.cache_index("public", raw))
            if public is not None and public.unchanged:
                logger.debug("[在线配置] 公开配置仍为 r{}，本轮不下载", local_public)
                public = None
        except Exception as error:
            errors.append(f"公开更新未获取，保留已安装版本: {error}")
    if not job.enabled:
        member = MemberDownload({}, {})
    elif job.member_access_known:
        try:
            member = fetch_member_update(job.protected, job.app_version, fetch=fetch, timeout=timeout,
                                         known_revision=local_member,
                                         cache_index=lambda raw: store.cache_index("member", raw))
            if member is not None and member.unchanged:
                logger.debug("[在线配置] 会员配置仍为 r{}，本轮不下载", local_member)
                member = None
        except Exception as error:
            errors.append(f"会员更新未获取，保留已安装版本: {error}")
    return SyncResult(job, public, member, tuple(errors))


def apply_outcome(result: SyncResult) -> tuple[str, ...]:
    """把已下载的候选安装到 system。调用时还不能有场景或任务读者。"""
    from .protected_remote import authorized
    job = result.job
    for message in result.errors:
        logger.info("[在线配置] {}", message)
    if not result.performed or job.config is None:
        return ()
    config = job.config
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    member = result.member
    enabled = is_enabled()
    if not enabled:
        member = MemberDownload({}, {})
    elif member is not None and member.files and not authorized(member.metadata):
        logger.info("[在线配置] 会员授权已变化，忽略本次会员候选")
        member = None
    elif member is None and job.member_access_known:
        state = store._state().get("member", {})
        if state.get("files") and not authorized(state):
            member = MemberDownload({}, {})
    if result.public is None and member is None and enabled:
        return ()
    if member is not None and member.encrypted:
        store.cache_member(member.metadata, member.manifest_json, member.encrypted)
    public = result.public if enabled else None
    changed = store.install(
        public.manifest if public else None, public.archive if public else None,
        manifest_json=public.manifest_json if public and public.manifest_json else None,
        reset_public=not enabled or bool(public is not None and public.manifest is None),
        member_files=member.files if member is not None else None,
        member_metadata=member.metadata if member is not None else None,
        activate=lambda *_args: None)
    if changed:
        logger.info("[在线配置] 已安装：{} 个文件，本次启动读取新配置", len(changed))
    return changed


def stage_outcome(result: SyncResult) -> dict[str, int]:
    """把已下载的候选落进暂存区并登记 pending；不安装、不触碰 system。"""
    job = result.job
    if not result.performed or job.config is None:
        return {}
    store = UpdateStore(job.config, job.app_version, job.baseline_sha256)
    staged: dict[str, int] = {}
    public = result.public
    if public is not None and public.manifest is not None and public.archive:
        revision = int(public.manifest["revision"])
        store.cache_public(public.manifest_json, public.archive)
        store.mark_pending(public=(revision, digest(public.manifest_json)))
        staged["revision"] = revision
        logger.info("[在线配置] 已下载公开修订 r{}，下次启动安装", revision)
    member = result.member
    if member is not None and member.encrypted and member.metadata.get("package_version"):
        manifest_sha256 = str(member.metadata.get("manifest_sha256")
                              or digest(member.manifest_json))
        store.cache_member(member.metadata, member.manifest_json, member.encrypted)
        store.mark_pending(member=(int(member.metadata["package_version"]), manifest_sha256))
        staged["member"] = int(member.metadata["package_version"])
        logger.info("[在线配置] 已下载会员包 v{}，下次启动安装", member.metadata["package_version"])
    return staged


def _staged_public(store: UpdateStore) -> PublicDownload | None:
    staged = store.pending_public()
    if staged is None:
        return None
    _manifest, archive, manifest_json = staged
    return PublicDownload(manifest_json, archive)


def _staged_member(store: UpdateStore, job: SyncJob, *, timeout: float) -> MemberDownload | None:
    """把暂存的会员候选还原成可安装结果。

    暂存只放密文：解密要用授权接口下发的一次性密钥，所以这里仍要请求一次授权
    （小请求），而不是把会员明文留在磁盘上。授权未通过或请求失败时返回 None，
    候选保留到下次启动重试。
    """
    staged = store.pending_member()
    if staged is None:
        return None
    index_json, manifest_json, encrypted = staged
    from .protected_remote import snapshot
    try:
        access = snapshot()
    except (ValueError, OSError) as error:
        logger.warning("[在线配置] 无法确定会员授权，暂不安装已下载的会员包: {}", error)
        return None
    try:
        return install_staged_member(access, job.app_version, index_json=index_json,
                                     manifest_json=manifest_json, encrypted=encrypted,
                                     timeout=timeout)
    except Exception as error:
        logger.info("[在线配置] 已下载的会员包本次未安装，保留到下次启动: {}", error)
        return None


def install_pending(*, timeout: float = 3.0) -> tuple[str, ...]:
    """安装上次后台下载的候选：不下载公开包，会员包只取一次授权。"""
    job = build_sync_job()
    config = job.config
    if config is None:
        return ()
    store = UpdateStore(config, job.app_version, job.baseline_sha256)
    if not is_enabled():
        # 关闭在线配置或进入离线模式：下次启动恢复官方预置并撤下会员内容。
        store.clear_pending(public=True, member=True)
        return apply_outcome(SyncResult(job, member=MemberDownload({}, {})))
    public = _staged_public(store)
    member = _staged_member(store, job, timeout=timeout)
    if public is None and member is None:
        if store.pending_public() is None:
            store.clear_pending(public=True)
        if store.pending_member() is None:
            store.clear_pending(member=True)
        return ()
    result = SyncResult(job, public=public, member=member)
    for message in result.errors:
        logger.info("[在线配置] {}", message)
    try:
        changed = apply_outcome(result)
    except Exception as error:
        logger.warning("[在线配置] 已下载的配置本次安装失败，保留到下次启动: {}", error)
        return ()
    store.clear_pending(public=True, member=member is not None)
    return changed


@dataclass
class _BackgroundSync:
    thread: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)


_background = _BackgroundSync()


def _background_worker(stop: threading.Event, timeout: float) -> None:
    try:
        result = run_sync(build_sync_job(), timeout=timeout, cancelled=stop.is_set)
        if stop.is_set():
            logger.debug("[在线配置] 后台下载已取消，不写入暂存")
            return
        stage_outcome(result)
    except Exception as error:  # noqa: BLE001
        logger.warning("[在线配置] 后台下载未完成，下次启动再试: {}", error)


def start_background_sync(*, timeout: float = BACKGROUND_TIMEOUT) -> bool:
    """启动后台下载线程；已在运行或不需要下载时返回 False。

    只在窗口出现之后、确认是主实例时调用：下载不触碰生效层，装好也只登记为待安装。
    """
    thread = _background.thread
    if thread is not None and thread.is_alive():
        return False
    _background.stop = threading.Event()
    thread = threading.Thread(target=_background_worker, name="remote-config-download",
                              args=(_background.stop, timeout), daemon=True)
    _background.thread = thread
    thread.start()
    return True


def stop_background_sync(*, wait: float = 2.0) -> None:
    """让后台下载尽快结束；最多等 ``wait`` 秒，不把进程卡在退出上。

    ``_fetch_bytes`` 分块读并检查 stop 事件，所以正常情况远小于 socket 超时就能停；
    真等不到也不影响正确性——暂存是「临时文件 + 原子替换」，不会留下半成品。
    """
    _background.stop.set()
    thread = _background.thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=wait)
        if thread.is_alive():
            logger.info("[在线配置] 后台下载未在 {} 秒内结束，随进程退出", wait)
    _background.thread = None


def background_running() -> bool:
    thread = _background.thread
    return thread is not None and thread.is_alive()


def pending_versions() -> tuple[int, int]:
    """待安装的公开修订与会员包版本；0 表示没有对应候选。"""
    store = _installed_store()
    if store is None:
        return 0, 0
    pending = store.pending()
    member = pending.get("member") or {}
    return int(pending.get("revision") or 0), int(member.get("package_version") or 0)
