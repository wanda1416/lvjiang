"""Main-window layout sizing tests."""

from PyQt6.QtWidgets import QComboBox

from lvjiang.ui.main_window import (
    _TOP_COMBO_CHARACTER_CAPACITY,
    _set_combo_character_capacity,
)


def test_top_combo_capacity_fits_six_chinese_characters(qtbot):
    combo = QComboBox()
    qtbot.addWidget(combo)

    width = _set_combo_character_capacity(combo)
    text_width = combo.fontMetrics().horizontalAdvance(
        "汉" * _TOP_COMBO_CHARACTER_CAPACITY
    )

    assert width > text_width  # 还包含边框、内边距和下拉箭头
    assert combo.minimumWidth() == width
    assert combo.maximumWidth() == width
