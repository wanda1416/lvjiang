"""交流群二维码：临时目录缓存优先，缺失时从站点获取。"""
from __future__ import annotations

from pathlib import Path

from .fs_util import atomic_write_bytes
from .qr_cache import cache_path, cached_bytes, write_cache
from .qr_image import MAX_QRCODE_BYTES, QrCodeError, fetch_image_bytes

# 与 announcement.py 的 notices.json 同域名，走 GitHub Pages 静态站点，
# 不依赖 GitHub API，避免限流。
QRCODE_URL = "https://wanda1416.github.io/lvjiang/feedback-qrcode.jpg"

#: 缓存文件名；位置由 qr_cache 决定（临时目录优先，session 兜底）。
FILENAME = "feedback-qrcode.jpg"
_WRITE_PREFIX = "feedback-qrcode-"


def qrcode_path() -> Path:
    """当前缓存文件的位置：已缓存的那份，未缓存时给首选写入位置。"""
    return cache_path(FILENAME)


def fetch_qrcode_bytes(*, timeout: float = 10.0) -> bytes:
    """从固定 GitHub Pages 地址下载最新二维码图片二进制。"""
    return fetch_image_bytes(
        QRCODE_URL, user_agent="lvjiang-qrcode-refresher", timeout=timeout)


def save_qrcode(data: bytes, *, path: Path | None = None) -> Path | None:
    """原子写入缓存；显式给出 ``path`` 时只写那一处。失败不影响原文件。"""
    if path is not None:
        atomic_write_bytes(path, data, prefix=_WRITE_PREFIX)
        return path
    return write_cache(FILENAME, data, prefix=_WRITE_PREFIX)


def load_qrcode_bytes() -> bytes:
    """缓存缺失时下载并落盘；调用者在后台线程执行。"""
    cached = cached_bytes(FILENAME)
    if cached is not None:
        return cached
    payload = fetch_qrcode_bytes()
    save_qrcode(payload)
    return payload


__all__ = [
    "QRCODE_URL",
    "FILENAME",
    "qrcode_path",
    "load_qrcode_bytes",
    "MAX_QRCODE_BYTES",
    "QrCodeError",
    "fetch_qrcode_bytes",
    "save_qrcode",
]
