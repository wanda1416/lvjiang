"""Reference-manager layout sizing and cut-result editor tests."""

import numpy as np
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox

from lvjiang.core.reference_db import MetaFieldDef
from lvjiang.ui.reference.browser_panel import _reference_file_text
from lvjiang.ui.reference.combo_sizing import (
    set_combo_minimum_character_capacity,
)
from lvjiang.ui.reference.dialog import (
    _SPACE_COMBO_CHARACTER_CAPACITY,
    _set_space_combo_minimum_capacity,
)
from lvjiang.ui.reference.grid_panel import GridPanel
from lvjiang.ui.reference.meta_schema_panel import (
    _META_COMBO_CHARACTER_CAPACITY,
    MetaSchemaPanel,
)


def test_reference_file_info_includes_pixel_dimensions(tmp_path):
    path = tmp_path / "sample.png"
    image = QImage(37, 19, QImage.Format.Format_RGB888)
    assert image.save(str(path))

    assert _reference_file_text("sample.png", path) == (
        "文件: sample.png (37 × 19)"
    )


def test_reference_file_info_falls_back_when_image_is_missing(tmp_path):
    assert _reference_file_text("missing.png", tmp_path / "missing.png") == (
        "文件: missing.png"
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


def test_cut_result_only_edits_input_metadata_and_includes_notes(qtbot):
    panel = GridPanel()
    qtbot.addWidget(panel)
    panel.set_meta_fields([
        MetaFieldDef(key="level", name="等级", scope="input"),
        MetaFieldDef(
            key="level_text", name="等级文本区域", scope="output",
            crop=[0.0, 0.0, 1.0, 0.5],
        ),
        MetaFieldDef(
            key="count_text", name="数量文本区域", scope="output",
            crop=[0.0, 0.5, 1.0, 0.5],
        ),
    ])
    panel.show_cut_cells([np.zeros((12, 12, 3), dtype=np.uint8)])

    editor = panel._cell_editors[0]
    assert set(editor._meta_edits) == {"level"}
    assert editor.notes_edit is not None

    editor.label_edit.setEditText("金狗粮")
    editor.group_edit.setEditText("调律材料")
    editor.notes_edit.setText("测试备注")
    editor._meta_edits["level"].setText("110")
    emitted = []
    panel.submit_cells.connect(emitted.extend)
    panel._on_submit_cells()

    assert emitted[0][1:] == (
        "金狗粮", "调律材料", "测试备注", {"level": "110"},
    )
