"""燕云采集页与非模态录制编辑器。"""
from __future__ import annotations

import sys
import time
from copy import deepcopy
from dataclasses import asdict

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ....core.access import is_readonly
from ....core.key_names import normalize_pressable
from ....core.platforms import hotkey_pynput_token, start_global_hotkeys
from ....i18n import tr
from ....ui.button_styles import (
    apply_button_style,
    exec_styled_message_box,
    fit_button_width,
)
from ....ui.execution_user_selector import ExecutionUserSelector
from ....ui.hotkeys import hotkey_label
from ..core.gather import GatherRoute, GatherStep, GatherStore, gather_step_dsl
from ..core.gather_recorder import GatherInputRecorder


class GatherSignals(QObject):
    progress = pyqtSignal(str)
    completed = pyqtSignal(dict)
    failed = pyqtSignal(str)
    mark = pyqtSignal()
    travel = pyqtSignal()


def _start_task(host, route, signals, username, *, selected_target=False):
    snapshot = deepcopy(route)
    window = dict(host._target_window or {})

    def configure(workflow, engine):
        # 桌面截图来自实际屏幕；按钮启动时先让游戏到前台，防止编辑器遮挡地图。
        # configure 在宿主取得执行授权后调用。
        if sys.platform == "win32":
            from ....core.desktop.win32_util import activate_window
            if not activate_window(window["hwnd"], restore=False):
                raise ValueError(tr("无法激活游戏窗口，请切到游戏后重试"))
        workflow.configure(snapshot, selected_target=selected_target,
                           progress=signals.progress.emit, completed=signals.completed.emit)

    params = asdict(snapshot)
    for step in params["steps"]:
        step.pop("viewport", None)
    params["recording_trial"] = selected_target
    host.run_workflow_implementation(
        "auto_gather", tr("采集试跑") if selected_target else tr("自动采集"), configure,
        execution_username=username, history_params=params)


def _desktop_error(host) -> str:
    if is_readonly():
        return tr("只读实例不能执行采集或监听录制")
    if host._selected_run_env() != "desktop" or host._backend == "adb":
        return tr("实验性采集仅支持桌面游戏环境")
    if not host._backend_ready():
        return tr("请先连接游戏")
    allows = getattr(host, "_plan_allows_backend", None)
    if allows is not None and not allows():
        return tr("当前连接方案不支持所选后端")
    return ""


def open_recording(host):
    dialog = getattr(host, "_gather_dialog", None)
    if dialog is None:
        dialog = GatherRecordingDialog(host)
        host._gather_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog


class GatherTab(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self._host = host
        host._gather_tab = self
        self.store = GatherStore()
        self.signals = GatherSignals(self)
        self._routes: dict[str, GatherRoute] = {}
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.run_button = QPushButton()
        self.run_button.clicked.connect(self.f9_run)
        self.pause_button = QPushButton(tr("暂停"))
        self.pause_button.clicked.connect(host.request_pause_resume)
        apply_button_style(self.run_button, variant="action")
        apply_button_style(self.pause_button, variant="neutral")
        fit_button_width(self.run_button, self.pause_button)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.pause_button)
        layout.addLayout(buttons)
        self.users = ExecutionUserSelector(host.user_manager)
        layout.addWidget(self.users)
        self.routes = QComboBox()
        self.routes.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.routes.currentIndexChanged.connect(self._selected)
        layout.addWidget(QLabel(tr("采集路线（实验性标记模式）")))
        layout.addWidget(self.routes)
        self.start_note = QLabel()
        self.start_note.setWordWrap(True)
        layout.addWidget(self.start_note)
        self.edit_button = QPushButton(tr("采集录制…"))
        self.edit_button.clicked.connect(lambda: open_recording(host))
        apply_button_style(self.edit_button, variant="neutral")
        layout.addWidget(self.edit_button)
        note = QLabel(tr("请先回到录制起点，保持地图缩放和资源筛选一致。\n"
                         "单轮执行；途中失败会停止。智能扫描与循环采集尚未开放。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.signals.progress.connect(self.status.setText)
        host.automation_state_changed.connect(self._state)
        host.user_changed.connect(lambda _: self.users.refresh_users())
        self.users.resolved_user_changed.connect(lambda _: self._state())
        self.reload_routes()

    def reload_routes(self):
        selected = self.routes.currentData()
        routes, errors = self.store.routes()
        self._routes = {route.key: route for route in routes}
        self.routes.blockSignals(True)
        self.routes.clear()
        for route in routes:
            self.routes.addItem(route.name, route.key)
        index = self.routes.findData(selected)
        if index >= 0:
            self.routes.setCurrentIndex(index)
        self.routes.blockSignals(False)
        if errors:
            self.status.setText("\n".join(errors))
        self._selected()

    def _selected(self):
        route = self._routes.get(self.routes.currentData())
        self.start_note.setText(
            tr("起点：{note}\n共 {count} 个采集点").format(note=route.start_note, count=len(route.steps))
            if route else tr("请从燕云菜单打开采集录制，保存第一条路线"))
        self._state()

    def _state(self, state=""):
        if state:
            self._automation_state = state
        state = getattr(self, "_automation_state", "idle")
        running = self._host.is_running
        hk = self._host._user_config.hotkeys
        self.run_button.setText(hotkey_label(tr("停止") if running else tr("开始采集"),
                                            hk.stop if running else hk.start))
        reason = _desktop_error(self._host)
        if not reason and not self.users.resolve_username():
            reason = tr("请选择有效的执行用户")
        if not reason and self.routes.currentData() not in self._routes:
            reason = tr("请先录制并保存路线")
        self.run_button.setEnabled(running or not reason)
        self.run_button.setToolTip(reason)
        apply_button_style(self.run_button, variant="danger" if running else "action")
        self.pause_button.setEnabled(running and state not in ("pausing", "stopping"))
        self.pause_button.setText(hotkey_label(tr("恢复") if state == "paused" else tr("暂停"), hk.pause))
        apply_button_style(
            self.pause_button,
            variant="action" if running and state == "paused" else "neutral",
        )
        if state == "stopping":
            self.run_button.setEnabled(False)
            self.run_button.setText(tr("结束中"))
        elif state == "pausing":
            self.pause_button.setText(tr("暂停中"))
        fit_button_width(self.run_button, self.pause_button)
        self.routes.setEnabled(not running)
        self.users.setEnabled(not running)

    def f9_run(self):
        if self._host.is_running:
            self._host.request_stop()
            return
        try:
            error = _desktop_error(self._host)
            if error:
                raise ValueError(error)
            route = deepcopy(self._routes.get(self.routes.currentData()))
            if route is None:
                raise ValueError(tr("请先录制并保存路线"))
            route.validate(runnable=True)
            if route.layout_key != self._host.layout_combo.currentData():
                raise ValueError(tr("当前布局与录制布局不同，请切回录制布局"))
            _start_task(self._host, route, self.signals, self.users.resolve_username())
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))


class GatherRecordingDialog(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.setWindowTitle(tr("采集录制（实验性）"))
        self.resize(640, 700)
        self.store = GatherStore()
        self.route = GatherRoute()
        self.previous: dict | None = None
        self.recorder: GatherInputRecorder | None = None
        self.hotkeys = None
        self.marked: GatherStep | None = None
        self._travel_started_at: float | None = None
        self._confirm_pending = False
        self.signals = GatherSignals(self)
        self.signals.mark.connect(self.mark)
        self.signals.travel.connect(self.travel)
        self.signals.failed.connect(self._recording_failed)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.saved = QComboBox()
        self.saved.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        row.addWidget(self.saved, 1)
        self.open_button = QPushButton(tr("打开"))
        self.open_button.clicked.connect(self._open)
        row.addWidget(self.open_button)
        self.new_button = QPushButton(tr("新建"))
        self.new_button.clicked.connect(self._new)
        apply_button_style(self.open_button, variant="neutral")
        apply_button_style(self.new_button, variant="action")
        fit_button_width(self.open_button, self.new_button)
        row.addWidget(self.new_button)
        layout.addLayout(row)
        self.form = QWidget()
        fields = QFormLayout(self.form)
        self.name = QLineEdit(self.route.name)
        self.origin = QLineEdit()
        self.origin.setPlaceholderText(tr("填写传送起点、资源和单人／多人页"))
        self.map_key = QLineEdit("M")
        self.auto_travel_key = QLineEdit("V")
        self.confirm_key = QLineEdit("F")
        self.gather_key = QLineEdit("1")
        self.timeout = QSpinBox()
        self.timeout.setRange(10, 1800)
        self.timeout.setValue(120)
        self.timeout.setSuffix(tr(" 秒"))
        self.gather_wait = QSpinBox()
        self.gather_wait.setRange(1, 120)
        self.gather_wait.setValue(6)
        self.gather_wait.setSuffix(tr(" 秒"))
        self.mark_key = QComboBox()
        self.travel_key = QComboBox()
        for combo in (self.mark_key, self.travel_key):
            combo.addItems([f"F{i}" for i in range(1, 9)])
        self.travel_key.setCurrentText("F2")
        for label, widget in (("路线名称", self.name), ("固定起点说明", self.origin),
                              ("打开地图按键", self.map_key),
                              ("自动识途按键", self.auto_travel_key),
                              ("确认识途按键", self.confirm_key),
                              ("采集按键", self.gather_key),
                              ("识途超时", self.timeout), ("采集动作等待", self.gather_wait),
                              ("确认标记快捷键", self.mark_key), ("识途试跑快捷键", self.travel_key)):
            fields.addRow(tr(label), widget)
        layout.addWidget(self.form)
        self.users = ExecutionUserSelector(host.user_manager)
        layout.addWidget(self.users)
        host.user_changed.connect(lambda _: self.users.refresh_users())
        hint = QLabel(tr("先传送到固定起点并打开资源地图。开始录制后，把鼠标悬停在目标图标上按 F1；"
                         "第一次按 F2 会自动执行 V、等待后按 F 并开始计时；到达后再按 F2 结束计时。"
                         "每次按键生成的回放操作都会立即显示在下方。\n"
                         "录制热键可能同时传给游戏，请选择不冲突的按键。"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = QHBoxLayout()
        self.record_button = QPushButton(tr("开始录制"))
        self.record_button.clicked.connect(self.toggle_recording)
        apply_button_style(self.record_button, variant="action")
        row.addWidget(self.record_button)
        row.addStretch()
        layout.addLayout(row)
        self.steps = QListWidget()
        layout.addWidget(self.steps, 1)
        row = QHBoxLayout()
        self.undo_button = QPushButton(tr("撤销末尾点"))
        self.undo_button.clicked.connect(self._undo)
        self.save_button = QPushButton(tr("保存路线"))
        self.save_button.clicked.connect(self._save)
        apply_button_style(self.undo_button, variant="danger")
        apply_button_style(self.save_button, variant="action")
        fit_button_width(self.undo_button, self.save_button)
        row.addWidget(self.undo_button)
        row.addStretch()
        row.addWidget(self.save_button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.host.automation_state_changed.connect(self._state)
        self.host.register_cleanup(self._stop_recording)
        self._reload_saved()
        self._refresh()

    def _read_form(self):
        self.route.name = self.name.text().strip()
        self.route.start_note = self.origin.text().strip()
        self.route.map_key = self.map_key.text().strip().upper()
        self.route.travel_key = self.auto_travel_key.text().strip().upper()
        self.route.confirm_key = self.confirm_key.text().strip().upper()
        self.route.gather_key = self.gather_key.text().strip().upper()
        self.route.travel_timeout = self.timeout.value()
        self.route.gather_seconds = self.gather_wait.value()

    def _reload_saved(self):
        selected = self.saved.currentData()
        routes, errors = self.store.routes()
        self._saved_routes = {route.key: route for route in routes}
        self.saved.clear()
        for route in routes:
            self.saved.addItem(route.name, route.key)
        index = self.saved.findData(selected)
        if index >= 0:
            self.saved.setCurrentIndex(index)
        if errors:
            self.status.setText("\n".join(errors))

    def _discard_allowed(self) -> bool:
        self._read_form()
        baseline = self.previous or asdict(GatherRoute(key=self.route.key))
        if asdict(self.route) == baseline:
            return True
        box = QMessageBox(QMessageBox.Icon.Question, tr("未保存的采集录制"),
                          tr("放弃当前未保存的草稿？"),
                          QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                          self)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return exec_styled_message_box(box) == QMessageBox.StandardButton.Discard

    def _load_form(self):
        self.name.setText(self.route.name)
        self.origin.setText(self.route.start_note)
        self.map_key.setText(self.route.map_key)
        self.auto_travel_key.setText(self.route.travel_key)
        self.confirm_key.setText(self.route.confirm_key)
        self.gather_key.setText(self.route.gather_key)
        self.timeout.setValue(int(self.route.travel_timeout))
        self.gather_wait.setValue(int(self.route.gather_seconds))
        self._refresh_steps()

    def _new(self):
        if self._discard_allowed():
            self.route = GatherRoute()
            self.previous = None
            self._load_form()

    def _open(self):
        route = self._saved_routes.get(self.saved.currentData())
        if route and self._discard_allowed():
            self.route = deepcopy(route)
            self.previous = asdict(route)
            self._load_form()

    def _save(self):
        try:
            self._read_form()
            self.route.validate(runnable=True)
            self.previous = self.store.save(self.route, self.previous)
            self._reload_saved()
            tab = getattr(self.host, "_gather_tab", None)
            if tab is not None:
                tab.reload_routes()
            self.status.setText(tr("路线已保存"))
        except (ValueError, OSError, RuntimeError) as exc:
            self._reload_saved()
            self.status.setText(str(exc))

    def _undo(self):
        if self.marked is not None:
            self.marked = None
            self._travel_started_at = None
            self._confirm_pending = False
            self._refresh_steps()
        elif self.route.steps:
            self.route.steps.pop()
            self._refresh_steps()

    def _refresh_steps(self):
        self.steps.clear()
        sequence = 1
        for step in self.route.steps:
            for line in gather_step_dsl(self.route, step):
                self.steps.addItem(f"{sequence}. {line}")
                sequence += 1
        if self.marked is not None:
            self.steps.addItem(f'{sequence}. press "{self.route.map_key}"')
            self.steps.addItem(
                f"{sequence + 1}. click ({self.marked.x:.6f}, {self.marked.y:.6f})")
            sequence += 2
            if self._confirm_pending or self._travel_started_at is not None:
                for line in (f'press "{self.route.travel_key}"', "wait 0.800",
                             f'press "{self.route.confirm_key}"'):
                    self.steps.addItem(f"{sequence}. {line}")
                    sequence += 1
        self._refresh()

    def mark(self):
        if self.recorder is None or self.host.is_running:
            return
        try:
            self._check_recording_context()
            if self.marked is not None:
                raise ValueError(tr("当前采集点尚未完成，请到达后再按 F2，然后录制下一点"))
            self.marked = self.recorder.mark_current()
            self.status.setText(tr("已记录当前鼠标坐标。按 F2 执行 V → F 并开始计时"))
            self._refresh_steps()
        except ValueError as exc:
            self.status.setText(str(exc))
        self._refresh()

    def _recording_failed(self, message):
        self._stop_recording()
        self.status.setText(message)

    def _stop_recording(self):
        if self.hotkeys is not None:
            self.hotkeys.stop()
            self.hotkeys = None
        if self.recorder is not None:
            self.recorder.stop()
            self.recorder = None
        self.marked = None
        self._travel_started_at = None
        self._confirm_pending = False
        self._refresh()

    def toggle_recording(self):
        if self.recorder:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        if self.host.is_running:
            return
        try:
            error = _desktop_error(self.host)
            if error:
                raise ValueError(error)
            self._read_form()
            self.route.validate()
            if not self.route.start_note:
                raise ValueError(tr("请先填写固定起点说明"))
            if not self.users.resolve_username():
                raise ValueError(tr("请选择有效的执行用户"))
            key = self.host.layout_combo.currentData()
            if self.route.steps and self.route.layout_key != key:
                raise ValueError(tr("请切回录制布局，或新建路线"))
            layout = self.host._layout_manager.load_layout(key)
            if layout is None:
                raise ValueError(tr("无法读取当前布局"))
            hk = self.host._user_config.hotkeys
            mark, travel = self.mark_key.currentText(), self.travel_key.currentText()
            if mark == travel or {mark, travel} & {hk.start, hk.pause, hk.stop, hk.record, "F5", "F6"}:
                raise ValueError(tr("录制热键不能重复，也不能与任务、脚本录制或燕云菜单热键冲突"))
            self.route.layout_key = key
            self.recorder = GatherInputRecorder(
                self.host._capture, layout, self.host._target_window,
                connected=self.host._backend_ready,
                failed=self.signals.failed.emit)
            self.recorder.start()

            def hotkey(signal):
                if self.recorder is not None and self.host._backend_ready():
                    signal.emit()

            self.hotkeys = start_global_hotkeys({
                hotkey_pynput_token(mark): lambda: hotkey(self.signals.mark),
                hotkey_pynput_token(travel): lambda: hotkey(self.signals.travel),
            })
            if self.hotkeys is None:
                raise ValueError(tr("无法注册录制热键，请检查输入监听权限"))
            self.status.setText(tr(
                "全局热键 {mark}/{travel} 已注册。鼠标悬停目标后按 {mark} 记录坐标"
            ).format(mark=mark, travel=travel))
            self._refresh()
        except (ValueError, RuntimeError, OSError, ImportError) as exc:
            self._recording_failed(str(exc))

    def travel(self):
        if self.host.is_running or self.marked is None or self._confirm_pending:
            return
        try:
            self._check_recording_context()
        except ValueError as exc:
            self._recording_failed(str(exc))
            return
        if self._travel_started_at is not None:
            self.marked.travel_seconds = max(0.1, time.monotonic() - self._travel_started_at)
            self.route.steps.append(self.marked)
            self.status.setText(tr("已记录到达时间 {seconds:.1f} 秒；回放时随后按 {key} 采集").format(
                seconds=self.marked.travel_seconds, key=self.route.gather_key))
            self.marked = None
            self._travel_started_at = None
            self._refresh_steps()
            return
        try:
            self._read_form()
            self._send_key(self.route.travel_key)
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))
            return
        self._confirm_pending = True
        self.status.setText(tr("已按 {travel}，等待后将自动按 {confirm}").format(
            travel=self.route.travel_key, confirm=self.route.confirm_key))
        self._refresh_steps()
        QTimer.singleShot(800, self._confirm_travel)

    def _send_key(self, key: str) -> None:
        normalized = normalize_pressable(key)
        input_ctrl = getattr(self.host, "_input", None)
        if input_ctrl is None:
            raise ValueError(tr("当前连接没有可用的输入后端"))
        input_ctrl.key_down(normalized)
        input_ctrl.key_up(normalized)

    def _confirm_travel(self) -> None:
        if self.recorder is None or self.marked is None or not self._confirm_pending:
            return
        try:
            self._check_recording_context()
            self._send_key(self.route.confirm_key)
            self._travel_started_at = time.monotonic()
            self.status.setText(tr("已按 {key} 并开始计时；到达后再按 F2").format(
                key=self.route.confirm_key))
        except (ValueError, RuntimeError) as exc:
            self.status.setText(str(exc))
        finally:
            self._confirm_pending = False
            self._refresh_steps()

    def _state(self, state):
        if self.host.is_running:
            self._stop_recording()
        elif self.recorder is not None:
            try:
                self._check_recording_context()
            except ValueError as exc:
                self._recording_failed(str(exc))
        self._refresh()

    def _check_recording_context(self):
        if self.recorder is None or not self.host._backend_ready():
            raise ValueError(tr("游戏连接已断开，请重新连接后开始选点"))

    def _refresh(self):
        busy = self.host.is_running
        recording = self.recorder is not None
        editable = not busy and not recording
        self.form.setEnabled(editable)
        self.users.setEnabled(editable)
        self.saved.setEnabled(editable)
        self.open_button.setEnabled(editable)
        self.new_button.setEnabled(editable)
        self.undo_button.setEnabled(editable and bool(self.route.steps or self.marked))
        self.save_button.setEnabled(editable and bool(self.route.steps))
        self.record_button.setEnabled(not busy and not _desktop_error(self.host))
        self.record_button.setToolTip(_desktop_error(self.host))
        self.record_button.setText(tr("结束录制") if recording else tr("开始录制"))
        apply_button_style(self.record_button, variant="danger" if recording else "action")

    def closeEvent(self, event):  # noqa: N802
        self._stop_recording()
        if self.host.is_running:
            # 其他任务运行中不弹模态确认，保留草稿待下次打开。
            event.accept()
            return
        if not self._discard_allowed():
            event.ignore()
            return
        # 关闭后重开从保存版本恢复，避免把已放弃草稿误当作保存内容。
        self.route = GatherRoute.from_dict(self.previous) if self.previous else GatherRoute()
        self._load_form()
        event.accept()

    def reject(self):
        # QDialog 默认的 Esc 直接隐藏窗口，不经过 closeEvent，必须统一清理路径。
        self.close()
