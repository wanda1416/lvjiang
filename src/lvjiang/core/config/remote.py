"""在线配置下发 —— 拉 manifest、校验、落 ``config/remote/``。

## 形态

和公告（`core/announcement.py`）、群二维码（`core/group_qrcode.py`）同款：
GitHub Pages 静态文件，不依赖 GitHub API、不需要服务端。manifest 描述有
哪些文件、各自的 URL / sha256 / content_version / 适用客户端版本区间：

    https://wanda1416.github.io/lvjiang/config/config.json

本模块只负责**把文件正确地拿下来**；拿下来之后谁生效是
`resolver.ConfigResolver.resolve_read` 的事（remote 只在 content_version
严格新于 system 时才顶替 system，见 `core.config.versioning`）。两件事
分开，是因为"三层合并的正确性"和"网络传输"是两个独立问题。

## 四道闸门

1. **协议版本**：`schema_version` 对不上直接整份拒绝，不做旧协议兼容
2. **客户端版本区间**：每个条目可声明 `min_app_version` /
   `max_app_version_exclusive`。这是"远端配置和本地代码对不上"的唯一防线
   ——布局里的 region key 是被 `.wf` 脚本按名字引用的（`[场景].[区域]`），
   代码改了 key 而远端还在下发老配置，脚本会直接崩。改了就抬门槛。
3. **sha256**：逐文件校验，半截文件/被篡改的文件不落盘
4. **路径合法性**：只接受 versioning 注册表里声明过的相对路径，且拒绝
   任何 `..` / 绝对路径 —— manifest 是远端内容，不能让它决定往哪写盘

## 撤回

manifest 是**全量声明**：本地 `config/remote/` 里存在、但本轮 manifest 没
列（或版本区间不适用）的文件一律删除。作者推错了配置，从 manifest 里移除
该条目即可撤回——不用发明 `revoked: true` 之类的标志位，语义只有一个。

注意撤回**不能靠把 content_version 改小**：闸门要求远端严格大于 system，
版本号调小只会让它不生效，但文件还留在本地 remote 目录里；只有从
manifest 移除才会真正删掉。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from loguru import logger

from . import versioning

REMOTE_CONFIG_URL = "https://wanda1416.github.io/lvjiang/config/config.json"
REMOTE_CONFIG_SCHEMA_VERSION = 1
#: manifest 本身的大小上限
MAX_MANIFEST_BYTES = 256 * 1024
#: 单个配置文件的大小上限（最大的出厂布局 JSON 也就几十 KB）
MAX_FILE_BYTES = 1024 * 1024
MAX_ENTRIES = 500
_USER_AGENT = "lvjiang-remote-config"


class RemoteConfigError(RuntimeError):
    """在线配置的网络或数据错误。"""


@dataclass(frozen=True)
class RemoteEntry:
    """manifest 里的一个文件条目。"""

    rel_path: str
    url: str
    sha256: str
    content_version: int
    min_app_version: str = ""
    max_app_version_exclusive: str = ""


@dataclass(frozen=True)
class RemoteManifest:
    schema_version: int
    config_version: int
    updated_at: str
    entries: tuple[RemoteEntry, ...]


@dataclass(frozen=True)
class SyncJob:
    """主线程备好、传给 worker 的入参（见 :func:`build_sync_job`）。"""

    etag: str = ""
    app_version: str = ""
    enabled: bool = True
    #: 本地已同步到的清单版本，用于拒绝倒退的过期清单（见 run_sync）
    config_version: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.enabled


@dataclass(frozen=True)
class SyncResult:
    """一轮同步的结果，用于日志与设置页展示。

    同时是 worker → 主线程的状态迁移载体：``etag`` / ``config_version``
    要由**主线程**写回 SessionStore，见 :func:`apply_outcome`。
    """

    config_version: int = 0
    updated: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    not_modified: bool = False
    etag: str = ""
    #: 是否真的跑了一轮（用户关掉在线配置时为 False）。
    #: apply_outcome 据此完全跳过状态写入——否则会把已有的 etag /
    #: config_version 清零覆盖掉，用户重新打开后白拉一整轮。
    performed: bool = True

    @property
    def changed(self) -> bool:
        return bool(self.updated or self.removed)


# ─── manifest 解析与校验 ──────────────────────────────────

def _required_string(data: dict[str, Any], key: str, *, max_length: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RemoteConfigError(f"配置清单字段 {key} 必须是非空字符串")
    value = value.strip()
    if len(value) > max_length:
        raise RemoteConfigError(f"配置清单字段 {key} 超过长度限制")
    return value


def _optional_string(data: dict[str, Any], key: str, *, max_length: int) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RemoteConfigError(f"配置清单字段 {key} 必须是字符串")
    value = value.strip()
    if len(value) > max_length:
        raise RemoteConfigError(f"配置清单字段 {key} 超过长度限制")
    return value


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RemoteConfigError(f"配置清单字段 {key} 必须是整数")
    if value < 0:
        raise RemoteConfigError(f"配置清单字段 {key} 不能小于 0")
    return value


def is_safe_rel_path(rel_path: str) -> bool:
    """rel_path 是否可以安全地拼进本地 remote 目录。

    manifest 是远端内容，绝不能让它决定往哪写盘：拒绝绝对路径、盘符、
    ``..`` 回溯与反斜杠（Windows 上 ``a\\..\\..\\x`` 同样能逃逸）。
    再要求它落在 versioning 注册表声明过的目录里——没声明的目录本来就
    不参与在线下发，远端往那儿放文件没有任何正当理由。
    """
    if not rel_path or rel_path.startswith("/") or "\\" in rel_path:
        return False
    if ".." in rel_path.split("/"):
        return False
    if Path(rel_path).is_absolute() or ":" in rel_path:
        return False
    return versioning.spec_for(rel_path) is not None


def _valid_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def parse_manifest(data: Any) -> RemoteManifest:
    """校验并解析远端配置清单。未知字段忽略，不做旧协议兼容。"""
    if not isinstance(data, dict):
        raise RemoteConfigError("配置清单必须是 JSON 对象")

    schema_version = data.get("schema_version")
    if schema_version != REMOTE_CONFIG_SCHEMA_VERSION:
        raise RemoteConfigError(f"不支持的配置清单协议版本: {schema_version!r}")

    config_version = _positive_int(data, "config_version")
    updated_at = _optional_string(data, "updated_at", max_length=64)

    raw_files = data.get("files")
    if not isinstance(raw_files, list):
        raise RemoteConfigError("files 必须是数组")
    if len(raw_files) > MAX_ENTRIES:
        raise RemoteConfigError(f"配置文件数量不能超过 {MAX_ENTRIES}")

    entries: list[RemoteEntry] = []
    seen: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise RemoteConfigError("配置清单条目必须是 JSON 对象")
        rel_path = _required_string(raw, "rel_path", max_length=512)
        if rel_path in seen:
            raise RemoteConfigError(f"配置清单 rel_path 重复: {rel_path}")
        seen.add(rel_path)

        url = _required_string(raw, "url", max_length=2048)
        if not _valid_https_url(url):
            raise RemoteConfigError(f"配置文件 url 必须是 HTTPS 地址: {rel_path}")

        sha256 = _required_string(raw, "sha256", max_length=64).lower()
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise RemoteConfigError(f"配置文件 sha256 非法: {rel_path}")

        entries.append(RemoteEntry(
            rel_path=rel_path,
            url=url,
            sha256=sha256,
            content_version=_positive_int(raw, "content_version"),
            min_app_version=_optional_string(raw, "min_app_version", max_length=64),
            max_app_version_exclusive=_optional_string(
                raw, "max_app_version_exclusive", max_length=64),
        ))

    return RemoteManifest(
        schema_version=schema_version,
        config_version=config_version,
        updated_at=updated_at,
        entries=tuple(entries),
    )


def _version_key(version: str) -> tuple[int, ...]:
    from ..update import parse_version
    return tuple((parse_version(version) + [0, 0, 0, 0])[:4])


def entry_applies(entry: RemoteEntry, app_version: str) -> bool:
    """条目是否适用于当前客户端版本。

    这是"远端配置和本地代码对不上"的唯一防线：布局里的 region key 被
    `.wf` 脚本按名字引用，代码改了 key 而远端还在下发老配置，脚本会崩。
    """
    current = _version_key(app_version)
    if entry.min_app_version and current < _version_key(entry.min_app_version):
        return False
    if (entry.max_app_version_exclusive
            and current >= _version_key(entry.max_app_version_exclusive)):
        return False
    return True


def applicable_entries(manifest: RemoteManifest,
                       app_version: str | None = None) -> tuple[RemoteEntry, ...]:
    """筛掉不适用当前客户端、以及路径不合法的条目。"""
    from ..update import get_version
    version = app_version or get_version()
    result: list[RemoteEntry] = []
    for entry in manifest.entries:
        if not is_safe_rel_path(entry.rel_path):
            logger.warning(f"[在线配置] 路径不合法或未参与下发，已忽略: {entry.rel_path}")
            continue
        if not entry_applies(entry, version):
            logger.info(
                f"[在线配置] 不适用于当前版本 {version}，跳过: {entry.rel_path}")
            continue
        result.append(entry)
    return tuple(result)


# ─── 网络 ────────────────────────────────────────────────

def _fetch_bytes(url: str, *, max_bytes: int, timeout: float,
                 etag: str = "") -> tuple[bytes, str, bool]:
    """取一个 URL；返回 (内容, ETag, 是否 304)。"""
    req = Request(url)
    req.add_header("User-Agent", _USER_AGENT)
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with urlopen(req, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length:
                try:
                    if int(length) > max_bytes:
                        raise RemoteConfigError(f"响应体超过 {max_bytes} 字节: {url}")
                except ValueError:
                    pass
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise RemoteConfigError(f"响应体超过 {max_bytes} 字节: {url}")
            return payload, response.headers.get("ETag", ""), False
    except HTTPError as exc:
        if exc.code == 304:
            return b"", etag, True
        raise RemoteConfigError(f"请求失败 HTTP {exc.code}: {url}") from exc
    except RemoteConfigError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RemoteConfigError(f"请求失败: {url} ({exc})") from exc


def fetch_manifest(*, etag: str = "", timeout: float = 5.0,
                   url: str = REMOTE_CONFIG_URL) -> tuple[RemoteManifest | None, str]:
    """拉取并解析 manifest；304 时返回 (None, etag)。"""
    payload, response_etag, not_modified = _fetch_bytes(
        url, max_bytes=MAX_MANIFEST_BYTES, timeout=timeout, etag=etag)
    if not_modified:
        return None, etag
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteConfigError("配置清单不是有效的 UTF-8 JSON") from exc
    return parse_manifest(data), response_etag


def _download_entry(entry: RemoteEntry, timeout: float) -> bytes:
    payload, _, _ = _fetch_bytes(
        entry.url, max_bytes=MAX_FILE_BYTES, timeout=timeout)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != entry.sha256:
        raise RemoteConfigError(
            f"sha256 不匹配（期望 {entry.sha256[:12]}…，实得 {digest[:12]}…）: "
            f"{entry.rel_path}")
    text = payload.decode("utf-8")
    actual = versioning.version_from_text(text, Path(entry.rel_path).suffix)
    if actual != entry.content_version:
        # manifest 说的版本号和文件里写的对不上，说明作者发布时漏了一步。
        # 以文件里的为准会让 manifest 失去意义，直接拒绝更干脆。
        raise RemoteConfigError(
            f"content_version 与清单不符（清单 {entry.content_version}，"
            f"文件 {actual}）: {entry.rel_path}")
    return payload


# ─── 落盘同步 ────────────────────────────────────────────

def _local_files(remote_dir: Path) -> set[str]:
    """本地 remote 目录里已有的文件（相对路径，正斜杠）。"""
    if not remote_dir.is_dir():
        return set()
    return {p.relative_to(remote_dir).as_posix()
            for p in remote_dir.rglob("*") if p.is_file()}


def _prune_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def sync_to_dir(manifest: RemoteManifest, remote_dir: Path, *,
                app_version: str | None = None, timeout: float = 10.0) -> SyncResult:
    """把 manifest 声明的文件同步到 remote_dir，并删掉它没声明的。

    单个文件失败（网络/校验）不中断整轮——其余文件照常更新，失败的保持
    本地原样并记 warning。一份配置拉不下来不该让另外几十份也停在旧版本。
    """
    entries = applicable_entries(manifest, app_version)
    wanted = {e.rel_path for e in entries}

    updated: list[str] = []
    skipped: list[str] = []
    for entry in entries:
        target = remote_dir / entry.rel_path
        local_version = versioning.read_version(target)
        if local_version is not None and local_version >= entry.content_version:
            # 已是该版本或**更新**，不下载。
            #
            # 必须是 >= 而不是 ==：清单声称的版本比本地已有的旧，说明拿到的
            # 是一份过期清单（CDN 缓存未失效、回滚了发布目录、甚至是重放），
            # 照做就是把本地配置降级回旧版——正是 content_version 这套机制要
            # 防的事故。撤回配置的正道是从 manifest 里移除条目（见模块文档），
            # 不是把版本号调小。
            continue
        try:
            payload = _download_entry(entry, timeout)
        except RemoteConfigError as exc:
            logger.warning(f"[在线配置] 跳过 {entry.rel_path}: {exc}")
            skipped.append(entry.rel_path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再替换：中途崩溃不会留下半截配置被 resolver 读到
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(payload)
        tmp.replace(target)
        updated.append(entry.rel_path)

    removed = sorted(_local_files(remote_dir) - wanted)
    for rel_path in removed:
        (remote_dir / rel_path).unlink(missing_ok=True)
    if removed:
        _prune_empty_dirs(remote_dir)

    return SyncResult(
        config_version=manifest.config_version,
        updated=tuple(updated),
        removed=tuple(removed),
        skipped=tuple(skipped),
    )


# ─── Session 状态（server_config.remote_config）────────────

def _state() -> dict[str, Any]:
    from .session import get_session_store
    server = get_session_store().get_node("server_config", {})
    if not isinstance(server, dict):
        return {}
    state = server.get("remote_config")
    return state if isinstance(state, dict) else {}


def _update_state(patch: dict[str, Any]) -> None:
    from .session import get_session_store

    def update(server: Any) -> dict[str, Any]:
        result = dict(server) if isinstance(server, dict) else {}
        state = result.get("remote_config")
        state = dict(state) if isinstance(state, dict) else {}
        state.update(patch)
        result["remote_config"] = state
        return result

    get_session_store().mutate_node("server_config", update)


def is_enabled() -> bool:
    """用户是否允许在线配置更新。

    走 ``settings.network`` 那套统一的联网偏好（与公告/更新同一层），
    **不另立一个开关**——否则"完全离线模式"这个总闸管不到在线配置，
    用户勾了离线却还在后台拉配置，是必然的投诉。

    默认开启，与统计的"默认关 + 首启询问"相反：统计是把数据传出去，
    在线配置是把修复拿回来。见 `core.config.models.NetworkConfig`。
    """
    from . import load_user_config
    network = load_user_config().network
    return bool(network.remote_config) and not network.offline


def get_cached_etag() -> str:
    value = _state().get("etag", "")
    return value if isinstance(value, str) else ""


def get_config_version() -> int:
    value = _state().get("config_version", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def get_last_synced_at() -> str:
    value = _state().get("last_synced_at", "")
    return value if isinstance(value, str) else ""


# ─── 暂存层与提升（"下次启动才生效"的落实）──────────────

def stage_dir() -> Path:
    """本次会话下载内容的落点。

    内容先落暂存层、**下次启动**才提升为生效层，见 :func:`promote_pending`。
    直接写生效层做不到"本次会话不热切换"：工作流每次启动都会重新
    ``load_layout()``（``ui/main/run_control.py``），会立刻读到新下发的布局，
    而场景注册表只在进程启动时加载一次——两者一新一旧配在一起，出的问题
    极难查。
    """
    from .resolver import REMOTE_STAGE_DIR
    return REMOTE_STAGE_DIR


def _copytree(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        if not path.is_file():
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def prepare_stage() -> Path:
    """把生效层的当前内容复制进暂存层，作为本轮同步的基线。

    不复制的话，暂存层里只有本轮变更的文件，提升上去等于把没变的那些
    全删了；而 ``sync_to_dir`` 的"清单没列的一律删"也需要看到完整现状
    才能正确求差集。这些都是几十 KB 的小文本文件，整份复制的开销可以忽略。
    """
    from .resolver import REMOTE_CONFIG_DIR
    stage = stage_dir()
    if stage.exists():
        return stage          # 上一轮下载完还没提升，接着用
    stage.mkdir(parents=True, exist_ok=True)
    if REMOTE_CONFIG_DIR.is_dir():
        _copytree(REMOTE_CONFIG_DIR, stage)
    return stage


def promote_pending() -> bool:
    """**启动早期**调用：把暂存层提升为生效层。返回是否发生了提升。

    必须在任何配置读取之前调用（场景注册表、布局都在启动时加载），否则
    这一次启动仍会用旧配置，要等再下一次。

    先把现役目录挪开、再改名暂存目录、最后删挪开的那份：中途崩溃最坏是
    留下一个 ``.old`` 残留（下次启动清掉），不会出现"生效层没了"的窗口。

    用户已关闭在线配置时，两层一并清掉：开关的语义必须是"回到出厂配置"，
    只停止下载是不够的——已下发的配置会一直顶替出厂内容，用户遇到一份有
    问题的远端配置时就没有退路了。重新打开会重新同步，删掉没有损失。
    """
    import shutil

    from .resolver import REMOTE_CONFIG_DIR
    stage = stage_dir()
    retired = REMOTE_CONFIG_DIR.with_name(REMOTE_CONFIG_DIR.name + ".old")
    shutil.rmtree(retired, ignore_errors=True)   # 清掉上次可能的残留

    if not is_enabled():
        if stage.exists() or REMOTE_CONFIG_DIR.exists():
            shutil.rmtree(stage, ignore_errors=True)
            shutil.rmtree(REMOTE_CONFIG_DIR, ignore_errors=True)
            logger.info("[在线配置] 已关闭，已清除下发的配置，回到出厂内容")
        return False

    if not stage.is_dir():
        return False
    if REMOTE_CONFIG_DIR.exists():
        REMOTE_CONFIG_DIR.rename(retired)
    stage.rename(REMOTE_CONFIG_DIR)
    shutil.rmtree(retired, ignore_errors=True)
    logger.info("[在线配置] 已应用上次下载的配置")
    return True


def build_sync_job() -> SyncJob:
    """**主线程**调用：读 Session 状态与客户端版本，备好 worker 的入参。

    读 SessionStore 与 get_version() 都放主线程，理由同
    `core/telemetry/reporter.py` 的约束 1——不该在 worker 线程里首次触发
    这些模块的懒加载。
    """
    from ..update import get_version
    if not is_enabled():
        return SyncJob(enabled=False)
    return SyncJob(etag=get_cached_etag(), app_version=get_version(),
                   enabled=True, config_version=get_config_version())


def run_sync(job: SyncJob, *, remote_dir: Path | None = None,
             timeout: float = 10.0) -> SyncResult:
    """**worker 线程**调用：拉 manifest → 校验 → 落盘。

    只做网络与磁盘，**不碰 SessionStore**——``main_window`` 给 SessionStore
    注册了 UI 回调，写锁超时会从调用线程弹 QMessageBox，非主线程弹原生模态
    框是未定义行为（同 `core/telemetry/reporter.py` 的约束 2）。状态迁移由
    主线程在 finished 槽里经 :func:`apply_outcome` 完成。
    """
    if job.is_empty:
        logger.info("[在线配置] 用户已关闭，跳过同步")
        return SyncResult(performed=False)

    target_dir = remote_dir if remote_dir is not None else prepare_stage()

    manifest, etag = fetch_manifest(etag=job.etag, timeout=timeout)
    if manifest is None:
        logger.info("[在线配置] 清单未变化（304）")
        return SyncResult(not_modified=True, etag=job.etag)

    if manifest.config_version < job.config_version:
        # 清单版本比本地已同步过的旧 —— 拿到的是过期清单（CDN 缓存未失效、
        # 发布目录被回滚、重放）。发布约定是 config_version 单调递增，倒退
        # 一律不采信，否则整批配置会被静默回退到旧版。
        logger.warning(
            f"[在线配置] 清单版本回退（本地 v{job.config_version} → "
            f"远端 v{manifest.config_version}），已忽略本轮")
        return SyncResult(config_version=job.config_version, performed=False)

    result = sync_to_dir(manifest, target_dir,
                         app_version=job.app_version or None, timeout=timeout)
    if result.changed:
        logger.info(
            f"[在线配置] 同步完成 v{manifest.config_version}："
            f"更新 {len(result.updated)}、移除 {len(result.removed)}"
            + (f"、跳过 {len(result.skipped)}" if result.skipped else ""))
    return SyncResult(
        config_version=manifest.config_version,
        updated=result.updated,
        removed=result.removed,
        skipped=result.skipped,
        # 有文件没拿到就**不记 etag**：记了的话下次带 If-None-Match 会收到
        # 304，整轮直接跳过，这些文件就永远停在旧版/缺失，直到作者恰好又发
        # 一版新清单为止。宁可下次多拉一次清单（几 KB），也不能让一次网络
        # 抖动把某个文件永久卡住。
        etag="" if result.skipped else etag,
    )


def apply_outcome(result: SyncResult) -> None:
    """**主线程**调用：把 worker 的结果写回 Session（见 run_sync 的说明）。

    没真跑（用户关掉了在线配置）就一个字段都不写——否则会把已有的
    etag / config_version 清零，用户重新打开后要白拉一整轮。
    304 时只更新时间戳，etag 与版本号本来就没变。
    """
    if not result.performed:
        return
    patch: dict[str, Any] = {
        "last_synced_at": datetime.now(timezone.utc).isoformat()}
    if not result.not_modified:
        patch["etag"] = result.etag
        patch["config_version"] = result.config_version
    _update_state(patch)


def sync_now(*, remote_dir: Path | None = None,
             timeout: float = 10.0) -> SyncResult:
    """单线程一把梭：build → run → apply。

    供 CLI / 设置页的"立即检查"这类**已在主线程**的同步场景用。启动期
    走后台线程的路径请用 :class:`~lvjiang.core.remote_config_sync.RemoteConfigSyncer`，
    它按三段式拆开，不会在 worker 里写 SessionStore。
    """
    result = run_sync(build_sync_job(), remote_dir=remote_dir, timeout=timeout)
    apply_outcome(result)
    return result
