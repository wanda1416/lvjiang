"""Online activation runs off the UI thread, freezes input and protects thread lifetime."""
from threading import Event
from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QPushButton

from lvjiang.core import license
from lvjiang.ui import settings_dialog


def test_activation_is_nonblocking_and_dialog_waits_for_worker(qtbot, monkeypatch):
    # Explicit minimal UI fixture: do not load real application settings or databases.
    dialog = settings_dialog.SettingsDialog.__new__(settings_dialog.SettingsDialog)
    QDialog.__init__(dialog)
    qtbot.addWidget(dialog)
    dialog._license_edit = QPlainTextEdit(dialog)
    dialog._license_status = QLabel(dialog)
    dialog._activate_btn = QPushButton(dialog)
    dialog._clear_license_btn = QPushButton(dialog)
    dialog._refresh_license_status = lambda: dialog._license_status.setText("original entitlement")
    issued = license.License(1, "ui-test", "claim", None, ("lv1",), days=1)
    monkeypatch.setattr(license, "verify_code", lambda code: issued)
    monkeypatch.setattr(settings_dialog, "load_user_config",
                        lambda: SimpleNamespace(network=SimpleNamespace(offline=False)))
    started, release = Event(), Event()
    captured = []
    def activate(code):
        captured.append(code)
        started.set()
        assert release.wait(5)
        return license.Entitlement(reason="test unavailable")
    monkeypatch.setattr(license, "try_activate", activate)
    dialog._license_edit.setPlainText("frozen-test-input")
    dialog.show()
    try:
        dialog._on_activate_license()
        qtbot.waitUntil(started.is_set)
        assert dialog._activation_busy and not dialog._activate_btn.isEnabled()
        dialog._license_edit.setPlainText("changed-after-start")
        dialog.done(0)
        assert dialog.isVisible()  # Closing cannot destroy a running QThread.
        release.set()
        qtbot.waitUntil(lambda: not dialog._activation_busy)
        assert captured == ["frozen-test-input"]
        assert dialog._activate_btn.isEnabled()
        assert "test unavailable" in dialog._license_status.text()
    finally:
        release.set()
        if getattr(dialog, "_activation_busy", False):
            qtbot.waitUntil(lambda: not dialog._activation_busy)
