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
    combo.ensurePolished()
    metrics = combo.fontMetrics()
    text_width = max(
        metrics.horizontalAdvance("汉" * character_count),
        max(
            (metrics.horizontalAdvance(combo.itemText(index))
             for index in range(combo.count())),
            default=0,
        ),
    )
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
    view.ensurePolished()
    # 弹出列表没有下拉箭头，但列表项及外框仍需要左右留白。若直接复用
    # 控件宽度，在较大字体或平台样式下四个汉字会被压缩/省略。
    view.setMinimumWidth(width + 12)
    return width
