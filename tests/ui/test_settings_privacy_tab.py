"""设置对话框「网络与隐私」Tab：切换即时生效（不经保存按钮）、
离线模式联动置灰、开关变更接入 core.telemetry.settings。
"""
from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QPushButton

from lvjiang.core.config.models import UserConfig
from lvjiang.core.config.session import reset_session_store
from lvjiang.ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    reset_session_store()
    # 与既有 test_android_method_settings.py 同款打桩：避免真的写 app.yaml
    monkeypatch.setattr("lvjiang.ui.settings_dialog.save_app_config", lambda *a: None)
    yield
    reset_session_store()


def _make_dialog(qtbot):
    dlg = SettingsDialog()
    qtbot.addWidget(dlg)
    return dlg


class TestInitialState:
    def test_defaults_match_network_config_defaults(self, qtbot):
        dlg = _make_dialog(qtbot)
        defaults = UserConfig().network
        assert dlg._offline_check.isChecked() == defaults.offline
        assert dlg._announcement_check.isChecked() == defaults.announcement
        assert dlg._update_check.isChecked() == defaults.update
        assert dlg._telemetry_check.isChecked() == defaults.telemetry  # 默认关

    def test_save_button_only_hidden_on_immediate_apply_tab(self, qtbot):
        dlg = _make_dialog(qtbot)
        assert not dlg._save_btn.isHidden()

        dlg._tabs.setCurrentIndex(dlg._privacy_tab_index)
        assert dlg._save_btn.isHidden()

        for index in range(dlg._tabs.count() - 1):
            dlg._tabs.setCurrentIndex(index)
            assert not dlg._save_btn.isHidden()

    def test_switching_to_privacy_preserves_other_tab_dirty_state(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._title_edit.setText("新的窗口标题")
        assert dlg._save_btn.isEnabled()

        dlg._tabs.setCurrentIndex(dlg._privacy_tab_index)
        assert dlg._save_btn.isHidden()

        dlg._tabs.setCurrentIndex(0)
        assert not dlg._save_btn.isHidden()
        assert dlg._save_btn.isEnabled()


class TestTogglesApplyImmediately:
    """不经"保存"按钮：这类有副作用（尤其是清空本地缓冲）的开关不该被
    延后到用户可能永远不会点的"保存"按钮之后。"""

    def test_telemetry_toggle_writes_settings_immediately_without_save_click(self, qtbot):
        dlg = _make_dialog(qtbot)
        assert not dlg._save_btn.isEnabled()  # 确认没有"忘了保存也生效"的误解
        dlg._telemetry_check.setChecked(True)

        from lvjiang.core.config import load_user_config
        assert load_user_config().network.telemetry is True

    def test_offline_toggle_persists(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._offline_check.setChecked(True)
        from lvjiang.core.config import load_user_config
        assert load_user_config().network.offline is True

    def test_announcement_toggle_persists(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._announcement_check.setChecked(False)
        from lvjiang.core.config import load_user_config
        assert load_user_config().network.announcement is False


class TestOfflineModeGreysOutSubOptions:
    def test_checking_offline_shows_effective_disabled_state(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)
        dlg._offline_check.setChecked(True)
        assert not dlg._telemetry_check.isEnabled()
        assert not dlg._announcement_check.isEnabled()
        assert not dlg._update_check.isEnabled()
        assert not dlg._telemetry_check.isChecked()
        assert not dlg._announcement_check.isChecked()
        assert not dlg._update_check.isChecked()
        assert "已暂停" in dlg._offline_hint.text()

        from lvjiang.core.config import load_user_config
        network = load_user_config().network
        assert network.telemetry is True
        assert network.announcement is True
        assert network.update is True

    def test_unchecking_offline_restores_without_clearing_values(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)
        dlg._offline_check.setChecked(True)
        dlg._offline_check.setChecked(False)
        assert dlg._telemetry_check.isEnabled()
        assert dlg._telemetry_check.isChecked() is True  # 值没被清空
        assert "恢复之前的选择" not in dlg._offline_hint.text()

    def test_local_privacy_actions_remain_available_offline(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)
        dlg._offline_check.setChecked(True)
        buttons = {
            button.text(): button
            for button in dlg.findChildren(QPushButton)
        }
        assert buttons["重置标识"].isEnabled()
        assert buttons["查看待上报数据"].isEnabled()


class TestDisablingTelemetryPurgesLocalData:
    def test_unchecking_clears_pending_spool(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)

        from lvjiang.core.telemetry import spool as spool_mod
        from lvjiang.core.telemetry.schema import EventSchema, FieldSpec
        schema = EventSchema(name="t", version=1, fields=(FieldSpec("x", str, choices=("a",)),))
        spool_mod.append(schema.validate({"x": "a"}))
        spool_mod.flush()
        assert spool_mod.take_batches(10)

        dlg._telemetry_check.setChecked(False)
        assert spool_mod.take_batches(10) == []


class TestIdLabelReflectsState:
    def test_shows_placeholder_when_disabled(self, qtbot):
        dlg = _make_dialog(qtbot)
        assert "未生成" in dlg._telemetry_id_label.text()

    def test_shows_complete_id_when_enabled(self, qtbot):
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)
        from lvjiang.core.telemetry.identity import get_identity
        install_id = get_identity().install_id
        assert dlg._telemetry_id_label.text() == f"当前标识：{install_id}"
        assert "…" not in dlg._telemetry_id_label.text()


class TestResetIdButtonRequiresConfirmation:
    def test_declining_confirmation_keeps_old_id(self, qtbot, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)
        from lvjiang.core.telemetry.identity import get_identity
        old_id = get_identity().install_id

        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.No)
        dlg._on_reset_telemetry_id()
        assert get_identity().install_id == old_id

    def test_confirming_generates_new_id(self, qtbot, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox
        dlg = _make_dialog(qtbot)
        dlg._telemetry_check.setChecked(True)
        from lvjiang.core.telemetry.identity import get_identity
        old_id = get_identity().install_id

        monkeypatch.setattr(QMessageBox, "question",
                            lambda *a, **k: QMessageBox.StandardButton.Yes)
        dlg._on_reset_telemetry_id()
        new_id = get_identity().install_id
        assert new_id != old_id
        assert dlg._telemetry_id_label.text() == f"当前标识：{new_id}"
