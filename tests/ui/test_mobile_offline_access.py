"""Lv1 仅限制配置下发，不影响状态查看及已有任务的安全控制。"""
from types import SimpleNamespace
from unittest.mock import Mock

from lvjiang.core.android import offline as transfer
from lvjiang.ui.mobile import offline


def test_sync_is_disabled_but_existing_task_controls_remain_available(qapp, monkeypatch):
    monkeypatch.setattr(offline, "has_feature", lambda _level: False)
    monkeypatch.setattr(transfer, "has_feature", lambda _level: False)
    monkeypatch.setattr(offline, "load_layout_entries", lambda: {"android": SimpleNamespace(name="Android")})
    manager = SimpleNamespace(list_users=lambda: ["tester"], get_active_user_name=lambda: "tester")
    page = offline.OfflineControlPage(SimpleNamespace(user_manager=manager), lambda: "", lambda _: None)
    try:
        assert not page.buttons["sync"].isEnabled()
        assert "Lv1" in page.buttons["sync"].toolTip()
        assert page.buttons["status"].isEnabled()
        assert page.buttons["diagnostics"].isEnabled()
        page._run("sync")
        assert "Lv1" in page.report.toPlainText()
        assert page._worker is None
        page._state = "running"
        page._update_buttons()
        assert page.buttons["pause"].isEnabled()
        assert page.buttons["stop"].isEnabled()
        monkeypatch.setattr(offline, "has_feature", lambda _level: True)
        page._state = "idle"
        page._update_buttons()
        assert page.buttons["sync"].isEnabled()
    finally:
        page.close()
        page.deleteLater()
        assert page.preserve_task_params.isChecked()


def test_unlicensed_worker_cannot_connect_or_build_snapshot(qapp, monkeypatch):
    monkeypatch.setattr(transfer, "has_feature", lambda _level: False)
    connect = Mock()
    build = Mock()
    monkeypatch.setattr(offline, "connect_agent_diagnostic", connect)
    monkeypatch.setattr(offline, "build_offline_bundle", build)
    worker = offline._OfflineWorker("fake-device", "sync", "tester", "android", "")
    results = []
    worker.done.connect(lambda data, error: results.append(error))
    worker.run()
    assert len(results) == 1 and "Lv1" in results[0]
    connect.assert_not_called()
    build.assert_not_called()


def test_sync_scope_is_independent_of_remote_user_and_task_choices(qapp, monkeypatch):
    monkeypatch.setattr(offline, "has_feature", lambda _level: True)
    monkeypatch.setattr(offline, "load_layout_entries", lambda: {"android": SimpleNamespace(name="Android")})
    manager = SimpleNamespace(get_active_user_name=lambda: "pc_user")
    page = offline.OfflineControlPage(SimpleNamespace(user_manager=manager), lambda: "fake-device", lambda _: None)
    try:
        assert page.buttons["sync"].isEnabled()
        assert not page.buttons["start"].isEnabled()
        page._done({"serial": "fake-device", "status": {"state": "idle"},
                    "users": {"users": ["phone_first", "phone_second"], "selected": "phone_first"},
                    "tasks": {"tasks": [{"id": "task", "name": "Task"}]}}, "")
        page.user.setCurrentIndex(1)
        assert page.user.currentData() == "phone_second"
        assert page._sync_username() == "pc_user"
        assert page.buttons["sync"].isEnabled() and page.buttons["start"].isEnabled()
        page._done({"serial": "fake-device", "status": {"state": "running", "message": "执行中"}}, "")
        assert page.report.toPlainText().splitlines() == ["任务状态：运行中"]
        page._done({"serial": "fake-device", "status": {"state": "paused", "message": "请手动进入菜单"}}, "")
        assert "请手动进入菜单" in page.report.toPlainText()
    finally:
        page.close()
        page.deleteLater()
