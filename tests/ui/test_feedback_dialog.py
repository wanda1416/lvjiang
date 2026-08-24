"""反馈规范对话框与交流群二维码子对话框测试。"""
from unittest.mock import patch

from lvjiang.i18n import init_i18n
from lvjiang.ui.feedback_dialog import (
    ISSUE_GUIDE_URL,
    NEW_ISSUE_URL,
    FeedbackDialog,
    GroupQrDialog,
)


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
        "lvjiang.ui.feedback_dialog.QDesktopServices.openUrl"
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

    with patch("lvjiang.ui.feedback_dialog.GroupQrDialog") as qr_dialog:
        dialog._group_btn.click()

    qr_dialog.assert_called_once_with(dialog)
    qr_dialog.return_value.exec.assert_called_once_with()


def test_group_qr_dialog_loads_image(qtbot):
    dialog = GroupQrDialog()
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "扫码加群"
    assert dialog._image_label.pixmap() is not None
    assert not dialog._image_label.pixmap().isNull()


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
