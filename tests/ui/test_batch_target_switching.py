"""真实 Qt 选择信号与批量进度页：删除目标后的自动选中不得丢失运行投影。"""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QTreeWidget

from lvjiang.core.batch_config import BatchConfig
from lvjiang.ui.batch.batch_runner import BatchScript
from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.main.execution_runs import ExecutionRunManager, RunState
from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.window_ops import WindowOpsMixin


def _noop(*_args):
    pass


class _Host(QObject, WindowOpsMixin):
    automation_state_changed = pyqtSignal(str)
    _project_run_context_for_target = RunControlMixin._project_run_context_for_target
    _capture_launch_draft = _noop
    _sync_active_target_compat = _noop
    _refresh_active_target_ui = _noop
    _abort_recording_for_target = _noop
    _dispose_execution_target = _noop
    _selected_run_env = _noop

    def __init__(self):
        super().__init__()
        self._user_config = SimpleNamespace(hotkeys=SimpleNamespace(start="F9", pause="F10", stop="F11"))
        self._run_state = "idle"
        self._execution_targets = ExecutionTargetRegistry()
        self._run_manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
        self.execution_target_list = QTreeWidget()
        self.execution_target_list.setColumnCount(5)
        self.execution_target_list.currentItemChanged.connect(self._on_execution_target_selected)
        self.log_text = SimpleNamespace(append=_noop)
        self._red_box_flash_timer = SimpleNamespace(stop=_noop)
        self._overlay = SimpleNamespace(hide_border=_noop)
        self.restored = []
        self.redraws = []

    def statusBar(self):
        return SimpleNamespace(showMessage=_noop)

    def _restore_launch_draft(self, target_id, draft):
        self.restored.append((target_id, draft))

    def _refresh_run_button(self):
        self.automation_state_changed.emit(self._run_state)

    def _redraw_log_events(self):
        self.redraws.append(self._execution_targets.active_target_id)


@pytest.fixture
def running_batch(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.load_batch_config", lambda: BatchConfig())
    monkeypatch.setattr("lvjiang.workflows.discovery.list_exposed_scripts", lambda _: [])
    monkeypatch.setattr("lvjiang.core.app_controller.remove_connected_target", _noop)
    host = _Host()
    qtbot.addWidget(host.execution_target_list)
    window = ExecutionTarget(id=WINDOW_TARGET_ID, kind="windows", display_name="测试窗口",
                             capture=object(), input_ctrl=object())
    phone = ExecutionTarget(id=android_target_id("test-device"), kind="adb", display_name="测试设备",
                            capture=object(), input_ctrl=object())
    host._execution_targets.put(window)
    host._execution_targets.put(phone)
    _, run = host._run_manager.try_begin(target=phone.snapshot(), username="", name="测试批量")
    assert run is not None
    run.metadata.update(batch=True, launch_draft=phone.launch_draft)
    run.worker = SimpleNamespace(isRunning=lambda: True)
    host._run_manager.set_state(run.task_run_id, RunState.RUNNING)
    host._batch_tab = BatchTab(host)
    qtbot.addWidget(host._batch_tab)
    host._batch_tab._build_progress_table(["测试单元"], None, [BatchScript("one", "脚本一"), BatchScript("two", "脚本二")])
    host._batch_tab.bind_run(run.task_run_id)
    host._refresh_execution_targets_ui()
    host.execution_target_list.setCurrentItem(host.execution_target_list.topLevelItem(1))
    return host, window, phone, run


@pytest.mark.parametrize("state", [RunState.RUNNING, RunState.PAUSED, RunState.STOPPING])
def test_removing_selected_window_restores_running_device_immediately(running_batch, state):
    host, window, phone, run = running_batch
    tab = host._batch_tab
    snapshot = run.target_snapshot
    host._run_manager.set_state(run.task_run_id, state)
    host.execution_target_list.setCurrentItem(host.execution_target_list.topLevelItem(0))
    assert tab._progress_table.rowCount() == 0
    tab.update_run_progress(run.task_run_id, 0, "测试单元", "one", "完成")
    host._disconnect_execution_target(window.id)

    assert host._execution_targets.active_target_id == phone.id
    assert host._current_run_context is run
    assert host._current_worker is run.worker
    assert host._run_state == state.value
    assert tab._visible_run_id == run.task_run_id
    assert tab._progress_table.rowCount() == 2
    assert tab._progress_table.item(0, 2).text() == "完成"
    assert host.restored[-1] == (phone.id, run.metadata["launch_draft"])
    assert host.redraws[-1] == phone.id
    assert run.target_snapshot is snapshot
    assert not run.stop_event.is_set()
    if state == RunState.PAUSED:
        assert "恢复" in tab._btn_pause_resume.text()
    elif state == RunState.STOPPING:
        assert not tab._btn_run.isEnabled()
    else:
        assert "暂停" in tab._btn_pause_resume.text()
    tab.update_run_progress(run.task_run_id, 0, "测试单元", "two", "执行中")
    assert tab._progress_table.item(1, 2).text() == "执行中"


def test_removing_unselected_window_preserves_visible_progress(running_batch):
    host, window, phone, run = running_batch
    tab = host._batch_tab
    tab.update_run_progress(run.task_run_id, 0, "测试单元", "one", "执行中")
    item = tab._progress_table.item(0, 2)
    restores = len(host.restored)
    host._disconnect_execution_target(window.id)
    assert host._execution_targets.active_target_id == phone.id
    assert tab._progress_table.item(0, 2) is item
    assert tab._visible_run_id == run.task_run_id
    assert len(host.restored) == restores


def test_removing_last_idle_target_clears_projection(running_batch):
    host, window, phone, run = running_batch
    host._run_manager.finish(run.task_run_id, RunState.COMPLETED)
    host._batch_tab.finish_run(run.task_run_id)
    host._disconnect_execution_target(window.id)
    host._disconnect_execution_target(phone.id)
    assert host._execution_targets.active_target_id is None
    assert host._current_run_context is None
    assert host._current_worker is None
    assert host._run_state == "idle"
    assert host._batch_tab._progress_table.rowCount() == 0
    assert run.task_run_id in host._batch_tab._run_progress
