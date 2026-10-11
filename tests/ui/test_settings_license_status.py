"""本地激活不能冒充在线授权；换码不得继承上一张码的检查结果。"""

from PyQt6.QtWidgets import QVBoxLayout

from lvjiang.core import license
from lvjiang.core.config import UserConfig, protected_remote
from lvjiang.ui import settings_dialog


def test_local_activation_and_cloud_denial_are_shown_separately(qtbot, monkeypatch):
    access = protected_remote.ProtectedAccess("https://example.invalid", "test-only-license")
    issued = license.License(1, "test-license", "none", None, ("lv1",))
    entitlement = license.Entitlement(license=issued, features=frozenset({"lv1"}))
    monkeypatch.setattr(license, "current_serial", lambda: None)
    monkeypatch.setattr(license, "current_entitlement", lambda: entitlement)
    monkeypatch.setattr(license, "load_code", lambda: access.code)
    monkeypatch.setattr(protected_remote, "_access_status", protected_remote.MemberAccessStatus())
    monkeypatch.setattr(settings_dialog, "load_user_config", UserConfig)
    # 构造真实激活页，隔离与本契约无关的其它设置页和配置读取。
    def setup(dialog):
        QVBoxLayout(dialog).addWidget(dialog._build_license_tab())
    monkeypatch.setattr(settings_dialog.SettingsDialog, "_setup_ui", setup)
    monkeypatch.setattr(settings_dialog.SettingsDialog, "_connect_dirty_signals", lambda self: None)
    dialog = settings_dialog.SettingsDialog()
    qtbot.addWidget(dialog)
    assert "本地激活：已激活" in dialog._license_status.text()
    assert "尚未检查" in dialog._online_license_status.text()
    protected_remote.report_access_status(access, "denied", "激活码已在云端停用")
    dialog._license_status_timer.timeout.emit()
    assert "云端停用" in dialog._online_license_status.text()
    assert "本地激活：已激活" in dialog._license_status.text()
    monkeypatch.setattr(license, "load_code", lambda: "another-test-only-license")
    dialog._license_status_timer.timeout.emit()
    assert "尚未检查" in dialog._online_license_status.text()
