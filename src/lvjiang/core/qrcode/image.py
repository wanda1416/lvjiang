"""二维码图片的下载与校验。

交流群码与赞助收款码只在地址上不同，网络规则、体积上限和图片格式判定
必须一致；两份复制一份就等于以后改阈值要记得改两处，因此统一放在这里。
"""
from __future__ import annotations

from urllib.error import HTTPError
from urllib.request import Request, urlopen

#: 二维码图片体积很小，超过该阈值视为异常响应（防止误下载到大文件）。
MAX_QRCODE_BYTES = 2 * 1024 * 1024

_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class QrCodeError(RuntimeError):
    """二维码下载或校验失败。"""


def fetch_image_bytes(url: str, *, user_agent: str, timeout: float = 10.0) -> bytes:
    """下载一张二维码图片并校验体积与文件头。

    失败信息里始终带完整 URL：用户看到提示后要么自己复制链接、要么据此
    判断是站点问题还是本机网络问题，只说"网络错误"没法行动。
    """
    req = Request(url)
    req.add_header("Accept", "image/*")
    req.add_header("User-Agent", user_agent)

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
        raise QrCodeError(f"无法访问 {url}，请检查网络（HTTP {exc.code}）") from exc
    except QrCodeError:
        raise
    except Exception as exc:  # noqa: BLE001 - urlopen 的失败类型很多，统一转成可读错误
        raise QrCodeError(f"无法访问 {url}，请检查网络：{exc}") from exc

    if len(payload) > MAX_QRCODE_BYTES:
        raise QrCodeError("二维码文件超过 2MB")
    if not (payload.startswith(_JPEG_MAGIC) or payload.startswith(_PNG_MAGIC)):
        raise QrCodeError("下载内容不是有效的图片文件")
    return payload


__all__ = ["MAX_QRCODE_BYTES", "QrCodeError", "fetch_image_bytes"]
