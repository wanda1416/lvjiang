"""公告中心展示测试。"""
from unittest.mock import patch

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QPushButton

from lvjiang.core.announcement import Announcement, AnnouncementManifest
from lvjiang.ui.notices.announcement_dialog import AnnouncementDialog


def test_dialog_lists_notices_and_renders_markdown(qtbot):
    manifest = AnnouncementManifest(
        schema_version=1,
        notice_version=7,
        updated_at="2026-08-24T10:00:00Z",
        notices=(
            Announcement(
                id="critical", level="critical", title="严重问题",
                body="## 请注意\n\n暂停使用。", published_at="2026-08-24T10:00:00Z",
                url="https://example.test/details",
            ),
            Announcement(
                id="inactive", level="info", title="已撤回",
                body="不可见", active=False,
            ),
        ),
    )
    dialog = AnnouncementDialog(manifest, allow_refresh=False)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "公告"
    assert dialog._list.count() == 1
    assert "严重问题" in dialog._list.item(0).text()
    assert dialog._title_label.text() == "严重问题"
    assert "暂停使用" in dialog._body.toPlainText()
    assert dialog._details_btn.isEnabled()
    assert not dialog._refresh_btn.isVisible()


def test_startup_dialog_can_show_only_matching_subset(qtbot):
    first = Announcement(id="one", level="warning", title="目标公告", body="内容")
    second = Announcement(id="two", level="info", title="其它公告", body="内容")
    manifest = AnnouncementManifest(1, 2, "", (first, second))
    dialog = AnnouncementDialog(
        manifest, notices=(first,), allow_refresh=False)
    qtbot.addWidget(dialog)

    assert dialog._list.count() == 1
    assert "目标公告" in dialog._list.item(0).text()


def test_empty_cache_has_manual_refresh_guidance(qtbot):
    dialog = AnnouncementDialog(None)
    qtbot.addWidget(dialog)

    assert "重新获取" in dialog._status_label.text()
    assert not dialog._refresh_btn.isHidden()
    assert not dialog._details_btn.isEnabled()


def test_all_announcement_actions_use_shared_button_styles(qtbot):
    dialog = AnnouncementDialog(None)
    qtbot.addWidget(dialog)

    buttons = dialog.findChildren(QPushButton)
    assert {button.text() for button in buttons} == {
        "重新获取", "查看详情", "关闭",
    }
    assert all("padding: 5px 11px" in button.styleSheet() for button in buttons)
    close = next(button for button in buttons if button.text() == "关闭")
    assert "palette(button)" in close.styleSheet()


def test_body_links_only_open_https():
    with patch(
        "lvjiang.ui.notices.announcement_dialog.QDesktopServices.openUrl"
    ) as open_url:
        AnnouncementDialog._open_body_link(QUrl("http://example.test/plain"))
        AnnouncementDialog._open_body_link(QUrl("javascript:alert(1)"))
        AnnouncementDialog._open_body_link(QUrl("https://example.test/safe"))

    open_url.assert_called_once()
    assert open_url.call_args.args[0].toString() == "https://example.test/safe"
