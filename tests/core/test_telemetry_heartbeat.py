"""心跳 payload 组装与每日节流。"""
from __future__ import annotations

import pytest

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.telemetry import heartbeat


@pytest.fixture(autouse=True)
def isolated_session(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_PATH", tmp_path / "session.json")
    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    reset_session_store()
    yield
    reset_session_store()


class TestBuildPayload:
    def test_payload_validates_against_schema(self):
        payload = heartbeat.build_heartbeat_payload(
            install_id="0" * 32, first_seen="2026-01-01")
        heartbeat.HEARTBEAT_SCHEMA.validate(payload)  # 不抛即通过

    def test_unknown_version_falls_back_gracefully(self, monkeypatch):
        from lvjiang.core import update as update_mod
        monkeypatch.setattr(update_mod, "get_version", lambda: "not a valid version!!")
        payload = heartbeat.build_heartbeat_payload(
            install_id="0" * 32, first_seen="2026-01-01")
        assert payload["app_version"] == "unknown"

    def test_os_release_truncated_to_major(self, monkeypatch):
        import platform
        monkeypatch.setattr(platform, "release", lambda: "22631.4169.extra.build.info")
        payload = heartbeat.build_heartbeat_payload(
            install_id="0" * 32, first_seen="2026-01-01")
        assert payload["os_release"] == "22631"

    def test_run_env_falls_back_to_desktop_for_unknown_value(self, monkeypatch):
        from lvjiang.core.config import session as session_mod
        monkeypatch.setattr(session_mod, "load_env", lambda: "some_unexpected_env")
        payload = heartbeat.build_heartbeat_payload(
            install_id="0" * 32, first_seen="2026-01-01")
        assert payload["run_env"] == "desktop"


class TestThrottle:
    def test_should_send_when_never_sent(self):
        assert heartbeat.should_send_heartbeat() is True

    def test_should_not_send_twice_same_day(self):
        heartbeat.mark_attempt(success=True)
        assert heartbeat.should_send_heartbeat() is False

    def test_failed_attempt_does_not_block_same_day_retry_immediately(self):
        """失败的一天不能算已上报——但 1 小时内不重试（另有测试覆盖）。"""
        heartbeat.mark_attempt(success=False)
        assert "last_report_date" not in heartbeat._state()

    def test_failed_attempt_blocks_retry_within_an_hour(self):
        heartbeat.mark_attempt(success=False)
        assert heartbeat.should_send_heartbeat() is False

    def test_failed_attempt_allows_retry_after_an_hour(self, monkeypatch):
        from datetime import datetime, timedelta, timezone
        heartbeat.mark_attempt(success=False)
        two_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        from lvjiang.core.config.session import get_session_store

        def _merge(existing):
            existing = existing if isinstance(existing, dict) else {}
            node = dict(existing.get("telemetry") or {})
            node["last_attempt_at"] = two_hours_ago
            existing["telemetry"] = node
            return existing
        get_session_store().mutate_node("server_config", _merge)
        assert heartbeat.should_send_heartbeat() is True

    def test_mark_success_writes_todays_date(self):
        from datetime import date
        heartbeat.mark_attempt(success=True)
        assert heartbeat._state()["last_report_date"] == date.today().isoformat()

    def test_does_not_clobber_skip_version_or_announcement_state(self):
        """server_config 是共享节点，写 telemetry 子节点不能冲掉
        update.py 的 skip_version 与 announcement.py 的既有状态。"""
        from lvjiang.core.config.session import get_session_store
        get_session_store().mutate_node(
            "server_config", lambda e: {**(e or {}), "skip_version": "9.9.9"})
        heartbeat.mark_attempt(success=True)
        server = get_session_store().get_node("server_config")
        assert server["skip_version"] == "9.9.9"
        assert "telemetry" in server
