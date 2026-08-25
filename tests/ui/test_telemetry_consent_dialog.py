"""首启同意弹窗：视觉等权、示例数据真实性、选择落地。"""
from __future__ import annotations

import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.ui.telemetry_consent_dialog import (
    TelemetryConsentDialog,
    maybe_prompt_and_record,
)


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "config" / "session" / "session.json")
    monkeypatch.setattr("lvjiang.core.telemetry.consent._is_dev_build", lambda: False)
    reset_session_store()
    yield
    reset_session_store()


class TestButtonsEqualWeight:
    def test_same_style_variant(self, qtbot):
        dlg = TelemetryConsentDialog()
        qtbot.addWidget(dlg)
        assert dlg._btn_agree.styleSheet() == dlg._btn_decline.styleSheet()


class TestChoiceResult:
    def test_agree_sets_granted(self, qtbot):
        dlg = TelemetryConsentDialog()
        qtbot.addWidget(dlg)
        dlg._on_agree()
        assert dlg.granted() is True

    def test_decline_sets_not_granted(self, qtbot):
        dlg = TelemetryConsentDialog()
        qtbot.addWidget(dlg)
        dlg._on_decline()
        assert dlg.granted() is False

    def test_default_before_any_click_is_declined(self, qtbot):
        """右上角 X 关闭（不点任何按钮）等价于不同意，不产生半同意态。"""
        dlg = TelemetryConsentDialog()
        qtbot.addWidget(dlg)
        assert dlg.granted() is False


class TestExampleDataIsReal:
    def test_shown_example_is_schema_generated_not_hardcoded(self, qtbot):
        from lvjiang.core.telemetry.heartbeat import HEARTBEAT_SCHEMA
        dlg = TelemetryConsentDialog()
        qtbot.addWidget(dlg)
        text = dlg._example_payload_text()
        for key in HEARTBEAT_SCHEMA.field_names():
            assert key in text


class TestMaybePromptAndRecord:
    def test_skips_when_already_answered(self, qtbot, monkeypatch):
        from lvjiang.core.telemetry import consent
        consent.record_consent_choice(True)

        def _boom(*a, **k):
            raise AssertionError("已经问过，不应该再弹窗")
        monkeypatch.setattr(
            "lvjiang.ui.telemetry_consent_dialog.TelemetryConsentDialog", _boom)
        maybe_prompt_and_record(None)  # 不抛即通过

    def test_records_choice_on_first_prompt(self, qtbot, monkeypatch):
        from lvjiang.core.telemetry import consent

        class _AutoAgree(TelemetryConsentDialog):
            def exec(self):
                self._granted = True
                return 1

        monkeypatch.setattr(
            "lvjiang.ui.telemetry_consent_dialog.TelemetryConsentDialog", _AutoAgree)
        maybe_prompt_and_record(None)
        assert consent.get_consent_state() is consent.ConsentState.GRANTED
        assert consent.needs_prompt() is False
