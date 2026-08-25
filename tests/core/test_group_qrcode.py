"""交流群二维码远程刷新：下载校验、写盘与容错测试。"""
from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

import pytest

from lvjiang.core.group_qrcode import (
    MAX_QRCODE_BYTES,
    QRCODE_URL,
    QrCodeError,
    fetch_qrcode_bytes,
    save_qrcode,
)

_JPEG_BYTES = b"\xff\xd8\xff" + b"fake-jpeg-body"


class FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def test_fetch_downloads_from_fixed_github_pages_url(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return FakeResponse(_JPEG_BYTES)

    monkeypatch.setattr("lvjiang.core.group_qrcode.urlopen", fake_urlopen)

    data = fetch_qrcode_bytes(timeout=3.0)

    assert captured == {"url": QRCODE_URL, "timeout": 3.0}
    assert data == _JPEG_BYTES


def test_fetch_rejects_non_image_payload(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.core.group_qrcode.urlopen",
        lambda *args, **kwargs: FakeResponse(b"not-an-image"),
    )

    with pytest.raises(QrCodeError, match="不是有效的图片"):
        fetch_qrcode_bytes()


def test_fetch_rejects_oversized_response(monkeypatch):
    payload = _JPEG_BYTES + b"x" * MAX_QRCODE_BYTES
    monkeypatch.setattr(
        "lvjiang.core.group_qrcode.urlopen",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(QrCodeError, match="2MB"):
        fetch_qrcode_bytes()


def test_fetch_wraps_http_error(monkeypatch):
    def raise_http_error(*args, **kwargs):
        raise HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr("lvjiang.core.group_qrcode.urlopen", raise_http_error)

    with pytest.raises(QrCodeError, match="HTTP 404"):
        fetch_qrcode_bytes()


def test_save_qrcode_writes_file_atomically(tmp_path):
    target = tmp_path / "image" / "feedback-qrcode.jpg"

    save_qrcode(_JPEG_BYTES, path=target)

    assert target.read_bytes() == _JPEG_BYTES
    # 临时文件不应残留
    assert list(target.parent.iterdir()) == [target]


def test_save_qrcode_overwrites_existing_file(tmp_path):
    target = tmp_path / "feedback-qrcode.jpg"
    target.write_bytes(b"old-content")

    save_qrcode(_JPEG_BYTES, path=target)

    assert target.read_bytes() == _JPEG_BYTES
