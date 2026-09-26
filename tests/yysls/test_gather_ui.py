from dataclasses import asdict
from types import SimpleNamespace

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.apps.yysls.core.gather import GatherStore
from lvjiang.apps.yysls.ui.gather import (
    GatherRecordingDialog,
    GatherTab,
    open_recording,
)
from tests.yysls.test_gather import make_route


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
    editor._save()
    assert tab.routes.currentData() == a.key
    assert editor.route.name == "乙改名"
    reloaded, _ = store.routes()
    assert next(r for r in reloaded if r.key == b.key).steps == b.steps


def test_single_nonmodal_editor_and_failed_trial_not_saved(qtbot, monkeypatch):
    host = Host()
    qtbot.addWidget(host)
    editor = open_recording(host)
    qtbot.addWidget(editor)
    assert open_recording(host) is editor
    assert not editor.isModal()
    editor.route = make_route()
    editor.previous = asdict(editor.route)
    editor._load_form()
    editor._trial_step = editor.route.steps[0]
    editor._trial_active = True
    editor._state("ready")
    assert len(editor.route.steps) == 1
    assert editor._trial_step is None


def test_close_waits_for_trial_shutdown(qtbot):
    host = Host()
    qtbot.addWidget(host)
    editor = GatherRecordingDialog(host)
    qtbot.addWidget(editor)
    editor.show()
    editor._trial_active = True
    host.is_running = True
    assert not editor.close()
    assert host.stops == 1
    assert editor.isVisible()
    host.is_running = False
    host.automation_state_changed.emit("ready")
    assert not editor.isVisible()


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


def test_recording_trial_only_updates_draft_after_success(qtbot, monkeypatch):
    host = Host()
    qtbot.addWidget(host)
    editor = GatherRecordingDialog(host)
    qtbot.addWidget(editor)
    editor.route = make_route()
    editor.route.steps.clear()
    editor._load_form()
    editor.marked = make_route().steps[0]
    monkeypatch.setattr(editor, "_check_recording_context", lambda: None)

    def launch(*args, **kwargs):
        host.is_running = True
        host.automation_state_changed.emit("running")

    monkeypatch.setattr(host, "run_workflow_implementation", launch)
    editor.travel()
    assert editor._trial_active
    assert editor._trial_step is not None
    editor._completed({"steps": [{"travel_seconds": 12.5, "collection_triggered": True}]})
    assert len(editor.route.steps) == 1
    assert editor.route.steps[0].travel_seconds == 12.5
    assert GatherStore().routes()[0] == []
    host.is_running = False
    host.automation_state_changed.emit("ready")
    editor.previous = asdict(editor.route)
