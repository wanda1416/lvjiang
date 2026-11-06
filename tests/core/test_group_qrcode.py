"""二维码不依赖预置图片，断网时缓存仍可使用；缓存位置不进 session。"""
from urllib.error import URLError

import pytest

from lvjiang.core import group_qrcode, qr_cache


@pytest.fixture
def caches(tmp_path, monkeypatch):
    """把两个候选缓存位置都指到 tmp_path，避免写真实的系统临时目录。"""
    temp = tmp_path / "temp"
    session = tmp_path / "session"
    monkeypatch.setattr(qr_cache, "temp_cache_directory", lambda: temp)
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", session)
    return temp, session


def test_missing_cache_downloads_once_then_works_offline(caches, monkeypatch):
    temp, session = caches
    calls = []
    def download():
        calls.append(True)
        return b"image"
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", download)
    assert group_qrcode.load_qrcode_bytes() == b"image"
    assert (temp / group_qrcode.FILENAME).read_bytes() == b"image"
    # 二维码是临时素材，默认不进 config/session。
    assert not session.exists()
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", lambda: pytest.fail("已有缓存不应联网"))
    assert group_qrcode.load_qrcode_bytes() == b"image"
    assert len(calls) == 1


def test_unwritable_temp_falls_back_to_session(caches, monkeypatch):
    temp, session = caches
    # 父路径是文件，mkdir 必然失败——模拟只读/受限的临时目录。
    temp.write_bytes(b"blocker")
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", lambda: b"image")
    assert group_qrcode.load_qrcode_bytes() == b"image"
    assert (session / group_qrcode.FILENAME).read_bytes() == b"image"
    monkeypatch.setattr(group_qrcode, "fetch_qrcode_bytes", lambda: pytest.fail("已有缓存不应联网"))
    assert group_qrcode.load_qrcode_bytes() == b"image"


def test_network_failure_identifies_url_and_does_not_create_cache(caches, monkeypatch):
    temp, session = caches
    def unavailable(*_args, **_kwargs):
        raise URLError("offline")
    monkeypatch.setattr("lvjiang.core.qr_image.urlopen", unavailable)
    with pytest.raises(group_qrcode.QrCodeError, match="请检查网络") as failure:
        group_qrcode.load_qrcode_bytes()
    assert group_qrcode.QRCODE_URL in str(failure.value)
    assert not (temp / group_qrcode.FILENAME).exists()
    assert not (session / group_qrcode.FILENAME).exists()
