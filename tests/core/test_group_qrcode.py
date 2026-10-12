"""二维码不依赖预置图片，断网时缓存仍可使用。"""
from urllib.error import URLError

import pytest

from lvjiang.core import group_qrcode


def test_missing_cache_downloads_once_then_works_offline(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path)
    calls = []
    def download():
        calls.append(True)
        return b"image"
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", download)
    assert group_qrcode.load_qrcode_bytes() == b"image"
    assert (tmp_path / "feedback-qrcode.jpg").read_bytes() == b"image"
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", lambda: pytest.fail("已有缓存不应联网"))
    assert group_qrcode.load_qrcode_bytes() == b"image"
    assert len(calls) == 1


def test_network_failure_identifies_url_and_does_not_create_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path)
    def unavailable(*_args, **_kwargs):
        raise URLError("offline")
    monkeypatch.setattr(group_qrcode, "urlopen", unavailable)
    with pytest.raises(group_qrcode.QrCodeError, match="请检查网络") as failure:
        group_qrcode.load_qrcode_bytes()
    assert group_qrcode.QRCODE_URL in str(failure.value)
    assert not group_qrcode.qrcode_path().exists()
