"""Application-wide light and dark theme support.

The theme layer owns neutral UI colours.  Business colours (equipment quality,
success/warning/error states) may still be supplied by individual widgets, but
neutral backgrounds, borders, and text should come from the Qt palette or the
semantic dynamic properties styled here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

ThemeName = Literal["light", "dark"]
DEFAULT_THEME: ThemeName = "light"


@dataclass(frozen=True)
class ThemeTokens:
    window: str
    surface: str
    surface_alt: str
    surface_hover: str
    surface_pressed: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_disabled: str
    accent: str
    accent_hover: str
    accent_pressed: str
    selection_text: str
    danger: str
    danger_surface: str
    warning: str
    warning_surface: str
    info: str
    info_surface: str
    success: str
    success_surface: str
    quality_gold: str
    quality_purple: str
    quality_blue: str
    quality_green: str


LIGHT = ThemeTokens(
    window="#f5f6f8",
    surface="#ffffff",
    surface_alt="#f0f2f4",
    surface_hover="#e7ebef",
    surface_pressed="#dce2e7",
    border="#d4d9de",
    border_strong="#aeb7c0",
    text="#202124",
    text_muted="#5f6368",
    text_disabled="#9aa0a6",
    accent="#0078d4",
    accent_hover="#106ebe",
    accent_pressed="#005a9e",
    selection_text="#ffffff",
    danger="#c62828",
    danger_surface="#ffebee",
    warning="#9a5a00",
    warning_surface="#fff3e0",
    info="#1557a0",
    info_surface="#e3f2fd",
    success="#247a3b",
    success_surface="#e8f5e9",
    quality_gold="#b8860b",
    quality_purple="#8b5cf6",
    quality_blue="#2563eb",
    quality_green="#16863e",
)

DARK = ThemeTokens(
    window="#202327",
    surface="#292d32",
    surface_alt="#32373d",
    surface_hover="#3a4148",
    surface_pressed="#454d55",
    border="#47505a",
    border_strong="#68737f",
    text="#e8eaed",
    text_muted="#b0b7bf",
    text_disabled="#747d87",
    accent="#4da3e6",
    accent_hover="#69b2ea",
    accent_pressed="#3189cf",
    selection_text="#ffffff",
    danger="#ff7b7b",
    danger_surface="#4a272b",
    warning="#ffb45b",
    warning_surface="#49351f",
    info="#83bfff",
    info_surface="#203b56",
    success="#73d58a",
    success_surface="#203f2b",
    quality_gold="#ffd166",
    quality_purple="#c4a0ff",
    quality_blue="#73a7ff",
    quality_green="#62d58a",
)

THEMES: dict[ThemeName, ThemeTokens] = {"light": LIGHT, "dark": DARK}


def normalize_theme(value: object) -> ThemeName:
    """Return a supported theme name, falling back safely for old configs."""
    return cast(ThemeName, value) if value in THEMES else DEFAULT_THEME


def _palette(tokens: ThemeTokens) -> QPalette:
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.window,
        QPalette.ColorRole.WindowText: tokens.text,
        QPalette.ColorRole.Base: tokens.surface,
        QPalette.ColorRole.AlternateBase: tokens.surface_alt,
        QPalette.ColorRole.ToolTipBase: tokens.surface,
        QPalette.ColorRole.ToolTipText: tokens.text,
        QPalette.ColorRole.Text: tokens.text,
        QPalette.ColorRole.Button: tokens.surface_alt,
        QPalette.ColorRole.ButtonText: tokens.text,
        QPalette.ColorRole.BrightText: tokens.selection_text,
        QPalette.ColorRole.Highlight: tokens.accent,
        QPalette.ColorRole.HighlightedText: tokens.selection_text,
        QPalette.ColorRole.Link: tokens.accent,
        QPalette.ColorRole.Mid: tokens.text_muted,
        QPalette.ColorRole.Midlight: tokens.border,
        QPalette.ColorRole.Dark: tokens.border_strong,
        QPalette.ColorRole.Shadow: tokens.window,
        QPalette.ColorRole.PlaceholderText: tokens.text_muted,
    }
    for role, colour in roles.items():
        palette.setColor(role, QColor(colour))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(
            QPalette.ColorGroup.Disabled, role, QColor(tokens.text_disabled)
        )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Highlight,
        QColor(tokens.surface_pressed),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.HighlightedText,
        QColor(tokens.text_disabled),
    )
    return palette


def _stylesheet(t: ThemeTokens) -> str:
    """Build the global QSS. Qt QSS has no variables, so tokens are rendered."""
    return f"""
QWidget {{
    color: {t.text};
}}
QMainWindow, QDialog {{
    background-color: {t.window};
}}
QGroupBox {{
    border: 1px solid {t.border};
    border-radius: 6px;
    margin-top: 8px;
    padding-top: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
QComboBox, QListView, QTreeView, QTableView, QListWidget, QTableWidget {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 4px;
    selection-background-color: {t.accent};
    selection-color: {t.selection_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus, QListView:focus, QTreeView:focus,
QTableView:focus {{
    border-color: {t.accent};
}}
QComboBox QAbstractItemView {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border_strong};
    selection-background-color: {t.accent};
    selection-color: {t.selection_text};
}}
QPushButton, QToolButton {{
    background-color: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border};
    border-radius: 4px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {t.surface_hover};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {t.surface_pressed};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: {t.text_disabled};
    border-color: {t.border};
}}
QDialogButtonBox QPushButton {{
    padding: 6px 18px;
}}
QListWidget[navigation="true"] {{
    font-size: 14px;
    padding: 4px;
}}
QListWidget[navigation="true"]::item {{
    min-height: 24px;
    padding: 5px 10px;
    border-radius: 4px;
}}
QListWidget[navigation="true"]::item:hover {{
    background-color: {t.surface_hover};
}}
QListWidget[navigation="true"]::item:selected {{
    background-color: {t.accent};
    color: {t.selection_text};
}}
QTabWidget::pane {{
    border: 1px solid {t.border};
    background-color: {t.window};
}}
QTabBar::tab {{
    background-color: {t.surface_alt};
    color: {t.text_muted};
    border: 1px solid {t.border};
    padding: 6px 12px;
}}
QTabBar::tab:selected {{
    background-color: {t.surface};
    color: {t.text};
    border-bottom-color: {t.surface};
}}
QTabBar::tab:hover:!selected {{
    background-color: {t.surface_hover};
}}
QMenuBar {{
    background-color: {t.window};
    color: {t.text};
}}
QMenuBar::item {{
    padding: 6px 16px;
    background: transparent;
}}
QMenuBar::item:selected {{
    background-color: {t.surface_hover};
}}
QMenu {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border_strong};
}}
QMenu::item {{ padding: 8px 32px; }}
QMenu::item:selected {{
    background-color: {t.accent};
    color: {t.selection_text};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t.border};
    margin: 4px 8px;
}}
QToolTip {{
    background-color: {t.surface};
    color: {t.text};
    border: 1px solid {t.border_strong};
}}
QHeaderView::section {{
    background-color: {t.surface_alt};
    color: {t.text};
    border: none;
    border-right: 1px solid {t.border};
    border-bottom: 1px solid {t.border};
    padding: 4px;
}}
QStatusBar {{
    background-color: {t.window};
    color: {t.text_muted};
}}
QFrame[surface="card"] {{
    background-color: {t.surface};
    border: 1px solid {t.border};
    border-radius: 6px;
}}
QLabel[tone="muted"] {{ color: {t.text_muted}; }}
QLabel[equipmentName="true"] {{ color: {t.text}; }}
QLabel[equipmentName="true"][quality="gold"] {{ color: {t.quality_gold}; }}
QLabel[equipmentName="true"][quality="purple"] {{ color: {t.quality_purple}; }}
QLabel[equipmentName="true"][quality="blue"] {{ color: {t.quality_blue}; }}
QLabel[equipmentName="true"][quality="green"] {{ color: {t.quality_green}; }}
QLabel[status="info"], QFrame[status="info"], QWidget[status="info"] {{
    color: {t.info}; background-color: {t.info_surface};
}}
QLabel[status="warning"], QFrame[status="warning"], QWidget[status="warning"] {{
    color: {t.warning}; background-color: {t.warning_surface};
}}
QLabel[status="danger"], QFrame[status="danger"], QWidget[status="danger"] {{
    color: {t.danger}; background-color: {t.danger_surface};
}}
QLabel[status="success"], QFrame[status="success"], QWidget[status="success"] {{
    color: {t.success}; background-color: {t.success_surface};
}}
QToolButton#themeToggleButton {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px 7px;
    font-size: 16px;
}}
QToolButton#themeToggleButton:hover {{ background-color: {t.surface_hover}; }}
"""


class ThemeManager(QObject):
    """Apply and broadcast application theme changes."""

    theme_changed = pyqtSignal(str)

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._theme: ThemeName = DEFAULT_THEME
        # Fusion consistently honours custom palettes on Windows/Linux/macOS.
        app.setStyle("Fusion")

    @property
    def current(self) -> ThemeName:
        return self._theme

    @property
    def tokens(self) -> ThemeTokens:
        return THEMES[self._theme]

    def apply(self, theme: object) -> ThemeName:
        app = QApplication.instance()
        if app is None:
            raise RuntimeError("Cannot apply a theme without QApplication")
        normalized = normalize_theme(theme)
        changed = normalized != self._theme
        self._theme = normalized
        app.setProperty("lvjiangTheme", normalized)
        app.setPalette(_palette(THEMES[normalized]))
        app.setStyleSheet(_stylesheet(THEMES[normalized]))
        if changed:
            self.theme_changed.emit(normalized)
        return normalized

    def toggle(self) -> ThemeName:
        return self.apply("dark" if self._theme == "light" else "light")


_manager: ThemeManager | None = None


def get_theme_manager(app: QApplication | None = None) -> ThemeManager:
    global _manager
    if _manager is None:
        app = app or QApplication.instance()  # type: ignore[assignment]
        if app is None:
            raise RuntimeError("ThemeManager requires a QApplication")
        _manager = ThemeManager(app)
    return _manager


def reset_theme_manager() -> None:
    """Drop the singleton reference (primarily for isolated tests)."""
    global _manager
    _manager = None
