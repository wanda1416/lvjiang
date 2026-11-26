"""Lv1 仅限制配置下发，不影响状态查看及已有任务的安全控制。"""
from types import SimpleNamespace
from unittest.mock import Mock

from lvjiang.core.android import offline as transfer
from lvjiang.ui.mobile import offline


def test_sync_is_disabled_but_existing_task_controls_remain_available(qapp, monkeypatch):
    monkeypatch.setattr(offline, "has_feature", lambda _level: False)
    monkeypatch.setattr(transfer, "has_feature", lambda _level: False)
    monkeypatch.setattr(offline, "load_layout_entries", lambda: {"android": SimpleNamespace(name="Android", env="android"), "desktop": SimpleNamespace(name="Desktop", env="desktop")})
    monkeypatch.setattr(offline, "load_plans", lambda: [
        SimpleNamespace(id="plan-android", name="手游", space="android", env="android", layout="android"),
        SimpleNamespace(id="plan-desktop", name="端游", space="desktop", env="desktop", layout="desktop"),
    ])
    monkeypatch.setattr(offline, "get_active_plan_id", lambda: "plan-android")
    manager = SimpleNamespace(list_users=lambda: ["tester"], get_active_user_name=lambda: "tester")
    page = offline.OfflineControlPage(SimpleNamespace(user_manager=manager), lambda: "", lambda _: None)
    try:
        assert not page.buttons["sync"].isEnabled()
        assert "需激活功能" in page.buttons["sync"].text()
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
        assert page.plan_choice.count() == 1
        assert page.plan_choice.itemData(0) == "plan-android"
        assert page.buttons["sync"].isEnabled()
        assert page.buttons["sync"].text() == "下发配置"
        assert page.preserve_task_params.isChecked()
    finally:
        page.close()
        page.deleteLater()


def test_mobile_dialog_opens_offline_tasks_before_application_install(qapp, monkeypatch):
    from lvjiang.ui.mobile import dialog
    monkeypatch.setattr(dialog, "list_adb_devices", lambda: [])
    monkeypatch.setattr(dialog, "lan_addresses", lambda: [])
    monkeypatch.setattr(dialog, "find_local_apk", lambda *_args: None)
    window = dialog.MobileDeviceDialog()
    try:
        assert window._tabs.tabText(0) == "离线任务"
        assert window._tabs.tabText(1) == "应用安装"
        assert window._tabs.currentIndex() == 0
    finally:
        window.close()
        window.deleteLater()


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
    monkeypatch.setattr(offline, "load_layout_entries", lambda: {"android": SimpleNamespace(name="Android", env="android"), "desktop": SimpleNamespace(name="Desktop", env="desktop")})
    monkeypatch.setattr(offline, "load_plans", lambda: [
        SimpleNamespace(id="plan-android", name="手游", space="android", env="android", layout="android"),
        SimpleNamespace(id="plan-desktop", name="端游", space="desktop", env="desktop", layout="desktop"),
    ])
    monkeypatch.setattr(offline, "get_active_plan_id", lambda: "plan-android")
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


def test_history_choices_are_device_specific_and_independent_of_downstream(qapp, monkeypatch, tmp_path):
    from lvjiang.core.config import interface
    interface.reset_interface_store()
    monkeypatch.setattr(offline, "has_feature", lambda _: False)
    monkeypatch.setattr(offline, "load_layout_entries", dict)
    monkeypatch.setattr(offline, "load_plans", list)
    monkeypatch.setattr(offline, "get_active_plan_id", lambda: "")
    serial = ["device-a"]
    page = offline.OfflineControlPage(None, lambda: serial[0], lambda _: None)
    try:
        assert page.buttons["history"].isEnabled()
        assert not page.buttons["sync"].isEnabled()
        page.history_choices["tasks"].setChecked(False)
        assert page.history_choices["logs"].isChecked()
        # 勾选记忆是界面状态，落在 interface.json 的 ui_state，不再单开配置文件与目录
        stored = interface.load_ui_page_state("mobile_offline")["history_choices"]
        assert stored["device-a"]["tasks"] is False
        assert not list((tmp_path / "session").glob("history-transfer*"))
        serial[0] = "device-b"
        page.device_changed()
        assert page.history_choices["tasks"].isChecked()
        serial[0] = "device-a"
        page.device_changed()
        assert not page.history_choices["tasks"].isChecked()
        page._state = "running"
        page._update_buttons()
        # 回传只导出终态历史（运行中的任务会被导出查询跳过），所以任务运行中也保持可用
        assert page.buttons["history"].isEnabled()
        assert page.buttons["stop"].isEnabled()
    finally:
        page.close()
        page.deleteLater()
        interface.reset_interface_store()
