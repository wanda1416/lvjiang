"""反馈规范对话框与交流群二维码子对话框测试。"""
from io import BytesIO
from unittest.mock import patch

from lvjiang.core import group_qrcode
from lvjiang.i18n import init_i18n
from lvjiang.ui.notices.feedback_dialog import (
    ISSUE_GUIDE_URL,
    NEW_ISSUE_URL,
    FeedbackDialog,
    GroupQrDialog,
)

_JPEG_BYTES = b"\xff\xd8\xff" + b"fake-jpeg-body"
_NEW_JPEG_BYTES = b"\xff\xd8\xff" + b"refreshed-jpeg-body"


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = BytesIO(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._stream.read(size)


def test_feedback_dialog_shows_policy_without_qr(qtbot):
    dialog = FeedbackDialog()
    qtbot.addWidget(dialog)

    assert "复现" in dialog._summary_label.text()
    assert "不受理" in dialog._scope_label.text()
    assert dialog._group_btn.text() == "扫码加群"
    assert dialog._details_btn.text() == "查看详细规范"
    assert dialog._github_btn.text() == "提交 GitHub Issue"
    assert not hasattr(dialog, "_image_label")


def test_feedback_links_open_guide_and_new_issue(qtbot):
    dialog = FeedbackDialog()
    qtbot.addWidget(dialog)

    with patch(
        "lvjiang.ui.notices.feedback_dialog.QDesktopServices.openUrl"
    ) as open_url:
        dialog._details_btn.click()
        dialog._github_btn.click()

    assert [call.args[0].toString() for call in open_url.call_args_list] == [
        ISSUE_GUIDE_URL,
        NEW_ISSUE_URL,
    ]


def test_group_qr_is_opened_only_from_button(qtbot):
    dialog = FeedbackDialog()
    qtbot.addWidget(dialog)

    with patch("lvjiang.ui.notices.feedback_dialog.GroupQrDialog") as qr_dialog:
        dialog._group_btn.click()

    qr_dialog.assert_called_once_with(dialog)
    qr_dialog.return_value.exec.assert_called_once_with()


def test_group_qr_dialog_loads_image(qtbot):
    dialog = GroupQrDialog()
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "扫码加群"
    assert dialog._image_label.pixmap() is not None
    assert not dialog._image_label.pixmap().isNull()


def test_refresh_qrcode_downloads_and_replaces_local_file(qtbot, tmp_path, monkeypatch):
    target = tmp_path / "feedback-qrcode.jpg"
    target.write_bytes(_JPEG_BYTES)
    monkeypatch.setattr(group_qrcode, "QRCODE_PATH", target)
    monkeypatch.setattr(
        group_qrcode, "urlopen",
        lambda *args, **kwargs: _FakeResponse(_NEW_JPEG_BYTES))

    dialog = GroupQrDialog()
    qtbot.addWidget(dialog)

    dialog._refresh_btn.click()
    assert dialog._refresh_btn.text() == "刷新中..."
    assert not dialog._refresh_btn.isEnabled()

    qtbot.waitUntil(lambda: dialog._status_label.text() == "二维码已更新", timeout=2000)

    assert dialog._refresh_btn.isEnabled()
    assert dialog._refresh_btn.text() == "刷新二维码"
    assert target.read_bytes() == _NEW_JPEG_BYTES


def test_refresh_qrcode_keeps_old_file_on_failure(qtbot, tmp_path, monkeypatch):
    target = tmp_path / "feedback-qrcode.jpg"
    target.write_bytes(_JPEG_BYTES)
    monkeypatch.setattr(group_qrcode, "QRCODE_PATH", target)

    def raise_error(*args, **kwargs):
        raise TimeoutError("网络超时")

    monkeypatch.setattr(group_qrcode, "urlopen", raise_error)

    dialog = GroupQrDialog()
    qtbot.addWidget(dialog)

    dialog._refresh_btn.click()
    qtbot.waitUntil(lambda: dialog._refresh_btn.isEnabled(), timeout=2000)

    assert "刷新失败" in dialog._status_label.text()
    assert target.read_bytes() == _JPEG_BYTES


def test_feedback_dialog_has_compact_english_actions(qtbot):
    init_i18n("en_US")
    try:
        dialog = FeedbackDialog()
        qtbot.addWidget(dialog)

        assert dialog._group_btn.text() == "Join Group"
        assert dialog._details_btn.text() == "Reporting Guide"
        assert dialog._github_btn.text() == "Report on GitHub"
        assert all(button.width() > button.sizeHint().width() - 2 for button in (
            dialog._group_btn,
            dialog._details_btn,
            dialog._github_btn,
            dialog._close_btn,
        ))
    finally:
        init_i18n("zh_CN")
