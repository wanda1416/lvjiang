from dataclasses import asdict
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.apps.yysls.core.gather import GatherStore
from lvjiang.apps.yysls.ui.gather import (
    GatherRecordingDialog,
    GatherTab,
    open_recording,
)
from lvjiang.ui.button_styles import (
    ACTION_BUTTON_STYLE,
    DANGER_BUTTON_STYLE,
    NEUTRAL_BUTTON_STYLE,
)
from tests.yysls.test_gather import make_route


@pytest.fixture(autouse=True)
def isolated_gather_workflows(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lvjiang.apps.yysls.core.gather.GATHER_WORKFLOWS_DIR",
        tmp_path / "config" / "local" / "workflows" / "gather",
    )


class Host(QWidget):
    automation_state_changed = pyqtSignal(str)
    user_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.user_manager = SimpleNamespace(list_users=lambda: ["fake-user"], get_active_user_name=lambda: "fake-user")
        self._user_config = SimpleNamespace(hotkeys=SimpleNamespace(start="F9", pause="F11", stop="F10", record="F12"))
        self._backend = "desktop"
        self._target_window = {"hwnd": 1}
        self.is_running = False
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("桌面", "desktop")
        self.launches = []
        self.stops = 0
        self.pauses = 0
        self.cleanups = []
        self.input_events = []
        self._input = SimpleNamespace(
            key_down=lambda key: self.input_events.append(("down", key)),
            key_up=lambda key: self.input_events.append(("up", key)),
        )

    def register_cleanup(self, callback):
        self.cleanups.append(callback)

    def _selected_run_env(self):
        return "desktop"

    def _backend_ready(self):
        return True

    def request_stop(self):
        self.stops += 1

    def request_pause_resume(self):
        self.pauses += 1

    def run_workflow_implementation(self, *args, **kwargs):
        self.launches.append((args, kwargs))


def test_fourth_tab_plugin_registration_and_dedicated_task():
    from lvjiang.apps.yysls import hooks
    from lvjiang.apps.yysls.workflows.implementations.auto_gather import (
        AutoGatherWorkflow,
    )
    assert [name for name, _ in hooks.left_tab_builders] == ["调律", "采集"]
    assert "auto_gather" in hooks.workflow_implementations
    assert AutoGatherWorkflow.SCOPE == "dedicated"


def test_tab_uses_common_launch_pause_stop(qtbot):
    store = GatherStore()
    route = make_route()
    store.save(route, None)
    host = Host()
    qtbot.addWidget(host)
    tab = GatherTab(host)
    qtbot.addWidget(tab)
    tab.f9_run()
    args, kwargs = host.launches[0]
    assert args[0] == "auto_gather"
    assert kwargs["execution_username"] == "fake-user"
    assert "viewport" not in kwargs["history_params"]["steps"][0]
    host.is_running = True
    host.automation_state_changed.emit("paused")
    assert "恢复" in tab.pause_button.text()
    tab.pause_button.click()
    tab.f9_run()
    assert host.stops == host.pauses == 1


def test_gather_buttons_use_shared_styles_for_actions_and_states(qtbot):
    store = GatherStore()
    store.save(make_route(), None)
    host = Host()
    qtbot.addWidget(host)
    tab = GatherTab(host)
    qtbot.addWidget(tab)

    assert tab.run_button.styleSheet() == ACTION_BUTTON_STYLE
    assert tab.pause_button.styleSheet() == NEUTRAL_BUTTON_STYLE
    assert tab.edit_button.styleSheet() == NEUTRAL_BUTTON_STYLE

    host.is_running = True
    host.automation_state_changed.emit("running")
    assert tab.run_button.styleSheet() == DANGER_BUTTON_STYLE
    assert tab.pause_button.styleSheet() == NEUTRAL_BUTTON_STYLE
    host.automation_state_changed.emit("paused")
    assert tab.pause_button.styleSheet() == ACTION_BUTTON_STYLE

    host.is_running = False
    editor = GatherRecordingDialog(host)
    qtbot.addWidget(editor)
    assert editor.open_button.styleSheet() == NEUTRAL_BUTTON_STYLE
    assert editor.new_button.styleSheet() == ACTION_BUTTON_STYLE
    assert editor.record_button.styleSheet() == ACTION_BUTTON_STYLE
    assert editor.undo_button.styleSheet() == DANGER_BUTTON_STYLE
    assert editor.save_button.styleSheet() == ACTION_BUTTON_STYLE

    editor.recorder = SimpleNamespace(stop=lambda: None)
    editor._refresh()
    assert editor.record_button.styleSheet() == DANGER_BUTTON_STYLE


def test_editing_route_does_not_change_selected_run_route(qtbot):
    store = GatherStore()
    a, b = make_route("甲"), make_route("乙")
    store.save(a, None)
    store.save(b, None)
    host = Host()
    qtbot.addWidget(host)
    tab = GatherTab(host)
    qtbot.addWidget(tab)
    tab.routes.setCurrentIndex(tab.routes.findData(a.key))
    editor = GatherRecordingDialog(host)
    qtbot.addWidget(editor)
    editor.saved.setCurrentIndex(editor.saved.findData(b.key))
    editor._open()
    editor.name.setText("乙改名")
    editor.auto_travel_key.setText("B")
    editor._save()
    assert tab.routes.currentData() == a.key
    assert editor.route.name == "乙改名"
    reloaded, _ = store.routes()
    saved_b = next(r for r in reloaded if r.key == b.key)
    assert saved_b.steps == b.steps
    assert saved_b.travel_key == "B"


def test_single_nonmodal_editor(qtbot):
    host = Host()
    qtbot.addWidget(host)
    editor = open_recording(host)
    qtbot.addWidget(editor)
    assert open_recording(host) is editor
    assert not editor.isModal()


def test_escape_stops_recording_listener(qtbot):
    host = Host()
    qtbot.addWidget(host)
    editor = GatherRecordingDialog(host)
    qtbot.addWidget(editor)
    stopped = []
    editor.recorder = SimpleNamespace(stop=lambda: stopped.append("mouse"))
    editor.hotkeys = SimpleNamespace(stop=lambda: stopped.append("keys"))
    editor.show()
    editor.reject()
    assert stopped == ["keys", "mouse"]
    assert not editor.isVisible()


def test_f1_f2_build_visible_sequence_and_record_travel_time(qtbot, monkeypatch):
    host = Host()
    qtbot.addWidget(host)
    editor = GatherRecordingDialog(host)
    qtbot.addWidget(editor)
    editor.route = make_route()
    editor.route.steps.clear()
    editor._load_form()
    point = make_route().steps[0]
    editor.recorder = SimpleNamespace(mark_current=lambda: point, stop=lambda: None)
    monkeypatch.setattr(editor, "_check_recording_context", lambda: None)
    clock = iter([10.0, 22.5])
    monkeypatch.setattr("lvjiang.apps.yysls.ui.gather.time.monotonic", lambda: next(clock))

    editor.mark()
    assert editor.marked is point
    assert editor.steps.item(0).text() == '1. press "M"'
    assert editor.steps.item(1).text() == "2. wait $gather_map_open_wait"
    assert editor.steps.item(2).text() == "3. click (0.400000, 0.500000)"
    editor.travel()
    assert host.input_events == [("down", "V"), ("up", "V")]
    editor._confirm_travel()
    assert host.input_events[-2:] == [("down", "F"), ("up", "F")]
    assert editor.steps.item(4).text() == '5. press "V"'
    assert editor.steps.item(6).text() == '7. press "F"'
    editor.travel()
    assert len(editor.route.steps) == 1
    assert editor.route.steps[0].travel_seconds == 12.5
    assert editor.steps.item(8).text() == '9. press "1"'
    assert GatherStore().routes()[0] == []
    editor.previous = asdict(editor.route)
