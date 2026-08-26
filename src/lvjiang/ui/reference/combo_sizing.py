"""Sizing helpers for combo boxes in the reference manager."""

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox


def set_combo_minimum_character_capacity(
    combo: QComboBox,
    character_count: int,
) -> int:
    """Guarantee a net content area for N full-width Chinese characters.

    Measuring only the glyphs is insufficient because the active Qt style also
    reserves room for the frame, padding, and drop-down arrow. Check the
    style's actual edit-field rectangle and grow the widget by any shortfall.
    """
    metrics = combo.fontMetrics()
    text_width = metrics.horizontalAdvance("汉" * character_count)
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.editable = combo.isEditable()
    option.frame = combo.hasFrame()
    style = combo.style()
    assert style is not None

    width = style.sizeFromContents(
        QStyle.ContentsType.CT_ComboBox,
        option,
        QSize(text_width, metrics.height()),
        combo,
    ).width()
    option.rect = QRect(0, 0, width, max(combo.sizeHint().height(), metrics.height()))
    content_width = style.subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    ).width()
    width += max(0, text_width - content_width)

    combo.setMinimumWidth(width)
    view = combo.view()
    assert view is not None
    view.setMinimumWidth(width)
    return width
