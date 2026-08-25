"""Shared button styles for top toolbars and configuration dialogs."""

from __future__ import annotations

from PyQt6.QtWidgets import QPushButton

ACTION_BUTTON_STYLE = (
    "QPushButton { border: 1px solid palette(highlight); "
    "color: palette(highlight); border-radius: 5px; padding: 5px 11px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:pressed, QPushButton:checked { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

NEUTRAL_BUTTON_STYLE = (
    "QPushButton { background-color: palette(button); color: palette(button-text); "
    "border: 1px solid palette(mid); border-radius: 5px; padding: 5px 11px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: palette(midlight); }"
    "QPushButton:pressed, QPushButton:checked { background-color: palette(mid); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)

DANGER_BUTTON_STYLE = (
    "QPushButton { background-color: palette(button); color: #c62828; "
    "border: 1px solid #c62828; border-radius: 5px; padding: 5px 11px; "
    "font-weight: 600; }"
    "QPushButton:hover { background-color: rgba(198, 40, 40, 0.12); }"
    "QPushButton:pressed { background-color: rgba(198, 40, 40, 0.22); }"
    "QPushButton:disabled { color: palette(mid); border-color: palette(midlight); }"
)


def apply_button_style(
    *buttons: QPushButton,
    variant: str = "action",
) -> None:
    """Apply one consistent geometry with semantic colour variants."""
    styles = {
        "action": ACTION_BUTTON_STYLE,
        "neutral": NEUTRAL_BUTTON_STYLE,
        "danger": DANGER_BUTTON_STYLE,
    }
    style = styles[variant]
    for button in buttons:
        button.setStyleSheet(style)
