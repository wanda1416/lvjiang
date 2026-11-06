"""赞助收款码：渠道、图源与缓存。

图源只有一处——官方站点的静态资源（``/support/<渠道>.png``）。仓库里的
README 副本与站点是同一张图的两个交付副本，客户端不内置第三份：打开对话
框时按需取图并缓存，换码不需要发版。

收款码是"自愿赞助"的入口，不换取功能、授权或服务；这里的模块只负责把图
取回来，任何措辞与流程都不该从代码里暗示对价关系。
"""
from __future__ import annotations

from pathlib import Path
from typing import Final

from .public_links import SUPPORT_QRCODE_BASE_URL
from .qr_cache import cache_path, cached_bytes, write_cache
from .qr_image import fetch_image_bytes

#: 渠道顺序即界面展示顺序。
KINDS: Final[tuple[str, ...]] = ("wechat", "alipay")

_FILENAMES: Final[dict[str, str]] = {"wechat": "wechat.png", "alipay": "alipay.png"}


def _filename(kind: str) -> str:
    try:
        return _FILENAMES[kind]
    except KeyError:
        raise ValueError(f"未知的赞助渠道：{kind}") from None


def qrcode_url(kind: str) -> str:
    """该渠道收款码的下载地址（站点静态资源）。"""
    return f"{SUPPORT_QRCODE_BASE_URL}/{_filename(kind)}"


def qrcode_path(kind: str) -> Path:
    """当前缓存文件的位置：已缓存的那份，未缓存时给首选写入位置。"""
    return cache_path(_filename(kind))


def cached_qrcode(kind: str) -> bytes | None:
    """已缓存的图片字节；没有缓存返回 None（调用方据此决定是否联网）。"""
    return cached_bytes(_filename(kind))


def fetch_qrcode_bytes(kind: str, *, timeout: float = 10.0) -> bytes:
    """下载该渠道的收款码图片。"""
    return fetch_image_bytes(
        qrcode_url(kind), user_agent="lvjiang-support-qrcode", timeout=timeout)


def save_qrcode(kind: str, data: bytes) -> Path | None:
    """写入缓存；临时目录与 session 都不可写时返回 None。"""
    filename = _filename(kind)
    return write_cache(filename, data, prefix=f"support-{filename}-")


def load_qrcode_bytes(kind: str) -> bytes:
    """缓存缺失时下载并落盘；调用者在后台线程执行。"""
    cached = cached_qrcode(kind)
    if cached is not None:
        return cached
    payload = fetch_qrcode_bytes(kind)
    save_qrcode(kind, payload)
    return payload


def refresh(kind: str) -> bytes:
    """无条件重新下载并覆盖缓存，返回最新图片字节。"""
    payload = fetch_qrcode_bytes(kind)
    save_qrcode(kind, payload)
    return payload


__all__ = [
    "KINDS",
    "cached_qrcode",
    "fetch_qrcode_bytes",
    "load_qrcode_bytes",
    "qrcode_path",
    "qrcode_url",
    "refresh",
    "save_qrcode",
]
