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


class TestTelemetryCaptionComesFromPlugins:
    """收集说明必须由插件经 AppHooks.telemetry_disclosures 提供。

    原先这段文案写死在通用设置页里（"改进内置调律规则、不含装备名称或
    完整词条组合"），那是燕云的说法，换个插件就是错的；而且它和同意框
    各写一份，两处描述会各说各的。现在两边读同一份声明。
    """

    def test_uses_registered_disclosure(self, monkeypatch):
        from dataclasses import dataclass

        import lvjiang.apps as apps_mod

        @dataclass(frozen=True)
        class _Disclosure:
            title: str = "示例用途"
            purpose: str = "用于改进示例规则"
            collected: tuple = ()
            excluded: tuple = ("账号", "截图")

        monkeypatch.setattr(
            apps_mod, "get_registry",
            lambda: {"telemetry_disclosures": (_Disclosure(),)})
        caption = SettingsDialog._telemetry_caption()
        assert "用于改进示例规则" in caption
        assert "账号、截图" in caption

    def test_falls_back_to_neutral_text_without_plugins(self, monkeypatch):
        """没有插件时不能空着，也不能冒出任何游戏词汇。"""
        import lvjiang.apps as apps_mod
        monkeypatch.setattr(apps_mod, "get_registry", lambda: {})
        caption = SettingsDialog._telemetry_caption()
        assert caption.strip()
        for word in ("调律", "装备", "词条", "角色名"):
            assert word not in caption
