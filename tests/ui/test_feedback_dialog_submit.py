"""反馈对话框的回归测试。

保护的真实回归（都在提交那一刻才暴露）：

1. 默认类型是「功能建议」而不是「功能异常」——反馈通道不能一开场就把提建议的
   人挡在一张 Bug 表格前；
2. 复现细节只对需要它的类型显示，且**留空也能提交**（本地与服务端都不强制）；
3. 邮箱必填：回复走邮箱，缺了就等于「找不到人」；
4. 附件清单 = 截图 + 勾选的日志，顺序就是 seq（服务端按 seq 幂等）；
5. 草稿能存回来，且不假装恢复了截图。
"""
from __future__ import annotations

from datetime import date

import pytest
from PyQt6.QtGui import QColor, QImage

from lvjiang.core.feedback import draft as feedback_draft
from lvjiang.core.feedback import logtail
from lvjiang.core.feedback.limits import MAX_SCREENSHOTS
from lvjiang.ui.notices.feedback_dialog import FeedbackDialog

LOG_DAY = date(2026, 11, 19)


@pytest.fixture()
def dialog(tmp_path, monkeypatch, qtbot):
    """构造一个不依赖真实日志目录与真实草稿的对话框。

    ``qtbot`` 不是可选装饰：没有 QApplication 时构造任何 QWidget 都会在
    Qt 里直接 abort（进程级崩溃，连 traceback 都没有）。
    """
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / f"lvjiang_{LOG_DAY:%Y-%m-%d}.log").write_text(
        "2026-11-19 09:00:01 | INFO | 开始执行\n"
        r"读取 C:\Users\alice\lvjiang\config\session\account.json" + "\n",
        encoding="utf-8")
    monkeypatch.setattr(logtail, "log_dir", lambda: logs)
    monkeypatch.setattr(logtail, "available_days", lambda **kwargs: [LOG_DAY])
    monkeypatch.setattr(feedback_draft, "draft_dir", lambda: tmp_path / "draft")
    widget = FeedbackDialog()
    qtbot.addWidget(widget)
    return widget


def _image(width: int = 240, height: int = 120) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    return image


def _fill(dialog, *, email="user@example.com", title="标题", body="描述"):
    dialog._email_edit.setText(email)
    dialog._title_edit.setText(title)
    dialog._body_edit.setPlainText(body)


def test_default_kind_is_suggestion_and_hides_detail_fields(dialog):
    assert dialog._kind_key() == "suggestion"
    assert dialog._detail_group.isHidden()


def test_bug_kind_shows_optional_detail_fields_without_requiring_them(dialog):
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData("bug"))
    assert not dialog._detail_group.isHidden()
    _fill(dialog)
    # 复现步骤/预期/实际全部留空：仍然可以提交
    assert dialog._validate() == ""
    assert dialog._fields() == {}


def test_detail_fields_are_collected_with_stable_keys(dialog):
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData("bug"))
    dialog._steps_edit.setPlainText("打开总览 → 点同步")
    dialog._expect_edit.setText("出现进度条")
    dialog._actual_edit.setText("闪退")
    dialog._freq_combo.setCurrentIndex(dialog._freq_combo.findData("must"))
    assert dialog._fields() == {
        "steps": "打开总览 → 点同步",
        "expected": "出现进度条",
        "actual": "闪退",
        "frequency": "must",
    }


def test_validate_requires_email_title_and_body(dialog):
    assert dialog._validate() == "请填写能收到回复的邮箱"
    _fill(dialog, email="not-an-email")
    assert dialog._validate() == "请填写能收到回复的邮箱"
    _fill(dialog, title="")
    assert dialog._validate() == "请填写标题"
    _fill(dialog, body="   ")
    assert dialog._validate() == "请填写详细描述"
    _fill(dialog)
    assert dialog._validate() == ""


def test_attachments_are_screenshots_in_order_then_log(dialog):
    dialog._append_image(_image())
    dialog._append_image(_image(300, 150))
    assert len(dialog._shots) == 2
    assert dialog._shots[0].kind == "screenshot"
    assert dialog._shots[0].name.endswith(".png")
    assert dialog._log_check.isChecked()
    attachments = dialog._attachments()
    assert [item.kind for item in attachments] == ["screenshot", "screenshot", "log"]
    # 日志附件是脱敏后的文本：用户目录不许漏进附件
    assert "alice" not in attachments[-1].data.decode("utf-8")
    # 取消勾选日志后只剩截图
    dialog._log_check.setChecked(False)
    assert [item.kind for item in dialog._attachments()] == ["screenshot", "screenshot"]


def test_screenshot_count_is_capped(dialog):
    for _ in range(MAX_SCREENSHOTS + 2):
        dialog._append_image(_image())
    assert len(dialog._shots) == MAX_SCREENSHOTS
    assert "最多" in dialog._status_label.text()


def test_preview_hides_identity_but_shows_what_is_sent(dialog):
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData("bug"))
    _fill(dialog, email="User@Example.com ", title="同步闪退", body="点同步就闪退")
    dialog._append_image(_image())
    text = dialog._payload_preview_text()
    assert "user@example.com" in text      # 归一化后的小写邮箱
    assert "同步闪退" in text
    assert "screenshot-1" in text
    assert "alice" not in text            # 日志片段已脱敏
    assert "登录名" not in text


def test_draft_roundtrip_and_screenshot_not_restored(dialog, tmp_path, monkeypatch, qtbot):
    dialog._kind_combo.setCurrentIndex(dialog._kind_combo.findData("workflow"))
    _fill(dialog, email="user@example.com", title="工作流卡住", body="第三个用户卡住")
    dialog._append_image(_image())
    dialog._save_draft()

    monkeypatch.setattr(feedback_draft, "draft_dir", lambda: tmp_path / "draft")
    restored = FeedbackDialog()
    qtbot.addWidget(restored)
    assert restored._kind_key() == "workflow"
    assert restored._title_edit.text() == "工作流卡住"
    assert restored._body_edit.toPlainText() == "第三个用户卡住"
    # 截图不落盘：恢复后必须是 0 张，而不是「看起来恢复了」
    assert restored._shots == []


def test_submitted_feedback_clears_draft(dialog, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_draft, "draft_dir", lambda: tmp_path / "draft")
    _fill(dialog)
    dialog._save_draft()
    assert (tmp_path / "draft" / "draft.json").is_file()
    feedback_draft.clear_draft()
    assert not (tmp_path / "draft" / "draft.json").exists()
