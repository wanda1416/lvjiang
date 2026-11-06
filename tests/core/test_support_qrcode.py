"""赞助收款码：图源在官方站点，两个渠道各自缓存。"""
import pytest

from lvjiang.core import qr_cache, support_qrcode


def test_urls_point_at_site_static_assets():
    assert support_qrcode.KINDS == ("wechat", "alipay")
    assert (support_qrcode.qrcode_url("wechat")
            == "https://lvjiang.wyxj.net/support/wechat.png")
    assert (support_qrcode.qrcode_url("alipay")
            == "https://lvjiang.wyxj.net/support/alipay.png")
    with pytest.raises(ValueError):
        support_qrcode.qrcode_url("paypal")


def test_channels_cache_separately_and_reuse_without_network(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    monkeypatch.setattr(qr_cache, "temp_cache_directory", lambda: temp)
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path / "session")
    monkeypatch.setattr(support_qrcode, "fetch_qrcode_bytes",
                        lambda kind, **_kwargs: kind.encode())
    assert support_qrcode.load_qrcode_bytes("wechat") == b"wechat"
    assert support_qrcode.load_qrcode_bytes("alipay") == b"alipay"
    assert (temp / "wechat.png").read_bytes() == b"wechat"
    assert (temp / "alipay.png").read_bytes() == b"alipay"
    monkeypatch.setattr(support_qrcode, "fetch_qrcode_bytes",
                        lambda *_args, **_kwargs: pytest.fail("已有缓存不应联网"))
    assert support_qrcode.load_qrcode_bytes("wechat") == b"wechat"


def test_refresh_overwrites_cache_even_when_present(tmp_path, monkeypatch):
    temp = tmp_path / "temp"
    monkeypatch.setattr(qr_cache, "temp_cache_directory", lambda: temp)
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path / "session")
    support_qrcode.save_qrcode("wechat", b"old")
    monkeypatch.setattr(support_qrcode, "fetch_qrcode_bytes",
                        lambda kind, **_kwargs: b"latest")
    assert support_qrcode.refresh("wechat") == b"latest"
    assert (temp / "wechat.png").read_bytes() == b"latest"
