"""把图片变成能提交的附件：缩边、压到上限、命名。

为什么压缩放在 UI 层而不是 ``core.feedback``：压缩要 QImage，而 ``core`` 不依赖
Qt（便于测试与复用）。这里只做「图像 → 字节」，提交协议仍由 core 定义。

上限的来历见 ``core.feedback.limits``：单张截图目标 700KB，服务端单值上限
900KB，超了就是 413。所以这里宁可多次降级，也不允许生成一个注定失败的附件。
"""
from __future__ import annotations

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData, Qt
from PyQt6.QtGui import QImage, QPixmap

from ...core.feedback.client import OutgoingAttachment
from ...core.feedback.limits import MAX_SCREENSHOT_BYTES, SCREENSHOT_MAX_EDGE
from ...i18n import tr


class AttachmentError(Exception):
    """图片无法压到上限内；消息可直接展示。"""


def _encode(image: QImage, fmt: str, quality: int = -1) -> bytes:
    """按指定格式编码；编码失败返回空字节（由调用方继续降级）。

    ``QBuffer(QByteArray())`` 只保存指针，临时对象立刻被回收就是野指针
    （PyQt6 下表现为 save 时直接访问冲突崩溃）。存储必须活到 close 之后。
    """
    storage = QByteArray()
    buffer = QBuffer(storage)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return b""
    try:
        image.save(buffer, fmt, quality)
        # QByteArray.data() 在 PyQt6 里直接给 bytes（再包一层 bytes() 反而过不了类型检查）
        return buffer.data().data()
    finally:
        buffer.close()


def _scaled(image: QImage, edge: int) -> QImage:
    """按最长边等比缩放；本来就够小就原样返回。"""
    longest = max(image.width(), image.height())
    if longest <= edge:
        return image
    return image.scaled(edge, edge, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)


def encode_screenshot(image: QImage) -> tuple[bytes, str]:
    """把一张截图压成 ``(字节, mime)``，保证字节数不超过单张上限。

    降级顺序：PNG（截图文字锐利，优先）→ JPEG q85（照片类）→ 缩边重试。
    四轮之后仍超限才报错——那种图通常是一整屏 4K 壁纸，不适合当反馈附件，
    让用户自己裁剪比悄悄传一张 3MB 的图更好。

    Raises:
        AttachmentError: 压不到上限内。
    """
    if image.isNull():
        raise AttachmentError(tr("图片无效，无法作为附件"))
    edge = SCREENSHOT_MAX_EDGE
    for _ in range(4):
        current = _scaled(_flatten(image), edge)
        png = _encode(current, "PNG")
        if png and len(png) <= MAX_SCREENSHOT_BYTES:
            return png, "image/png"
        jpeg = _encode(current, "JPEG", 85)
        if jpeg and len(jpeg) <= MAX_SCREENSHOT_BYTES:
            return jpeg, "image/jpeg"
        edge = max(480, int(edge * 0.75))
    raise AttachmentError(tr("这张图太大，压到 {limit} KB 以内仍失败，请裁剪后重试")
                          .format(limit=MAX_SCREENSHOT_BYTES // 1024))


def _flatten(image: QImage) -> QImage:
    """带透明通道的图转成不透明 RGB：JPEG 不支持 alpha，直接存会把透明处变黑。"""
    if not image.hasAlphaChannel():
        return image
    flattened = image.convertToFormat(QImage.Format.Format_RGB32)
    return flattened


def attachment_from_image(image: QImage, *, index: int) -> OutgoingAttachment:
    """压缩并包装成待提交附件；``index`` 只用于默认文件名。"""
    data, mime = encode_screenshot(image)
    suffix = "jpg" if mime == "image/jpeg" else "png"
    return OutgoingAttachment(name=f"screenshot-{index}.{suffix}",
                              kind="screenshot", mime=mime, data=data)


def image_from_mime(source: QMimeData) -> QImage | None:
    """从剪贴板/拖放数据里取出图片；没有图片返回 ``None``。

    Qt 在不同平台返回 ``QImage`` 或 ``QPixmap``，两种都要认。
    """
    if source is None or not source.hasImage():
        return None
    data = source.imageData()
    if isinstance(data, QImage):
        return data
    if isinstance(data, QPixmap):
        return data.toImage()
    return None


__all__ = [
    "AttachmentError",
    "attachment_from_image",
    "encode_screenshot",
    "image_from_mime",
]
