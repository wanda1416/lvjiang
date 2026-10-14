"""网络分项只限制自动检查；离线总闸在真正请求前生效。"""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lvjiang.core import announcement, config, update
from lvjiang.core.config.models import NetworkConfig
from lvjiang.core.telemetry import consent
from lvjiang.ui.main.startup_ops import StartupOpsMixin


@pytest.mark.parametrize("announcement_enabled,update_enabled,offline,expected", [
    (False, True, False, ["update", "continue"]),
    (True, False, False, ["announcement", "continue"]),
    (True, True, True, ["continue"]),
])
def test_startup_skips_disabled_checks_and_continues_chain(
    monkeypatch, announcement_enabled, update_enabled, offline, expected,
):
    monkeypatch.delenv("LVJIANG_OFFLINE", raising=False)
    network = NetworkConfig(announcement=announcement_enabled, update=update_enabled, offline=offline)
    monkeypatch.setattr(config, "load_user_config", lambda: SimpleNamespace(network=network))
    events = []

    class Signal:
        def connect(self, callback):
            self.callback = callback

    def checker(kind):
        class Checker:
            def __init__(self, parent):
                self.finished, self.error = Signal(), Signal()

            def start(self):
                events.append(kind)
                self.error.callback("测试网络失败")
        return Checker

    monkeypatch.setattr("lvjiang.ui.notices.announcement_checker.AnnouncementChecker", checker("announcement"))
    monkeypatch.setattr("lvjiang.ui.background.update.UpdateChecker", checker("update"))

    class Host(StartupOpsMixin):
        def _continue_after_update_check(self):
            events.append("continue")

    Host().check_update_on_startup()
    assert events == expected


def test_manual_checks_ignore_startup_switches_but_honor_both_offline_sources(monkeypatch):
    monkeypatch.delenv("LVJIANG_OFFLINE", raising=False)
    network = NetworkConfig(announcement=False, update=False)
    monkeypatch.setattr(config, "load_user_config", lambda: SimpleNamespace(network=network))

    def response(data):
        stream = io.BytesIO(json.dumps(data).encode("utf-8"))
        stream.headers = {}
        return stream

    notices = Mock(side_effect=lambda *a, **kw: response({"schema_version": 1, "notice_version": 1, "notices": []}))
    releases = Mock(side_effect=lambda *a, **kw: response({"tag_name": "0.15.0"}))
    monkeypatch.setattr(announcement, "urlopen", notices)
    monkeypatch.setattr(update, "urlopen", releases)
    assert announcement.fetch_announcement_manifest().manifest.notice_version == 1
    assert update.fetch_latest_release().version == "0.15.0"
    notices.reset_mock()
    releases.reset_mock()
    network.offline = True
    with pytest.raises(announcement.AnnouncementNetworkError, match="离线"):
        announcement.fetch_announcement_manifest()
    with pytest.raises(update.UpdateCheckError, match="离线"):
        update.fetch_latest_release()
    network.offline = False
    monkeypatch.setenv("LVJIANG_OFFLINE", "1")
    assert not consent.is_network_feature_enabled(consent.NetFeature.ANNOUNCEMENT)
    with pytest.raises(announcement.AnnouncementNetworkError, match="离线"):
        announcement.fetch_announcement_manifest()
    with pytest.raises(update.UpdateCheckError, match="离线"):
        update.fetch_latest_release()
    notices.assert_not_called()
    releases.assert_not_called()


def test_offline_announcement_dialog_keeps_cache_and_disables_refresh(qapp, monkeypatch):
    from lvjiang.ui.notices.announcement_dialog import AnnouncementDialog

    network = NetworkConfig(offline=True)
    monkeypatch.setattr(config, "load_user_config", lambda: SimpleNamespace(network=network))
    fetch = Mock()
    monkeypatch.setattr(announcement, "fetch_announcement_manifest", fetch)
    cached = announcement.parse_announcement_manifest({"schema_version": 1, "notice_version": 2, "notices": []})
    dialog = AnnouncementDialog(cached)
    try:
        assert not dialog._refresh_btn.isEnabled()
        assert "离线" in dialog._refresh_btn.toolTip()
        dialog.refresh()
        assert dialog.manifest is cached
        assert dialog._checker is None
        fetch.assert_not_called()
    finally:
        dialog.close()
        dialog.deleteLater()
