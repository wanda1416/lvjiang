"""设备恢复询问必须非阻塞且只停止对应执行链。"""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

from lvjiang.ui.main.execution_runs import RunState
from lvjiang.ui.main.run_control import RunControlMixin


def test_recovery_question_waits_for_yes_or_no_without_affecting_other_target(qtbot):
    class Host(RunControlMixin, QWidget):
        pass
    host = Host()
    qtbot.addWidget(host)
    context = SimpleNamespace(task_run_id='test-run', stop_event=threading.Event(), pause_event=None)
    other = SimpleNamespace(stop_event=threading.Event())
    contexts = {'test-target': context, 'other-target': other}
    host._run_manager = SimpleNamespace(run_for_target=contexts.get,
                                       set_state=lambda *args: None)
    host._emit_run_instance_state = lambda *args: None
    host.log_text = SimpleNamespace(append=lambda message: None)
    target = SimpleNamespace(id='test-target', display_name='测试设备', resume_event=threading.Event())
    host._show_connection_recovery_question(target, context, '重试耗尽')
    box = host._connection_recovery_dialogs[target.id]
    assert box.windowModality() == Qt.WindowModality.NonModal
    assert box.isVisible() and not target.resume_event.is_set()
    box.done(QMessageBox.StandardButton.Yes)
    assert target.resume_event.is_set() and not context.stop_event.is_set()
    target.resume_event.clear()
    host._show_connection_recovery_question(target, context, '仍未恢复')
    box = host._connection_recovery_dialogs[target.id]
    states = []
    host._run_manager.set_state = lambda run, state: states.append(state)
    box.done(QMessageBox.StandardButton.No)
    assert context.stop_event.is_set() and target.resume_event.is_set()
    assert states == [RunState.STOPPING]
    assert not other.stop_event.is_set()
    qtbot.waitUntil(lambda: not host._connection_recovery_dialogs)


def test_recheck_does_not_resume_task_and_verified_recovery_preserves_user_pause(qtbot):
    class Host(RunControlMixin, QWidget):
        pass
    host = Host()
    qtbot.addWidget(host)
    resume = threading.Event()
    pause = threading.Event()  # clear 表示用户主动暂停。
    target = SimpleNamespace(id='test-target', resume_event=resume,
                             input_ctrl=SimpleNamespace(kind='agent'))
    context = SimpleNamespace(target_id=target.id, target_snapshot=target,
                              state=RunState.WAITING_TARGET, pause_event=pause,
                              stop_event=threading.Event(), task_run_id='test-run')
    host._current_run_context = context
    host._run_manager = SimpleNamespace(run_for_target=lambda _id: context, set_state=Mock())
    host._execution_targets = SimpleNamespace(get=lambda _id: target, active_target_id='other-target')
    host.log_text = SimpleNamespace(append=Mock())
    host.statusBar = lambda: SimpleNamespace(showMessage=Mock())
    host._emit_run_instance_state = Mock()
    host._refresh_execution_targets_ui = Mock()
    host._resume_adb()
    assert resume.is_set()
    host._run_manager.set_state.assert_not_called()
    host._on_adb_connection_restored(target.id)
    host._run_manager.set_state.assert_called_once_with(context.task_run_id, RunState.PAUSED)
    assert not pause.is_set()
