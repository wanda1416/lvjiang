"""窗口定位红框：默认短暂提示，手动勾选后持续显示。"""

from types import SimpleNamespace

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton, QWidget

from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Overlay:
    def __init__(self):
        self.visible = False
        self.color = None

    def show_border(self, *_rect):
        self.visible = True

    def hide_border(self):
        self.visible = False

    def set_color(self, color):
        self.color = color


class _Host(WindowOpsMixin, QWidget):
    def _restore_active_target_view(self):
        self._sync_active_target_compat()

    def __init__(self):
        super().__init__()
        self._backend = "windows"
        self._candidate_backend = "windows"
        self._running = False
        self._execution_targets = ExecutionTargetRegistry()
        self._device_ready = False
        self._target_window = None
        # 定位流程会按「后台截图」开关装配截图后端，宿主需提供这个字段
        self._capture = None
        self._overlay = _Overlay()
        self._red_box_flash_timer = QTimer(self)
        self._red_box_flash_timer.setSingleShot(True)
        self._red_box_flash_timer.timeout.connect(self._hide_red_box_after_locate)
        self.chk_red_box = QCheckBox(self)
        self.chk_red_box.stateChanged.connect(self._on_red_box_changed)
        self.window_combo = SimpleNamespace(currentData=lambda: {
            "hwnd": 1, "title": "测试窗口", "left": 10, "top": 20,
            "width": 800, "height": 600,
        })
        self.lbl_window_info = QLabel(self)
        self.btn_locate = QPushButton(self)
        self.log_text = SimpleNamespace(append=lambda _message: None)
        self._status_bar = SimpleNamespace(showMessage=lambda _message: None)

    def _refresh_window_rect(self, _window):
        pass

    def _get_window_dpi_ratio(self, _hwnd):
        return 1.0

    def _build_window_execution_target(self, window):
        return ExecutionTarget(
            id=WINDOW_TARGET_ID,
            kind="windows",
            display_name=window["title"],
            capture=object(),
            input_ctrl=SimpleNamespace(background_mode=False),
            window=window,
        )

    def _dispose_execution_target(self, _target):
        pass

    def _refresh_execution_targets_ui(self):
        pass

    def _refresh_active_target_ui(self):
        pass

    def _refresh_run_button(self):
        pass

    def _capture_preview(self):
        pass

    def statusBar(self):  # noqa: N802 - Qt API shape
        return self._status_bar


def test_locate_shows_red_box_for_one_second_by_default(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.core.app_controller.record_connected_window", lambda _w: None)
    host = _Host()
    qtbot.addWidget(host)

    assert not host.chk_red_box.isChecked()
    host._on_locate_window()

    assert host._overlay.visible
    assert host._overlay.color == "red"
    assert host._red_box_flash_timer.interval() == 1000
    qtbot.waitUntil(lambda: not host._red_box_flash_timer.isActive(), timeout=2000)
    assert not host._overlay.visible


def test_checked_red_box_stays_visible_and_toggle_cancels_flash(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.core.app_controller.record_connected_window", lambda _w: None)
    host = _Host()
    qtbot.addWidget(host)

    host._on_locate_window()
    assert host._red_box_flash_timer.isActive()

    host.chk_red_box.setChecked(True)
    assert host._overlay.visible
    assert not host._red_box_flash_timer.isActive()
    host._hide_red_box_after_locate()
    assert host._overlay.visible

    host.chk_red_box.setChecked(False)
    assert not host._overlay.visible

    host._disconnect_execution_target(WINDOW_TARGET_ID)
    host.chk_red_box.setChecked(True)
    host._on_locate_window()
    assert host._overlay.visible
    assert not host._red_box_flash_timer.isActive()


def test_disconnect_cancels_flash_before_next_locate(qtbot, monkeypatch):
    monkeypatch.setattr("lvjiang.core.app_controller.record_connected_window", lambda _w: None)
    host = _Host()
    qtbot.addWidget(host)

    host._on_locate_window()
    host._disconnect_execution_target(WINDOW_TARGET_ID)
    assert not host._overlay.visible
    assert not host._red_box_flash_timer.isActive()

    host._on_locate_window()
    assert host._overlay.visible
    assert host._red_box_flash_timer.isActive()
