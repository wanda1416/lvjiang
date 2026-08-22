from PyQt6.QtGui import QColor, QPalette

from lvjiang.ui.theme import apply_light_theme


def test_apply_light_theme_overrides_dark_application_palette(qapp):
    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#101010"))
    dark.setColor(QPalette.ColorRole.Base, QColor("#080808"))
    dark.setColor(QPalette.ColorRole.Text, QColor("#eeeeee"))
    qapp.setPalette(dark)

    apply_light_theme(qapp)

    palette = qapp.palette()
    assert qapp.style().objectName().lower() == "fusion"
    assert palette.color(QPalette.ColorRole.Window) == QColor("#f0f0f0")
    assert palette.color(QPalette.ColorRole.Base) == QColor("#ffffff")
    assert palette.color(QPalette.ColorRole.Text) == QColor("#202020")
    assert palette.color(QPalette.ColorRole.Highlight) == QColor("#0078d4")


def test_light_theme_keeps_disabled_text_readable(qapp):
    apply_light_theme(qapp)

    color = qapp.palette().color(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
    )
    assert color == QColor("#787878")
