"""图像识别对话框：清洗规则改为显式保存 + 两个识别按钮补齐样式。

原来「清洗规则」页是边改边写：cellChanged → 立即回写整份配置。问题在于
填表用的 setItem 同样会触发 cellChanged，于是**光是打开对话框**就把配置
原样回写了 22 遍。用户模式下这还会平白落一份 local 影子——而 config/local
在 .gitignore 里，用户执行 git diff 什么都看不到，只会觉得"我什么都没动，
它却说改了/却写盘了"。

改成显式保存：填表期间屏蔽表格信号；用户改动只记脏；落盘等点「保存」；
没有改动时「保存」置灰；带着未保存改动关窗口先弹确认。
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMessageBox, QTableWidgetItem

import lvjiang.core.ocr_cleaner as ocr_cleaner_mod
import lvjiang.ui.ocr.dialog as dialog_mod
from lvjiang.ui.ocr.dialog import OCRDialog


@pytest.fixture
def writes(monkeypatch):
    """拦住真正的落盘，只统计次数（不碰用户真实配置）。"""
    calls: list[int] = []
    monkeypatch.setattr(ocr_cleaner_mod.OCRCleaner, "_save_config",
                        lambda self: calls.append(1))
    ocr_cleaner_mod.OCRCleaner.reset_instance()
    yield calls
    ocr_cleaner_mod.OCRCleaner.reset_instance()


@pytest.fixture
def dialog(qtbot, writes):
    d = OCRDialog()
    qtbot.addWidget(d)
    return d


class TestOpeningDoesNotWrite:
    def test_opening_dialog_writes_nothing(self, dialog, writes):
        assert writes == [], "仅仅打开对话框不该产生任何写盘"

    def test_save_button_starts_disabled(self, dialog):
        assert not dialog._btn_save_rules.isEnabled(), "没有改动时「保存」应置灰"

    def test_editing_marks_dirty_but_still_no_write(self, dialog, writes):
        dialog._repl_table.setItem(0, 1, QTableWidgetItem("改过了"))
        assert dialog._btn_save_rules.isEnabled(), "改动后「保存」应可点"
        assert writes == [], "改动只记脏，不该立刻写盘"


class TestExplicitSave:
    def test_save_writes_and_clears_dirty(self, dialog, writes):
        dialog._repl_table.setItem(0, 1, QTableWidgetItem("改过了"))
        assert dialog._on_save_rules() is True
        assert writes, "点保存才应该真的写盘"
        assert not dialog._btn_save_rules.isEnabled(), "保存后应重新置灰"

    def test_invalid_regex_blocks_save(self, dialog, writes):
        """正则非法时不能落盘，也不能把脏标记清掉。"""
        dialog._pattern_table.insertRow(0)
        dialog._pattern_table.setItem(0, 0, QTableWidgetItem("([unclosed"))
        dialog._pattern_table.setItem(0, 1, QTableWidgetItem("x"))
        assert dialog._on_save_rules() is False
        assert writes == [], "正则非法时不该写盘"
        assert dialog._btn_save_rules.isEnabled(), "保存失败后仍是未保存状态"

    def test_cancel_reverts_without_writing(self, dialog, writes):
        original = dialog._repl_table.item(0, 1).text()
        dialog._repl_table.setItem(0, 1, QTableWidgetItem("临时乱改"))
        dialog._on_cancel_rules()
        assert writes == [], "取消不该写盘"
        assert dialog._repl_table.item(0, 1).text() == original, "取消应还原成已保存内容"
        assert not dialog._btn_save_rules.isEnabled()


class TestCloseGuard:
    """带着未保存改动关窗口要拦一道，否则改了半天点 X 就没了。"""

    def _close(self, dialog, monkeypatch, answer):
        asked: list[int] = []
        monkeypatch.setattr(
            dialog_mod.QMessageBox, "question",
            staticmethod(lambda *a, **k: (asked.append(1), answer)[1]))
        closed: list[int] = []
        monkeypatch.setattr(dialog, "done", lambda code: closed.append(code))
        dialog.reject()
        return bool(asked), bool(closed)

    def test_clean_close_does_not_prompt(self, dialog, monkeypatch):
        asked, closed = self._close(dialog, monkeypatch,
                                    QMessageBox.StandardButton.Cancel)
        assert not asked, "没有未保存改动时不该弹窗"
        assert closed, "没有改动应直接关闭"

    def test_dirty_close_cancel_keeps_dialog_open(self, dialog, monkeypatch, writes):
        dialog._repl_table.setItem(0, 1, QTableWidgetItem("改过了"))
        asked, closed = self._close(dialog, monkeypatch,
                                    QMessageBox.StandardButton.Cancel)
        assert asked and not closed, "选取消应留在对话框里"
        assert writes == []

    def test_dirty_close_discard_closes_without_writing(self, dialog, monkeypatch, writes):
        dialog._repl_table.setItem(0, 1, QTableWidgetItem("改过了"))
        asked, closed = self._close(dialog, monkeypatch,
                                    QMessageBox.StandardButton.Discard)
        assert asked and closed
        assert writes == [], "放弃修改不该写盘"

    def test_dirty_close_save_writes_then_closes(self, dialog, monkeypatch, writes):
        dialog._repl_table.setItem(0, 1, QTableWidgetItem("改过了"))
        asked, closed = self._close(dialog, monkeypatch,
                                    QMessageBox.StandardButton.Save)
        assert asked and closed
        assert writes, "选保存应落盘后再关闭"


class TestRecognitionButtonsStyled:
    """两个识别按钮此前漏了统一样式，跟同页其他按钮长得不一样。"""

    def test_buttons_share_page_style(self, dialog):
        expected = dialog._btn_refresh.styleSheet()
        assert expected, "同页参照按钮本身应当有样式"
        assert dialog._btn_ocr.styleSheet() == expected
        assert dialog._btn_reference.styleSheet() == expected
