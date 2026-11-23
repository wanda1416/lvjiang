"""Tools-only opt-in preview controls; no simulation GUI pretence or persisted enable."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QLabel, QMainWindow, QWidget

from lvjiang.core.task_diagnostics import TaskDiagnostics
from lvjiang.ui.main.menu_ops import MenuOpsMixin
from lvjiang.ui.task_diagnostics_dialog import TaskDiagnosticsDialog


def test_tools_menu_opens_one_modeless_diagnostic_dialog(qtbot):
    class Host(MenuOpsMixin, QMainWindow):
        def _open_batch_config(self):
            pass

        def _check_update(self):
            pass

    host = Host()
    qtbot.addWidget(host)
    host._setup_menu()
    assert getattr(host, "_task_diagnostics", None) is None
    tools = next(a.menu() for a in host.menuBar().actions() if a.text() == "工具")
    action = next(a for a in tools.actions() if a.text() == "任务诊断")
    # 菜单保持四字短标签；「开发预览」的说明放在对话框正文，不塞进菜单标题。
    assert action.text() == "任务诊断"
    action.trigger()
    dialog = host._modeless_tool_windows["task_diagnostics"]
    # pytest-qt also closes registered widgets at teardown; avoid double-close
    # after the production modeless helper schedules deleteLater on close.
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    qtbot.addWidget(dialog)
    assert isinstance(dialog, TaskDiagnosticsDialog) and not dialog.isModal()
    action.trigger()
    assert host._modeless_tool_windows["task_diagnostics"] is dialog
    assert len(host._modeless_tool_windows) == 1
    assert host._close_modeless_tools()


def test_diagnostic_tabs_defaults_ephemeral_settings_and_launch_freeze(qtbot):
    service = TaskDiagnostics()
    dialog = TaskDiagnosticsDialog(service)
    qtbot.addWidget(dialog)
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "诊断调试",
        "模拟运行",
    ]
    assert dialog.tabs.widget(1).findChildren(QWidget) == []
    assert len(dialog.findChildren(QCheckBox)) == 1
    assert dialog.enabled_check.text() == "启用开发诊断模式"
    # 开发预览的口径写在对话框里，菜单标题只保留四字。
    assert any("开发预览" in label.text() for label in dialog.findChildren(QLabel))
    assert not dialog.enabled_check.isChecked() and service.freeze() is None
    assert dialog.frames_spin.value() == 100 and dialog.capacity_spin.value() == 50
    assert not dialog.frames_spin.isEnabled() and not dialog.capacity_spin.isEnabled()
    assert service.options.max_bytes == 50_000_000
    dialog.enabled_check.setChecked(True)
    assert dialog.frames_spin.isEnabled() and dialog.capacity_spin.isEnabled()
    frozen = service.freeze()
    dialog.frames_spin.setValue(2)
    dialog.capacity_spin.setValue(1)
    assert frozen.options.max_frames == 100 and frozen.options.max_bytes == 50_000_000
    assert service.freeze().options.max_frames == 2
    dialog.close()
    reopened = TaskDiagnosticsDialog(service)
    qtbot.addWidget(reopened)
    assert (
        reopened.enabled_check.isChecked()
    )  # Reopening isn't restarting the application.
    restarted = TaskDiagnosticsDialog(TaskDiagnostics())
    qtbot.addWidget(restarted)
    assert not restarted.enabled_check.isChecked()


def test_real_main_controls_no_longer_expose_capture_checkbox(qtbot, monkeypatch):
    """Build the real main control area, without managers, devices or unrelated tabs."""
    from types import SimpleNamespace

    from lvjiang.core.config.models import UserConfig
    from lvjiang.ui.main import window

    class Host(window.MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self._user_config = UserConfig()
            self._backend = None
            self._reference_db = SimpleNamespace(
                get_spaces=lambda: [], get_active_space=lambda: ""
            )
            self._setup_ui()

        def _apply_backend_ui(self, backend):
            pass

        def _build_capture_panel(self):
            return QWidget()

        def _build_left_tabs(self):
            pass

        def _build_right_tabs(self):
            pass

        def _restore_ui_state(self):
            pass

        def _setup_log_redirect(self):
            pass

        def _resize_execution_target_columns(self):
            pass

        def closeEvent(self, event):  # noqa: N802 - Qt
            QMainWindow.closeEvent(self, event)

    monkeypatch.setattr(window, "load_available_envs", lambda: [])
    monkeypatch.setattr(window, "load_env", lambda: "")
    host = Host()
    qtbot.addWidget(host)
    assert not hasattr(host, "chk_full_task_capture")
    assert not hasattr(host, "_task_diagnostics")
    assert all("全帧采集" not in c.text() for c in host.findChildren(QCheckBox))
