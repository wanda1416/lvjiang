"""User-overview content font-size scope tests."""

from unittest.mock import MagicMock

from PyQt6.QtWidgets import QPushButton

from lvjiang.ui.profile.tab import ProfileTab


def test_overview_font_size_changes_tables_but_not_toolbars(qtbot):
    tab = ProfileTab(MagicMock())
    qtbot.addWidget(tab)
    buttons = {button.text(): button for button in tab.findChildren(QPushButton)}
    top_size = buttons["刷新"].font().pointSize()
    bottom_size = buttons["数据模型"].font().pointSize()

    tab.apply_content_font_size(17)

    assert tab._tab_widget.font().pointSize() == 17
    assert all(table.font().pointSize() == 17 for table in tab._tables.values())
    assert all(
        table.horizontalHeader().font().pointSize() == 17
        for table in tab._tables.values()
    )
    assert buttons["刷新"].font().pointSize() == top_size
    assert buttons["数据模型"].font().pointSize() == bottom_size
