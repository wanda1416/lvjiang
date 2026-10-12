"""交流群二维码：session 缓存优先，缺失时从站点获取。"""
from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .fs_util import atomic_write_bytes
from .paths import session_directory

# 与 announcement.py 的 notices.json 同域名，走 GitHub Pages 静态站点，
# 不依赖 GitHub API，避免限流。
QRCODE_URL = "https://wanda1416.github.io/lvjiang/feedback-qrcode.jpg"


def qrcode_path() -> Path:
    return session_directory() / "feedback-qrcode.jpg"

# 二维码图片体积很小，超过该阈值视为异常响应（防止误下载到大文件）。
MAX_QRCODE_BYTES = 2 * 1024 * 1024
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class QrCodeError(RuntimeError):
    """二维码下载或校验失败。"""


def fetch_qrcode_bytes(*, timeout: float = 10.0) -> bytes:
    """从固定 GitHub Pages 地址下载最新二维码图片二进制。"""
    req = Request(QRCODE_URL)
    req.add_header("Accept", "image/*")
    req.add_header("User-Agent", "lvjiang-qrcode-refresher")

    try:
        with urlopen(req, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_QRCODE_BYTES:
                        raise QrCodeError("二维码文件超过 2MB")
                except ValueError:
                    pass
            payload = response.read(MAX_QRCODE_BYTES + 1)
    except HTTPError as exc:
        raise QrCodeError(f"无法访问 {QRCODE_URL}，请检查网络（HTTP {exc.code}）") from exc
    except QrCodeError:
        raise
    except Exception as exc:
        raise QrCodeError(f"无法访问 {QRCODE_URL}，请检查网络：{exc}") from exc

    if len(payload) > MAX_QRCODE_BYTES:
        raise QrCodeError("二维码文件超过 2MB")
    if not (payload.startswith(_JPEG_MAGIC) or payload.startswith(_PNG_MAGIC)):
        raise QrCodeError("下载内容不是有效的图片文件")
    return payload


def save_qrcode(data: bytes, *, path: Path | None = None) -> None:
    """原子写入 session 缓存；失败不影响原文件。"""
    atomic_write_bytes(path or qrcode_path(), data, prefix="feedback-qrcode-")


def load_qrcode_bytes() -> bytes:
    """缓存缺失时下载并落盘；调用者在后台线程执行。"""
    path = qrcode_path()
    if path.is_file():
        return path.read_bytes()
    payload = fetch_qrcode_bytes()
    save_qrcode(payload, path=path)
    return payload


__all__ = [
    "QRCODE_URL",
    "qrcode_path",
    "load_qrcode_bytes",
    "MAX_QRCODE_BYTES",
    "QrCodeError",
    "fetch_qrcode_bytes",
    "save_qrcode",
]
