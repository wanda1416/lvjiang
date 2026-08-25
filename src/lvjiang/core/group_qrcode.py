"""交流群二维码远程刷新。

二维码图片是随安装包分发的磁盘文件（``data/image/feedback-qrcode.jpg``），
过期后无法自行更新。本模块提供"从 GitHub Pages 拉取最新二维码并覆盖本地
文件"的能力：用户在 ``GroupQrDialog`` 里点击"刷新二维码"时触发，下载与
写盘都在后台线程完成；失败时保留本地原文件不清空，与
``core/announcement.py`` 的刷新范式保持一致。
"""
from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ..constants import DATA_DIR
from .fs_util import atomic_write_bytes

# 与 announcement.py 的 notices.json 同域名，走 GitHub Pages 静态站点，
# 不依赖 GitHub API，避免限流。
QRCODE_URL = "https://wanda1416.github.io/lvjiang/feedback-qrcode.jpg"
QRCODE_PATH = DATA_DIR / "image" / "feedback-qrcode.jpg"

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
        raise QrCodeError(f"二维码请求失败: HTTP {exc.code}") from exc
    except QrCodeError:
        raise
    except Exception as exc:
        raise QrCodeError(f"二维码请求失败: {exc}") from exc

    if len(payload) > MAX_QRCODE_BYTES:
        raise QrCodeError("二维码文件超过 2MB")
    if not (payload.startswith(_JPEG_MAGIC) or payload.startswith(_PNG_MAGIC)):
        raise QrCodeError("下载内容不是有效的图片文件")
    return payload


def save_qrcode(data: bytes, *, path: Path | None = None) -> None:
    """原子写入本地二维码文件；写入过程失败不会影响原文件。

    ``path`` 默认惰性取模块级 ``QRCODE_PATH``（而非在函数签名里早绑定），
    这样测试或未来的路径改造只需 monkeypatch 该模块属性即可生效。
    """
    atomic_write_bytes(path or QRCODE_PATH, data, prefix="feedback-qrcode-")


class QrCodeRefresher(QThread):
    """后台下载并保存最新交流群二维码。"""

    finished = pyqtSignal(bytes)  # 下载并写盘成功后的图片二进制
    error = pyqtSignal(str)

    def run(self):
        try:
            data = fetch_qrcode_bytes()
            save_qrcode(data)
        except QrCodeError as exc:
            logger.warning(f"[二维码] 刷新失败: {exc}")
            self.error.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - 线程边界统一转成信号
            logger.exception("[二维码] 刷新失败")
            self.error.emit(f"刷新二维码失败: {exc}")
        else:
            self.finished.emit(data)


__all__ = [
    "QRCODE_URL",
    "QRCODE_PATH",
    "MAX_QRCODE_BYTES",
    "QrCodeError",
    "QrCodeRefresher",
    "fetch_qrcode_bytes",
    "save_qrcode",
]
