"""安卓截图/输入设置的界面语义与连接命令回归测试。"""

from lvjiang.core.config.models import UserConfig
from lvjiang.ui.settings_dialog import SettingsDialog
from lvjiang.ui.window_ops import _DeviceWorker


def test_android_methods_are_exclusive_radio_groups_and_adb_input_is_default(
        qtbot, monkeypatch):
    saved = {}
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.load_user_config", UserConfig)
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.save_settings",
        lambda settings: saved.update(settings))
    monkeypatch.setattr(
        "lvjiang.ui.settings_dialog.save_app_config", lambda *args: None)

    dialog = SettingsDialog()
    qtbot.addWidget(dialog)

    assert dialog._capture_group.exclusive()
    assert dialog._capture_stream_radio.isChecked()
    assert not dialog._capture_static_radio.isChecked()

    assert dialog._android_input_group.exclusive()
    assert dialog._android_input_adb_radio.isChecked()
    assert not dialog._android_input_agent_radio.isChecked()
    assert "Beta" in dialog._android_input_agent_radio.text()

    dialog._capture_static_radio.click()
    assert dialog._capture_static_radio.isChecked()
    assert not dialog._capture_stream_radio.isChecked()

    dialog._android_input_agent_radio.click()
    assert dialog._android_input_agent_radio.isChecked()
    assert not dialog._android_input_adb_radio.isChecked()

    dialog._collect_custom = lambda: {}
    dialog._collect_envs = lambda: []
    dialog._on_save()
    assert saved["android_capture_method"] == "screencap"
    assert saved["android_input_method"] == "device_gesture"


def test_device_gesture_input_does_not_override_screenshot_method(monkeypatch):
    """启用 App 手势时，screencap 仍必须走 ADB 截图后端。"""
    import lvjiang.core.android as android

    calls = []

    class FakeDevice:
        def __init__(self, serial):
            self.serial = serial

        def get_resolution(self):
            return 1260, 2800

    class FakeAgent:
        connected = True

        def describe(self):
            return "测试代理"

        def close(self):
            calls.append(("close",))

    class FakeCapture:
        def start(self):
            return True

    agent = FakeAgent()
    monkeypatch.setattr(android, "AdbDevice", FakeDevice)
    monkeypatch.setattr(android, "connect_agent", lambda device: agent)

    def fake_create_capture_backend(*, device, method, agent=None):
        calls.append(("capture", method, agent))
        return FakeCapture()

    monkeypatch.setattr(android, "create_capture_backend", fake_create_capture_backend)

    results = []
    worker = _DeviceWorker(
        task="connect", serial="device-1",
        capture_method="screencap", agent_mode=True)
    worker.connect_finished.connect(lambda *args: results.append(args))
    worker._do_connect()

    assert calls == [("capture", "screencap", None)]
    assert results[0][2] == "screencap"
    assert results[0][5] is agent
