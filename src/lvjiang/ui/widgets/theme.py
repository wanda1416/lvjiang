"""Application-wide light and dark theme support.

The theme layer owns neutral UI colours.  Business colours (equipment quality,
success/warning/error states) may still be supplied by individual widgets, but
neutral backgrounds, borders, and text should come from the Qt palette or the
semantic dynamic properties styled here.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Literal, cast

from loguru import logger
from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication, QWidget

ThemeName = Literal["light", "paper", "dark"]
DEFAULT_THEME: ThemeName = "light"
THEME_ORDER: tuple[ThemeName, ...] = ("light", "paper", "dark")


@dataclass(frozen=True)
class ThemeTokens:
    is_dark: bool
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
    selection_bg: str
    selection_text: str
    danger: str
    danger_surface: str
    warning: str
    warning_surface: str
    info: str
    info_surface: str
    success: str
    success_surface: str


LIGHT = ThemeTokens(
    is_dark=False,
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
    selection_bg="#0078d4",
    selection_text="#ffffff",
    danger="#c62828",
    danger_surface="#ffebee",
    warning="#9a5a00",
    warning_surface="#fff3e0",
    info="#1557a0",
    info_surface="#e3f2fd",
    success="#247a3b",
    success_surface="#e8f5e9",
)

PAPER = ThemeTokens(
    is_dark=False,
    window="#f5f3ee",
    surface="#ffffff",
    surface_alt="#efece5",
    surface_hover="#e6e1d6",
    surface_pressed="#dad3c5",
    border="#dcd6ca",
    border_strong="#bdb5a6",
    text="#1c1b18",
    text_muted="#635f56",
    text_disabled="#9e988d",
    accent="#b45309",
    accent_hover="#9a4507",
    accent_pressed="#7c3705",
    selection_bg="#b45309",
    selection_text="#ffffff",
    danger="#be123c",
    danger_surface="#fce7ec",
    warning="#b45309",
    warning_surface="#fef3e2",
    info="#0369a1",
    info_surface="#e6f4fb",
    success="#15803d",
    success_surface="#e7f6ec",
)

DARK = ThemeTokens(
    is_dark=True,
    window="#141418",
    surface="#1c1c22",
    surface_alt="#24242c",
    surface_hover="#2d2d37",
    surface_pressed="#363642",
    border="#32323d",
    border_strong="#4d4d5c",
    text="#edeae2",
    text_muted="#9b968c",
    text_disabled="#66635c",
    accent="#e5a93b",
    accent_hover="#f0be5e",
    accent_pressed="#c98f24",
    selection_bg="#7a4c12",
    selection_text="#f5f2ea",
    danger="#f87171",
    danger_surface="#3b1c22",
    warning="#fb923c",
    warning_surface="#3a2515",
    info="#38bdf8",
    info_surface="#162d3d",
    success="#34d399",
    success_surface="#153226",
)

THEMES: dict[ThemeName, ThemeTokens] = {
    "light": LIGHT,
    "paper": PAPER,
    "dark": DARK,
}


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
        QPalette.ColorRole.Highlight: tokens.selection_bg,
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
    selection-background-color: {t.selection_bg};
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
    selection-background-color: {t.selection_bg};
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
QPushButton[btnVariant="action"] {{
    background-color: transparent;
    border: 1px solid {t.accent};
    color: {t.accent};
    border-radius: 5px;
    padding: 5px 11px;
    font-weight: 600;
}}
QPushButton[btnVariant="action"]:hover {{
    background-color: {t.surface_hover};
}}
QPushButton[btnVariant="action"]:pressed, QPushButton[btnVariant="action"]:checked {{
    background-color: {t.surface_pressed};
}}
QPushButton[btnVariant="action"]:disabled {{
    color: {t.text_disabled};
    border-color: {t.border};
}}
QPushButton[btnVariant="neutral"] {{
    background-color: {t.surface_alt};
    color: {t.text};
    border: 1px solid {t.border_strong};
    border-radius: 5px;
    padding: 5px 11px;
    font-weight: 600;
}}
QPushButton[btnVariant="neutral"]:hover {{
    background-color: {t.surface_hover};
}}
QPushButton[btnVariant="neutral"]:pressed, QPushButton[btnVariant="neutral"]:checked {{
    background-color: {t.surface_pressed};
}}
QPushButton[btnVariant="neutral"]:disabled {{
    color: {t.text_disabled};
    border-color: {t.border};
}}
QPushButton[btnVariant="danger"] {{
    background-color: {t.surface_alt};
    color: {t.danger};
    border: 1px solid {t.danger};
    border-radius: 5px;
    padding: 5px 11px;
    font-weight: 600;
}}
QPushButton[btnVariant="danger"]:hover {{
    background-color: {t.danger_surface};
}}
QPushButton[btnVariant="danger"]:pressed {{
    background-color: {t.danger};
    color: {t.selection_text};
}}
QPushButton[btnVariant="danger"]:disabled {{
    color: {t.text_disabled};
    border-color: {t.border};
}}
QPushButton[btnDensity="compact"] {{
    border-radius: 4px;
    padding: 2px 7px;
}}
/* 上面的 QWidget 规则给所有控件写死了 color，它会盖掉调色板里的 Disabled 分组，
   于是禁用的勾选框、单选和说明文字仍是常态色——看起来像「点不到」而不是「不可用」。
   按钮早就单独补过 :disabled，这里把其余带文字的控件一并补上。 */
QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled,
QGroupBox:disabled, QGroupBox::title:disabled {{
    color: {t.text_disabled};
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
    background-color: {t.selection_bg};
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
    background-color: {t.selection_bg};
    color: {t.selection_text};
}}
QMenu::item:disabled {{
    background-color: transparent;
    color: {t.text_disabled};
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
    padding: 2px 8px;
    font-size: 13px;
}}
QToolButton#themeToggleButton::menu-indicator {{
    image: none;
    width: 0px;
}}
QToolButton#themeToggleButton:hover {{ background-color: {t.surface_hover}; }}
"""


def _extension_stylesheet(tokens: ThemeTokens) -> str:
    """渲染已注册 app 的 QSS；单个扩展失败不影响基础主题。"""

    from ...core.plugins import get_registry

    chunks: list[str] = []
    for builder in get_registry().get("theme_stylesheet_builders", ()):
        try:
            chunk = builder(tokens)
            if chunk:
                chunks.append(str(chunk))
        except Exception:  # noqa: BLE001
            logger.exception("app 主题扩展渲染失败")
    return "\n".join(chunks)


_DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36


def _colorref(hex_color: str) -> int:
    """将 #RRGGBB 转换为 Windows GDI/DWM 使用的 0x00BBGGRR COLORREF。"""
    color = QColor(hex_color)
    return (color.blue() << 16) | (color.green() << 8) | color.red()


def _apply_win32_titlebar(widget: QWidget, tokens: ThemeTokens) -> None:
    """同步 Windows 原生顶层窗口标题栏配色（Win11 精确着色，Win10 同步深浅）。"""
    if sys.platform != "win32":
        return
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    if app.platformName() in {"offscreen", "minimal"}:
        return
    if not isinstance(widget, QWidget) or not widget.isWindow():
        return
    flags = widget.windowFlags()
    if flags & Qt.WindowType.FramelessWindowHint:
        return
    if (flags & Qt.WindowType.WindowType_Mask) in (
        Qt.WindowType.Popup,
        Qt.WindowType.ToolTip,
    ):
        return
    if not widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        return

    try:
        hwnd = int(widget.winId())
        if not hwnd:
            return
        dwmapi = ctypes.windll.dwmapi
        hwnd_ptr = ctypes.c_void_p(hwnd)
        dark = ctypes.c_int(1 if tokens.is_dark else 0)
        hr = dwmapi.DwmSetWindowAttribute(
            hwnd_ptr,
            ctypes.c_uint(_DWMWA_USE_IMMERSIVE_DARK_MODE),
            ctypes.byref(dark),
            ctypes.c_uint(ctypes.sizeof(dark)),
        )
        if hr != 0:
            dwmapi.DwmSetWindowAttribute(
                hwnd_ptr,
                ctypes.c_uint(_DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1),
                ctypes.byref(dark),
                ctypes.c_uint(ctypes.sizeof(dark)),
            )
        caption = ctypes.c_uint32(_colorref(tokens.window))
        dwmapi.DwmSetWindowAttribute(
            hwnd_ptr,
            ctypes.c_uint(_DWMWA_CAPTION_COLOR),
            ctypes.byref(caption),
            ctypes.c_uint(ctypes.sizeof(caption)),
        )
        text_color = ctypes.c_uint32(_colorref(tokens.text))
        dwmapi.DwmSetWindowAttribute(
            hwnd_ptr,
            ctypes.c_uint(_DWMWA_TEXT_COLOR),
            ctypes.byref(text_color),
            ctypes.c_uint(ctypes.sizeof(text_color)),
        )
    except Exception:  # noqa: BLE001 - 非关键装饰，绝不阻塞窗口显示
        pass


class ThemeManager(QObject):
    """Apply and broadcast application theme changes."""

    theme_changed = pyqtSignal(str)

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._theme: ThemeName = DEFAULT_THEME
        # Fusion consistently honours custom palettes on Windows/Linux/macOS.
        app.setStyle("Fusion")
        app.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if (
            event is not None
            and event.type() in (QEvent.Type.Show, QEvent.Type.WinIdChange)
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            _apply_win32_titlebar(watched, self.tokens)
        return super().eventFilter(watched, event)

    @property
    def current(self) -> ThemeName:
        return self._theme

    @property
    def tokens(self) -> ThemeTokens:
        return THEMES[self._theme]

    def apply(self, theme: object) -> ThemeName:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            raise RuntimeError("Cannot apply a theme without QApplication")
        normalized = normalize_theme(theme)
        changed = normalized != self._theme
        self._theme = normalized
        app.setProperty("lvjiangTheme", normalized)
        app.setPalette(_palette(THEMES[normalized]))
        tokens = THEMES[normalized]
        app.setStyleSheet(_stylesheet(tokens) + _extension_stylesheet(tokens))
        for widget in app.topLevelWidgets():
            _apply_win32_titlebar(widget, tokens)
        if changed:
            self.theme_changed.emit(normalized)
        return normalized

    def toggle(self) -> ThemeName:
        index = THEME_ORDER.index(self._theme)
        return self.apply(THEME_ORDER[(index + 1) % len(THEME_ORDER)])


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
    if _manager is not None:
        app = QApplication.instance()
        if app is not None and not sip.isdeleted(app) and not sip.isdeleted(_manager):
            app.removeEventFilter(_manager)
        _manager = None
