"""设置页保存在线配置开关，不在当前进程安装。"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QPlainTextEdit

from lvjiang.ui import settings_dialog


def test_settings_actions_do_not_install_configuration(qtbot, monkeypatch):
    installed = []
    monkeypatch.setattr(
        "lvjiang.core.config.remote.apply_outcome",
        lambda *_args, **_kwargs: installed.append("apply"))
    monkeypatch.setattr(
        "lvjiang.core.config.remote.sync_now",
        lambda **_kwargs: installed.append("sync"))
    monkeypatch.setattr(
        "lvjiang.core.config.remote.prepare_startup_configuration",
        lambda: installed.append("prepare"))
    saved = []
    monkeypatch.setattr(
        "lvjiang.core.telemetry.settings.set_network_feature",
        lambda feature, checked: saved.append((feature, checked)))
    monkeypatch.setattr("lvjiang.core.license.clear_code", lambda: saved.append("clear"))
    monkeypatch.setattr(
        "lvjiang.core.license.refresh_entitlement", lambda: saved.append("refresh"))
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes)

    dialog = settings_dialog.SettingsDialog.__new__(settings_dialog.SettingsDialog)
    QDialog.__init__(dialog)
    qtbot.addWidget(dialog)
    dialog._license_edit = QPlainTextEdit(dialog)
    dialog._license_status = QLabel(dialog)
    dialog._refresh_license_status = lambda: None
    dialog._refresh_privacy_tab_state = lambda: None
    dialog._activation_message = ""

    dialog._on_offline_toggled(True)
    dialog._on_net_feature_toggled("remote_config", False)
    dialog._activation_completed(SimpleNamespace(active=True, reason=""))
    dialog._on_clear_license()

    assert installed == []
    assert ("offline", True) in saved
    assert ("remote_config", False) in saved
    assert "clear" in saved and "refresh" in saved
