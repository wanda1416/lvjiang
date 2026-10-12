"""首次打开扫码窗口自动异步获取，已有缓存时不自动联网。"""
from lvjiang.ui.notices.feedback_dialog import GroupQrDialog
from lvjiang.ui.notices.qrcode_refresher import QrCodeRefresher


def test_dialog_starts_download_only_without_cache(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("lvjiang.constants.SESSION_CONFIG_DIR", tmp_path)
    started = []
    monkeypatch.setattr(QrCodeRefresher, "start", lambda self: started.append(self))
    dialog = GroupQrDialog()
    assert len(started) == 1
    dialog._on_refresh_error("无法访问 URL，请检查网络")
    assert dialog._status_label.text() == "无法访问 URL，请检查网络"
    dialog.close()
    (tmp_path / "feedback-qrcode.jpg").write_bytes(b"cached")
    cached = GroupQrDialog()
    assert len(started) == 1
    cached.close()
