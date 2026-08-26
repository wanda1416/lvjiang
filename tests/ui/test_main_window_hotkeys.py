"""全局热键回调门控测试

主窗口只注册全局热键 F8-F10（按键位跟随 UserConfig.hotkeys，默认值），
且回调须经 _backend_ready() 门控：未定位窗口/未连接设备时直接忽略，
不发射信号。不实例化 MainWindow、不启动 pynput listener，用桩对象直调
未绑定方法验证门控逻辑。
"""

from lvjiang.core.config.models import UserConfig
from lvjiang.ui.main.window import MainWindow
from lvjiang.ui.settings_dialog import SettingsDialog


class _Signal:
    """信号桩：记录 emit 次数"""

    def __init__(self):
        self.count = 0

    def emit(self):
        self.count += 1


class _Stub:
    """MainWindow 热键回调所需的最小属性集"""

    def __init__(self, ready: bool, running: bool = False):
        self._ready = ready
        self._running = running
        self._user_config = UserConfig()
        self.started = 0
        self.f9_pressed = _Signal()
        self.f10_pressed = _Signal()
        self.pause_resume_count = 0
        self._on_global_f8 = lambda: None
        self._on_global_f9 = lambda: None
        self._on_global_f10 = lambda: None
        self._hotkey_listener = None
        self.run_button_refreshes = 0
        self.pause_button_refreshes = 0
        self.status_messages = []

    def _backend_ready(self) -> bool:
        return self._ready

    def _on_start(self):
        self.started += 1

    def _on_pause_resume(self):
        self.pause_resume_count += 1

    def _main_global_hotkey_bindings(self):
        return MainWindow._main_global_hotkey_bindings(self)

    def _refresh_run_button(self):
        self.run_button_refreshes += 1

    def _refresh_pause_button(self):
        self.pause_button_refreshes += 1

    def statusBar(self):
        return self

    def showMessage(self, message, _timeout=0):
        self.status_messages.append(message)


class TestGlobalHotkeyGating:
    def test_main_listener_does_not_register_f12(self):
        stub = _Stub(ready=True)
        bindings = MainWindow._main_global_hotkey_bindings(stub)
        assert set(bindings) == {"<f8>", "<f9>", "<f10>"}

    def test_not_ready_ignores_all(self):
        stub = _Stub(ready=False)
        MainWindow._on_global_f9(stub)
        MainWindow._on_global_f10(stub)
        MainWindow._on_global_f8(stub)
        assert stub.f9_pressed.count == 0
        assert stub.f10_pressed.count == 0
        assert stub.pause_resume_count == 0

    def test_ready_emits_signals(self):
        stub = _Stub(ready=True)
        MainWindow._on_global_f9(stub)
        MainWindow._on_global_f10(stub)
        MainWindow._on_global_f8(stub)
        assert stub.f9_pressed.count == 1
        assert stub.f10_pressed.count == 1
        assert stub.pause_resume_count == 1  # F8 触发暂停/恢复


class TestConfigurableHotkeyBindings:
    def test_settings_only_offer_f7_through_f12_and_emit_on_save(
            self, qtbot, monkeypatch):
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

        for combo in dialog._hotkey_combos.values():
            assert [combo.itemText(i) for i in range(combo.count())] == [
                "F7", "F8", "F9", "F10", "F11", "F12",
            ]
            longest_text = max(
                combo.fontMetrics().horizontalAdvance(combo.itemText(i))
                for i in range(combo.count())
            )
            # 除文字外还要留出下拉箭头、边框和左右内边距。
            assert combo.minimumWidth() >= longest_text + 36
            assert combo.maximumWidth() == combo.minimumWidth()

        dialog._collect_custom = lambda: {}
        dialog._collect_envs = lambda: []
        emitted = []
        dialog.hotkeys_saved.connect(emitted.append)
        dialog._on_save()

        assert emitted == [saved["hotkeys"]]

    def test_bindings_follow_configured_keys(self):
        stub = _Stub(ready=True)
        stub._user_config = UserConfig(
            hotkeys={"start": "F7", "pause": "F8", "stop": "F11", "record": "F12"})
        bindings = MainWindow._main_global_hotkey_bindings(stub)
        assert set(bindings) == {"<f7>", "<f8>", "<f11>"}
        assert bindings["<f7>"] is stub._on_global_f9
        assert bindings["<f11>"] is stub._on_global_f10
        assert bindings["<f8>"] is stub._on_global_f8

    def test_saved_hotkeys_replace_listener_immediately(self, monkeypatch):
        class _Listener:
            def __init__(self):
                self.stopped = False
                self.joined = False

            def stop(self):
                self.stopped = True

            def join(self, _timeout):
                self.joined = True

            def is_alive(self):
                return False

        old_listener = _Listener()
        new_listener = _Listener()
        registrations = []
        monkeypatch.setattr(
            "lvjiang.core.platforms.start_global_hotkeys",
            lambda bindings: registrations.append(bindings) or new_listener,
        )
        stub = _Stub(ready=True)
        stub._hotkey_listener = old_listener

        MainWindow._apply_hotkey_settings(
            stub,
            {"start": "F7", "pause": "F8", "stop": "F11", "record": "F12"},
        )

        assert set(registrations[0]) == {"<f7>", "<f8>", "<f11>"}
        assert stub._hotkey_listener is new_listener
        assert old_listener.stopped and old_listener.joined
        assert stub._user_config.hotkeys.start == "F7"
        assert stub.run_button_refreshes == 1
        assert stub.pause_button_refreshes == 1


class TestF9StartEntry:
    def test_starts_when_idle(self):
        stub = _Stub(ready=True, running=False)
        MainWindow._on_f9_start(stub)
        assert stub.started == 1

    def test_ignored_when_running(self):
        stub = _Stub(ready=True, running=True)
        MainWindow._on_f9_start(stub)
        assert stub.started == 0
