"""YYSLS shared layout sizing helpers."""

from PyQt6.QtWidgets import QComboBox, QListWidget

from lvjiang.apps.yysls.ui.layout_helpers import (
    configure_navigation_list,
    fit_combo_to_contents,
)
from lvjiang.ui.theme import ThemeManager


def test_combo_and_popup_fit_longest_translated_item(qtbot):
    combo = QComboBox()
    combo.addItems(["无", "A much longer translated option"])
    qtbot.addWidget(combo)

    width = fit_combo_to_contents(combo, minimum=112)
    longest = combo.fontMetrics().horizontalAdvance(
        "A much longer translated option"
    )

    assert width > longest
    assert combo.minimumWidth() == width
    assert combo.view().minimumWidth() == width


def test_navigation_list_has_readable_width_and_row_height(qapp, qtbot):
    ThemeManager(qapp).apply("light")
    nav = QListWidget()
    configure_navigation_list(nav, minimum_width=200)
    nav.addItem("基础规则")
    qtbot.addWidget(nav)
    nav.show()
    qapp.processEvents()

    assert nav.minimumWidth() == 200
    assert nav.property("navigation") is True
    assert nav.visualItemRect(nav.item(0)).height() >= 34
