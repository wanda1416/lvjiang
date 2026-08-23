"""Qt standard dialog localization and button-spacing tests."""

from __future__ import annotations

from PyQt6.QtWidgets import QDialogButtonBox

from lvjiang.i18n import init_i18n
from lvjiang.ui.theme import ThemeManager


def _standard_buttons():
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok
        | QDialogButtonBox.StandardButton.Cancel
    )
    ok_button = box.button(QDialogButtonBox.StandardButton.Ok)
    cancel_button = box.button(QDialogButtonBox.StandardButton.Cancel)
    assert ok_button is not None
    assert cancel_button is not None
    return box, ok_button, cancel_button


def test_standard_buttons_follow_chinese_and_english_language(qapp):
    try:
        init_i18n("zh_CN")
        zh_box, zh_ok, zh_cancel = _standard_buttons()
        assert zh_ok.text() == "确定"
        assert zh_cancel.text() == "取消"

        init_i18n("en_US")
        en_box, en_ok, en_cancel = _standard_buttons()
        assert en_ok.text() == "OK"
        assert en_cancel.text() == "Cancel"

        # Keep the boxes alive while their buttons are inspected.
        assert zh_box.buttons() and en_box.buttons()
    finally:
        init_i18n("zh_CN")


def test_dialog_buttons_keep_horizontal_padding_for_unequal_text(qapp):
    ThemeManager(qapp).apply("light")
    init_i18n("en_US")
    try:
        box, ok_button, cancel_button = _standard_buttons()
        for button in (ok_button, cancel_button):
            text_width = button.fontMetrics().horizontalAdvance(button.text())
            assert button.sizeHint().width() >= text_width + 36
        assert cancel_button.sizeHint().width() > ok_button.sizeHint().width()
        assert box.buttons()
    finally:
        init_i18n("zh_CN")
