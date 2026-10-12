"""设备恢复询问必须非阻塞且只停止对应执行链。"""
import threading
from types import SimpleNamespace

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
