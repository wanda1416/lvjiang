"""赞助对话框：只补缺的那张图，已有缓存时不自动联网。"""
from lvjiang.core import qr_cache, support_qrcode
from lvjiang.ui.notices.qrcode_refresher import ImageRefreshThread
from lvjiang.ui.notices.sponsor_dialog import SponsorDialog


def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(qr_cache, "temp_cache_directory", lambda: tmp_path / "temp")
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path / "session")


def test_downloads_missing_images_only(qapp, tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(ImageRefreshThread, "start",
                        lambda self: started.append(self.key))
    dialog = SponsorDialog()
    assert started == ["wechat", "alipay"]
    dialog._on_error("wechat", "无法访问 URL，请检查网络")
    # 一张都没有时必须给出可行动的出口，而不是停在错误上。
    assert "打开赞助页面" in dialog._status_label.text()
    dialog.close()

    for kind in support_qrcode.KINDS:
        support_qrcode.save_qrcode(kind, b"cached")
    cached = SponsorDialog()
    assert started == ["wechat", "alipay"]
    cached.close()


def test_partial_cache_only_refetches_the_missing_channel(qapp, tmp_path, monkeypatch):
    _isolated(tmp_path, monkeypatch)
    support_qrcode.save_qrcode("wechat", b"cached")
    started = []
    monkeypatch.setattr(ImageRefreshThread, "start",
                        lambda self: started.append(self.key))
    dialog = SponsorDialog()
    assert started == ["alipay"]
    dialog.close()
