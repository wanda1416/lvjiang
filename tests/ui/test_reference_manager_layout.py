"""Reference-manager layout sizing tests."""

from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox

from lvjiang.ui.reference_manager.combo_sizing import (
    set_combo_minimum_character_capacity,
)
from lvjiang.ui.reference_manager.dialog import (
    _SPACE_COMBO_CHARACTER_CAPACITY,
    _set_space_combo_minimum_capacity,
)
from lvjiang.ui.reference_manager.meta_schema_panel import (
    _META_COMBO_CHARACTER_CAPACITY,
    MetaSchemaPanel,
)


def test_space_combo_fits_six_chinese_characters(qtbot):
    combo = QComboBox()
    qtbot.addWidget(combo)

    width = _set_space_combo_minimum_capacity(combo)
    text_width = combo.fontMetrics().horizontalAdvance(
        "汉" * _SPACE_COMBO_CHARACTER_CAPACITY
    )

    assert width > text_width  # 还包含边框、内边距和下拉箭头
    assert combo.minimumWidth() == width
    assert combo.maximumWidth() > width  # 仅设下限，长空间名仍可继续扩展
    assert combo.view().minimumWidth() == width


def test_combo_capacity_excludes_arrow_and_frame(qtbot):
    combo = QComboBox()
    qtbot.addWidget(combo)

    width = set_combo_minimum_character_capacity(combo, 4)
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.rect = combo.rect()
    option.rect.setWidth(width)
    content_rect = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    )

    assert content_rect.width() >= combo.fontMetrics().horizontalAdvance("汉" * 4)


def test_meta_type_and_sort_combos_fit_four_chinese_characters(qtbot):
    class StubDatabase:
        @staticmethod
        def get_match_threshold():
            return 0.8

        @staticmethod
        def get_meta_schema():
            return []

    panel = MetaSchemaPanel(StubDatabase())
    qtbot.addWidget(panel)
    panel.resize(800, 600)
    panel.show()

    for column in (2, 3):
        combo = panel._input_table.cellWidget(0, column)
        assert isinstance(combo, QComboBox)
        text_width = combo.fontMetrics().horizontalAdvance(
            "汉" * _META_COMBO_CHARACTER_CAPACITY
        )
        option = QStyleOptionComboBox()
        option.initFrom(combo)
        option.rect = combo.rect()
        option.rect.setWidth(combo.minimumWidth())
        content_rect = combo.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            combo,
        )

        assert content_rect.width() >= text_width
        assert panel._input_table.columnWidth(column) >= combo.minimumWidth()
