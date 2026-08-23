"""Shared sizing and styling helpers for YYSLS configuration UIs."""

from __future__ import annotations

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import (
    QComboBox,
    QListWidget,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
)


def fit_combo_to_contents(combo: QComboBox, *, minimum: int = 0) -> int:
    """Keep both the combo and its popup wide enough for the longest item."""
    text_width = max(
        (combo.fontMetrics().horizontalAdvance(combo.itemText(index))
         for index in range(combo.count())),
        default=0,
    )
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    style = combo.style()
    assert style is not None
    width = style.sizeFromContents(
        QStyle.ContentsType.CT_ComboBox,
        option,
        QSize(text_width, combo.fontMetrics().height()),
        combo,
    ).width()
    width = max(minimum, width + 12)
    combo.setMinimumWidth(width)
    view = combo.view()
    assert view is not None
    view.setMinimumWidth(width)
    return width


def configure_navigation_list(
    nav: QListWidget, *, minimum_width: int
) -> None:
    """Apply the shared spacious navigation-list presentation."""
    nav.setProperty("navigation", True)
    nav.setMinimumWidth(minimum_width)
    nav.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    nav.setSpacing(2)
