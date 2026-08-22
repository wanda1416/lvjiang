"""更新提示对话框布局测试。"""

from unittest.mock import patch

from lvjiang.core.update import ReleaseInfo
from lvjiang.ui.update_dialog import UpdateDialog


def test_four_action_buttons_are_single_row_and_equal_size(qtbot):
    release = ReleaseInfo(
        version="0.5.1",
        title="v0.5.1 — 功能更新",
        body="- 更新内容",
        published_at="2026-08-22T11:02:05Z",
        release_url="https://github.com/example/releases/tag/v0.5.1",
        download_url="https://example.test/app-v0.5.1-setup.exe",
    )
    with patch("lvjiang.ui.update_dialog.get_version", return_value="0.5.0"):
        dialog = UpdateDialog(release)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.wait(20)

    buttons = [
        dialog._skip_btn,
        dialog._release_notes_btn,
        dialog._download_btn,
        dialog._close_btn,
    ]
    assert [button.text() for button in buttons] == [
        "该版本不再提醒",
        "前往发布声明",
        "下载该版本",
        "忽略该提示",
    ]
    assert len({button.y() for button in buttons}) == 1
    assert len({button.height() for button in buttons}) == 1
    assert max(button.width() for button in buttons) - min(button.width() for button in buttons) <= 1
    assert "#4CAF50" in dialog._download_btn.styleSheet()
