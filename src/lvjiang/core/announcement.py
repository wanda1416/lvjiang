"""远程公告获取、校验、版本筛选与 Session 状态管理。

公告通过 GitHub Pages 静态 JSON 下发，不依赖 GitHub API。远程
``notice_version`` 必须单调递增；客户端只在版本推进且存在适用于当前
客户端的公告时自动展示。公告缓存与最后已处理版本存放在
``session.json/server_config.announcement``。
"""
from __future__ import annotations

import json
import ssl
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ..i18n import tr
from .update import get_version, network_access_error_message, parse_version

ANNOUNCEMENT_URL = "https://wanda1416.github.io/lvjiang/notices.json"
ANNOUNCEMENT_SCHEMA_VERSION = 1
MAX_RESPONSE_BYTES = 256 * 1024
MAX_NOTICES = 20
_LEVELS = {"info", "warning", "critical"}


class AnnouncementError(RuntimeError):
    """公告网络或数据错误。"""


class AnnouncementNetworkError(AnnouncementError):
    """可明确归类为本地网络访问失败的公告错误。"""


@dataclass(frozen=True)
class Announcement:
    id: str
    level: str
    title: str
    body: str
    published_at: str = ""
    min_app_version: str = ""
    max_app_version_exclusive: str = ""
    url: str = ""
    active: bool = True


@dataclass(frozen=True)
class AnnouncementManifest:
    schema_version: int
    notice_version: int
    updated_at: str
    notices: tuple[Announcement, ...]


@dataclass(frozen=True)
class AnnouncementFetchResult:
    manifest: AnnouncementManifest
    etag: str = ""
    not_modified: bool = False


def _required_string(data: dict[str, Any], key: str, *, max_length: int) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AnnouncementError(f"公告字段 {key} 必须是非空字符串")
    value = value.strip()
    if len(value) > max_length:
        raise AnnouncementError(f"公告字段 {key} 超过长度限制")
    return value


def _optional_string(data: dict[str, Any], key: str, *, max_length: int) -> str:
    value = data.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AnnouncementError(f"公告字段 {key} 必须是字符串")
    value = value.strip()
    if len(value) > max_length:
        raise AnnouncementError(f"公告字段 {key} 超过长度限制")
    return value


def _valid_https_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def parse_announcement_manifest(data: dict[str, Any]) -> AnnouncementManifest:
    """校验并解析远程公告清单。未知字段忽略，不做旧协议兼容。"""
    if not isinstance(data, dict):
        raise AnnouncementError("公告清单必须是 JSON 对象")

    schema_version = data.get("schema_version")
    if schema_version != ANNOUNCEMENT_SCHEMA_VERSION:
        raise AnnouncementError(f"不支持的公告协议版本: {schema_version!r}")

    notice_version = data.get("notice_version")
    if isinstance(notice_version, bool) or not isinstance(notice_version, int):
        raise AnnouncementError("notice_version 必须是整数")
    if notice_version < 0:
        raise AnnouncementError("notice_version 不能小于 0")

    updated_at = _optional_string(data, "updated_at", max_length=64)
    raw_notices = data.get("notices")
    if not isinstance(raw_notices, list):
        raise AnnouncementError("notices 必须是数组")
    if len(raw_notices) > MAX_NOTICES:
        raise AnnouncementError(f"公告数量不能超过 {MAX_NOTICES}")

    notices: list[Announcement] = []
    seen_ids: set[str] = set()
    for raw in raw_notices:
        if not isinstance(raw, dict):
            raise AnnouncementError("公告条目必须是 JSON 对象")
        notice_id = _required_string(raw, "id", max_length=128)
        if notice_id in seen_ids:
            raise AnnouncementError(f"公告 id 重复: {notice_id}")
        seen_ids.add(notice_id)

        level = _optional_string(raw, "level", max_length=16) or "info"
        if level not in _LEVELS:
            raise AnnouncementError(f"未知公告级别: {level}")
        active = raw.get("active", True)
        if not isinstance(active, bool):
            raise AnnouncementError("公告字段 active 必须是布尔值")

        url = _optional_string(raw, "url", max_length=2048)
        if not _valid_https_url(url):
            raise AnnouncementError("公告 url 必须是 HTTPS 地址")

        notices.append(Announcement(
            id=notice_id,
            level=level,
            title=_required_string(raw, "title", max_length=200),
            body=_required_string(raw, "body", max_length=32_000),
            published_at=_optional_string(raw, "published_at", max_length=64),
            min_app_version=_optional_string(raw, "min_app_version", max_length=64),
            max_app_version_exclusive=_optional_string(
                raw, "max_app_version_exclusive", max_length=64),
            url=url,
            active=active,
        ))

    return AnnouncementManifest(
        schema_version=schema_version,
        notice_version=notice_version,
        updated_at=updated_at,
        notices=tuple(notices),
    )


def manifest_to_dict(manifest: AnnouncementManifest) -> dict[str, Any]:
    """转成可写入 Session/JSON 的普通字典。"""
    return {
        "schema_version": manifest.schema_version,
        "notice_version": manifest.notice_version,
        "updated_at": manifest.updated_at,
        "notices": [asdict(notice) for notice in manifest.notices],
    }


def _version_key(version: str) -> tuple[int, ...]:
    parts = parse_version(version)
    return tuple((parts + [0, 0, 0, 0])[:4])


def notice_applies(notice: Announcement, app_version: str) -> bool:
    """判断公告是否处于启用状态且覆盖当前客户端版本。"""
    if not notice.active:
        return False
    current = _version_key(app_version)
    if notice.min_app_version and current < _version_key(notice.min_app_version):
        return False
    if (notice.max_app_version_exclusive
            and current >= _version_key(notice.max_app_version_exclusive)):
        return False
    return True


def applicable_notices(
    manifest: AnnouncementManifest,
    app_version: str | None = None,
) -> tuple[Announcement, ...]:
    """返回适用于指定（默认当前）客户端版本的公告。"""
    version = app_version or get_version()
    return tuple(n for n in manifest.notices if notice_applies(n, version))


def _announcement_state() -> dict[str, Any]:
    from .config.session import get_session_store
    server = get_session_store().get_node("server_config", {})
    if not isinstance(server, dict):
        return {}
    state = server.get("announcement")
    return state if isinstance(state, dict) else {}


def get_last_notice_version() -> int:
    value = _announcement_state().get("last_notice_version", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def load_cached_manifest() -> AnnouncementManifest | None:
    data = _announcement_state().get("cached_manifest")
    if not isinstance(data, dict):
        return None
    try:
        return parse_announcement_manifest(data)
    except AnnouncementError:
        logger.warning("[公告] Session 中的公告缓存无效，已忽略")
        return None


def get_cached_etag() -> str:
    value = _announcement_state().get("etag", "")
    return value if isinstance(value, str) else ""


def _update_announcement_state(patch: dict[str, Any]) -> None:
    from .config.session import get_session_store

    def update(server: Any) -> dict[str, Any]:
        result = dict(server) if isinstance(server, dict) else {}
        state = result.get("announcement")
        state = dict(state) if isinstance(state, dict) else {}
        applied = patch
        if "last_notice_version" in applied:
            previous = state.get("last_notice_version", 0)
            previous = previous if isinstance(previous, int) and not isinstance(previous, bool) else 0
            patch_version = applied["last_notice_version"]
            applied = {**applied, "last_notice_version": max(previous, patch_version)}
        state.update(applied)
        result["announcement"] = state
        return result

    get_session_store().mutate_node("server_config", update)


def cache_manifest(manifest: AnnouncementManifest, etag: str = "") -> None:
    """缓存最后一次成功获取的公告，但不将其标记为已提示。"""
    _update_announcement_state({
        "cached_manifest": manifest_to_dict(manifest),
        "etag": etag,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    })


def mark_notice_version(version: int) -> None:
    """公告已经展示或确认不适用后，推进客户端已处理版本。"""
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ValueError("公告版本必须是非负整数")
    _update_announcement_state({"last_notice_version": version})


def should_prompt_manifest(
    manifest: AnnouncementManifest,
    app_version: str | None = None,
) -> bool:
    """只有远程公告版本推进且存在适用公告时才自动提示。"""
    return (
        manifest.notice_version > get_last_notice_version()
        and bool(applicable_notices(manifest, app_version))
    )


def fetch_announcement_manifest(
    *,
    etag: str = "",
    cached: AnnouncementManifest | None = None,
    timeout: float = 5.0,
) -> AnnouncementFetchResult:
    """从固定 GitHub Pages 地址获取公告，支持 ETag/304。"""
    req = Request(ANNOUNCEMENT_URL)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "lvjiang-announcement-checker")
    if etag:
        req.add_header("If-None-Match", etag)

    try:
        with urlopen(req, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_RESPONSE_BYTES:
                        raise AnnouncementError("公告文件超过 256 KB")
                except ValueError:
                    pass
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise AnnouncementError("公告文件超过 256 KB")
            response_etag = response.headers.get("ETag", "")
    except HTTPError as exc:
        if exc.code == 304 and cached is not None:
            return AnnouncementFetchResult(cached, etag=etag, not_modified=True)
        raise AnnouncementError(f"公告请求失败: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, ssl.SSLError, ConnectionError) as exc:
        raise AnnouncementNetworkError(
            network_access_error_message(
                ANNOUNCEMENT_URL, "announcement")) from exc
    except AnnouncementError:
        raise
    except Exception as exc:
        raise AnnouncementError(f"公告请求失败: {exc}") from exc

    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnnouncementError("公告文件不是有效的 UTF-8 JSON") from exc

    return AnnouncementFetchResult(
        parse_announcement_manifest(data),
        etag=response_etag,
    )


class AnnouncementChecker(QThread):
    """后台获取 GitHub Pages 公告。"""

    finished = pyqtSignal(object)  # AnnouncementFetchResult
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cached = load_cached_manifest()
        self._etag = get_cached_etag()

    def run(self):
        try:
            self.finished.emit(fetch_announcement_manifest(
                etag=self._etag,
                cached=self._cached,
            ))
        except AnnouncementNetworkError as exc:
            # 常见网络故障只输出一行可操作说明，不泄漏 urllib/SSL 调用栈。
            logger.warning(str(exc))
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 线程边界统一转成信号
            logger.warning(f"[公告] 获取失败: {exc}")
            self.error.emit(tr("获取公告失败: {error}").format(error=exc))
