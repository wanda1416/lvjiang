"""YYSLS shared layout sizing helpers."""

from PyQt6.QtWidgets import QComboBox, QListWidget

from lvjiang.apps.yysls.ui.layout_helpers import (
    configure_navigation_list,
    fit_combo_popup_to_contents,
    fit_combo_to_contents,
    mark_navigation_list,
    navigation_width_for_chars,
)
from lvjiang.apps.yysls.ui.tune_settings.condition_editor import _ConditionRow
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


def test_combo_popup_can_expand_without_widening_closed_control(qtbot):
    combo = QComboBox()
    combo.setFixedWidth(80)
    combo.addItems(["短", "A much longer translated popup option"])
    qtbot.addWidget(combo)

    width = fit_combo_popup_to_contents(combo)

    assert combo.width() == 80
    assert width > combo.width()
    assert combo.view().minimumWidth() == width


def test_condition_kind_combo_displays_all_four_labels_in_full(qtbot):
    row = _ConditionRow(
        ["contains_all", "not_together", "count_max", "count_min"],
        [],
    )
    qtbot.addWidget(row)

    longest = max(
        row.kind_combo.fontMetrics().horizontalAdvance(row.kind_combo.itemText(i))
        for i in range(row.kind_combo.count())
    )

    assert row.kind_combo.count() == 4
    assert row.kind_combo.minimumWidth() > longest
    assert row.kind_combo.view().minimumWidth() == row.kind_combo.minimumWidth()


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


def test_width_measured_after_theme_matches_rendered_font(qapp, qtbot):
    """量宽度前必须先 mark，否则量到的是默认字体、比最终字体窄一号。

    主题样式表给 navigation 列表指定了 font-size，未 polish 时 fontMetrics()
    还是应用默认字体。之前 rules_dialog/rule_panel 正是在 mark 之前量的，
    导致左侧导航按窄字体分配宽度，主题一生效就偏窄。
    """
    ThemeManager(qapp).apply("light")

    bare = QListWidget()
    qtbot.addWidget(bare)
    before = navigation_width_for_chars(bare, 8)

    mark_navigation_list(bare)
    after = navigation_width_for_chars(bare, 8)

    # 主题字体比默认字体大，mark 之后量出来必须更宽
    assert after > before

    # 且这就是控件显示出来以后的最终宽度——再 show 一次不会再变
    bare.show()
    qapp.processEvents()
    assert navigation_width_for_chars(bare, 8) == after
