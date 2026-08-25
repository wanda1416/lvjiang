"""同意状态机 + 三条网络行为的统一开关。

红线用例见 TestNoRequestWhenDisabled——统计关闭/未同意/离线模式/dev
构建/今天已报 等状态下，绝不能发起网络请求，也绝不能攒本地数据。
"""
from __future__ import annotations

import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import consent, settings
from lvjiang.core.telemetry import identity as identity_mod


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    monkeypatch.delenv("LVJIANG_OFFLINE", raising=False)
    reset_session_store()
    yield
    reset_session_store()


@pytest.fixture
def dev_mode_off(monkeypatch):
    """consent.needs_prompt() 依赖 resolver.is_dev_mode()，而 get_resolver()
    是进程级单例，一旦在本次 pytest 进程里被任何用例构造过就不会重新计算，
    env var 在这里改不动它。直接打桩 consent._is_dev_build，测的是
    needs_prompt() 自己的逻辑，不依赖 resolver 的缓存状态。"""
    monkeypatch.setattr("lvjiang.core.telemetry.consent._is_dev_build", lambda: False)
    yield


class TestConsentState:
    def test_unknown_before_any_choice(self, dev_mode_off):
        assert consent.get_consent_state() is consent.ConsentState.UNKNOWN
        assert consent.needs_prompt() is True

    def test_grant_records_state_and_generates_identity(self, dev_mode_off):
        consent.record_consent_choice(True)
        assert consent.get_consent_state() is consent.ConsentState.GRANTED
        assert consent.needs_prompt() is False
        assert identity_mod.identity_path().exists()

    def test_deny_records_state_and_no_identity(self, dev_mode_off):
        consent.record_consent_choice(False)
        assert consent.get_consent_state() is consent.ConsentState.DENIED
        assert consent.needs_prompt() is False
        assert not identity_mod.identity_path().exists()

    def test_never_reprompts_after_either_choice(self, dev_mode_off):
        consent.record_consent_choice(False)
        assert consent.needs_prompt() is False
        consent.record_consent_choice(True)  # 设置里改主意，同样不算"重新弹窗"
        assert consent.needs_prompt() is False

    def test_dev_build_never_prompts(self, monkeypatch):
        monkeypatch.setattr("lvjiang.core.telemetry.consent._is_dev_build", lambda: True)
        assert consent.needs_prompt() is False


class TestSettingsToggleFollowsChoice(object):
    def test_grant_enables_telemetry_feature(self, dev_mode_off):
        consent.record_consent_choice(True)
        assert consent.is_network_feature_enabled(consent.NetFeature.TELEMETRY) is True

    def test_deny_disables_telemetry_feature(self, dev_mode_off):
        consent.record_consent_choice(False)
        assert consent.is_network_feature_enabled(consent.NetFeature.TELEMETRY) is False

    def test_settings_can_reenable_after_denial_without_reprompt(self, dev_mode_off):
        consent.record_consent_choice(False)
        settings.set_telemetry_enabled(True)
        assert consent.is_network_feature_enabled(consent.NetFeature.TELEMETRY) is True
        assert consent.needs_prompt() is False  # 依然不重新弹


class TestOfflineModeOverridesEverything:
    def test_offline_disables_all_three(self, dev_mode_off):
        consent.record_consent_choice(True)
        settings.set_network_feature("offline", True)
        assert consent.is_network_feature_enabled(consent.NetFeature.TELEMETRY) is False
        assert consent.is_network_feature_enabled(consent.NetFeature.ANNOUNCEMENT) is False
        assert consent.is_network_feature_enabled(consent.NetFeature.UPDATE) is False

    def test_env_var_override(self, dev_mode_off, monkeypatch):
        consent.record_consent_choice(True)
        monkeypatch.setenv("LVJIANG_OFFLINE", "1")
        assert consent.is_network_feature_enabled(consent.NetFeature.TELEMETRY) is False


class TestSetTelemetryEnabledPurges:
    def test_disable_purges_local_data(self, dev_mode_off):
        from lvjiang.core.telemetry import spool as spool_mod
        from lvjiang.core.telemetry.schema import EventSchema, FieldSpec

        consent.record_consent_choice(True)
        schema = EventSchema(name="t", version=1, fields=(FieldSpec("x", str, choices=("a",)),))
        spool_mod.append(schema.validate({"x": "a"}))
        spool_mod.flush()
        assert spool_mod.take_batches(10)

        settings.set_telemetry_enabled(False)
        assert spool_mod.take_batches(10) == []
        assert not identity_mod.identity_path().exists()

    def test_enable_generates_identity(self, dev_mode_off):
        settings.set_telemetry_enabled(True)
        assert identity_mod.identity_path().exists()
