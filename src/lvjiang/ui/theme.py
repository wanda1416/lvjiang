"""Application-wide visual theme configuration."""

from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


def apply_light_theme(app: QApplication) -> None:
    """Use a deterministic light theme instead of the operating-system theme.

    The UI still contains several light-background style sheets.  Letting Qt
    inherit the Windows dark palette therefore produces a mixture of dark
    native controls and light custom widgets.  An explicit Fusion palette
    keeps the complete application readable regardless of the system setting.
    """
    app.setStyle("Fusion")

    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, QColor("#f0f0f0"))
    palette.setColor(role.WindowText, QColor("#202020"))
    palette.setColor(role.Base, QColor("#ffffff"))
    palette.setColor(role.AlternateBase, QColor("#f7f7f7"))
    palette.setColor(role.ToolTipBase, QColor("#ffffdc"))
    palette.setColor(role.ToolTipText, QColor("#202020"))
    palette.setColor(role.Text, QColor("#202020"))
    palette.setColor(role.Button, QColor("#f0f0f0"))
    palette.setColor(role.ButtonText, QColor("#202020"))
    palette.setColor(role.BrightText, QColor("#ffffff"))
    palette.setColor(role.Link, QColor("#0067c0"))
    palette.setColor(role.LinkVisited, QColor("#5c2d91"))
    palette.setColor(role.Highlight, QColor("#0078d4"))
    palette.setColor(role.HighlightedText, QColor("#ffffff"))
    palette.setColor(role.PlaceholderText, QColor("#767676"))
    palette.setColor(role.Light, QColor("#ffffff"))
    palette.setColor(role.Midlight, QColor("#e3e3e3"))
    palette.setColor(role.Dark, QColor("#a0a0a0"))
    palette.setColor(role.Mid, QColor("#b8b8b8"))
    palette.setColor(role.Shadow, QColor("#696969"))

    disabled = QPalette.ColorGroup.Disabled
    palette.setColor(disabled, role.WindowText, QColor("#787878"))
    palette.setColor(disabled, role.Text, QColor("#787878"))
    palette.setColor(disabled, role.ButtonText, QColor("#787878"))
    palette.setColor(disabled, role.Highlight, QColor("#b8b8b8"))
    palette.setColor(disabled, role.HighlightedText, QColor("#f0f0f0"))

    app.setPalette(palette)
