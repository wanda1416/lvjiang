"""0.15 公开更新下载：只返回完整候选，不在网络线程修改生效目录。"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..update_bundle import (
    MAX_ARCHIVE_BYTES,
    PUBLIC_INDEX_URL,
    digest,
    parse_index,
    parse_manifest,
    series,
    verify_archive,
)


@dataclass(frozen=True)
class PublicDownload:
    manifest_json: bytes
    archive: bytes
    index_json: bytes = b""

    @property
    def manifest(self) -> dict[str, Any] | None:
        return parse_manifest(json.loads(self.manifest_json)) if self.manifest_json else None


def fetch_public_update(
    app_version: str, *, fetch: Callable[..., tuple[bytes, str, bool]],
    timeout: float = 10, index_url: str = PUBLIC_INDEX_URL,
    cache_index: Callable[[bytes], None] | None = None,
) -> PublicDownload:
    """fetch 由宿主提供受限 HTTPS 实现；无对应系列不请求任何历史包。"""
    version = series(app_version)
    raw, _, _ = fetch(index_url, max_bytes=256 * 1024, timeout=timeout)
    index = parse_index(json.loads(raw))
    if cache_index is not None:
        cache_index(raw)
    entry = index["series"].get(version)
    if entry is None:
        return PublicDownload(b"", b"", raw)
    base_url = index_url.rsplit("/", 1)[0]
    manifest_url = f"{base_url}/{entry['manifest']}"
    raw_manifest, _, _ = fetch(manifest_url + f"?sha256={entry['sha256']}", max_bytes=2 * 1024 * 1024, timeout=timeout)
    if digest(raw_manifest) != entry["sha256"]:
        raise ValueError("索引与修订清单摘要不匹配")
    manifest = parse_manifest(json.loads(raw_manifest))
    if manifest["app_series"] != version or manifest["revision"] != entry["revision"]:
        raise ValueError("索引与修订清单版本不一致")
    archive, _, _ = fetch(manifest_url.rsplit("/", 1)[0] + f"/update.zip?sha256={manifest['archive_sha256']}",
                          max_bytes=MAX_ARCHIVE_BYTES, timeout=timeout)
    verify_archive(manifest, archive)
    return PublicDownload(raw_manifest, archive, raw)
