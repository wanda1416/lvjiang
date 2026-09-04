"""反馈对话框的外链与二维码刷新。

只测错了会咬人的部分：外链 URL 指向正确的文档页与新建 issue 页；刷新
二维码在后台线程下载并原子落盘，失败时保留用户本地原图。

对话框长什么样不在这里断言（按钮文案、styleSheet 里的 padding、窗口标题、
控件像素宽度）：改一次样式就红一片，却拦不住任何真 bug。英文文案由
tests/core/test_i18n_consistency.py 的棘轮全局保证（缺失上限 0），比在这里
逐个断言更严；样式机制本身的单测在 tests/ui/test_button_styles.py。
"""
from io import BytesIO
from unittest.mock import patch

from lvjiang.core import group_qrcode
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


def _join_refresher(qtbot, dialog) -> None:
    """先把后台线程收干净，再让排队信号投递到 UI。

    不能直接 qtbot.waitUntil 轮询状态文字：那样等待期间主事件循环会把
    deleteLater 处理掉，而工作线程可能还没退出——销毁运行中的 QThread
    会让整个 pytest worker 进程崩掉（满载并发下稳定复现）。先 wait()
    把线程 join 完，剩下的就只是确定会发生的事件投递。
    """
    refresher = dialog._refresher
    assert refresher is not None
    assert refresher.wait(5000), "二维码刷新线程未在 5s 内退出"
    # 线程已退，_refresher 置 None 说明成功/失败信号已经投递完毕。
    qtbot.waitUntil(lambda: dialog._refresher is None, timeout=1000)


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

    _join_refresher(qtbot, dialog)

    assert dialog._status_label.text() == "二维码已更新"
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
    _join_refresher(qtbot, dialog)

    assert "刷新失败" in dialog._status_label.text()
    assert target.read_bytes() == _JPEG_BYTES
