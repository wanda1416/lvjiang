"""Light/dark theme infrastructure tests."""
from __future__ import annotations

from PyQt6.QtGui import QColor, QPalette

from lvjiang.core.config.models import UserConfig
from lvjiang.ui.main.window import MainWindow
from lvjiang.ui.theme import (
    DARK,
    LIGHT,
    ThemeManager,
    get_theme_manager,
    normalize_theme,
)


def test_normalize_theme_falls_back_for_old_or_invalid_config():
    assert normalize_theme("light") == "light"
    assert normalize_theme("dark") == "dark"
    assert normalize_theme("sepia") == "light"
    assert normalize_theme(None) == "light"


def test_user_config_rejects_unsupported_theme():
    assert UserConfig(theme="dark").theme == "dark"
    assert UserConfig(theme="unknown").theme == "light"


def test_apply_updates_palette_stylesheet_and_signal(qapp):
    manager = ThemeManager(qapp)
    seen: list[str] = []
    manager.theme_changed.connect(seen.append)

    assert manager.apply("dark") == "dark"
    assert manager.current == "dark"
    assert qapp.property("lvjiangTheme") == "dark"
    assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(DARK.window)
    assert DARK.surface_hover in qapp.styleSheet()
    assert seen == ["dark"]

    # Applying the same theme is idempotent and does not emit a false change.
    manager.apply("dark")
    assert seen == ["dark"]

    assert manager.toggle() == "light"
    assert qapp.palette().color(QPalette.ColorRole.Window) == QColor(LIGHT.window)
    assert seen == ["dark", "light"]


def test_dark_palette_has_readable_text_contrast(qapp):
    manager = ThemeManager(qapp)
    manager.apply("dark")
    palette = qapp.palette()
    assert palette.color(QPalette.ColorRole.Text) == QColor(DARK.text)
    assert palette.color(QPalette.ColorRole.Base) == QColor(DARK.surface)
    assert palette.color(QPalette.ColorRole.Text) != palette.color(
        QPalette.ColorRole.Base
    )
    manager.apply("light")


def test_main_window_toggle_applies_and_persists(qapp, monkeypatch):
    manager = get_theme_manager(qapp)
    manager.apply("light")
    saved: list[dict[str, str]] = []
    monkeypatch.setattr("lvjiang.core.config.save_settings", saved.append)

    MainWindow._toggle_theme(object())

    assert manager.current == "dark"
    assert saved == [{"theme": "dark"}]
    manager.apply("light")
