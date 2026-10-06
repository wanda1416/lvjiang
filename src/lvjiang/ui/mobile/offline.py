"""PC 离线任务控制页：配置单向同步，运行控制不依赖持续连接。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...constants import PROJECT_ROOT
from ...core.android.agent import connect_agent_diagnostic
from ...core.android.device import AdbDevice
from ...core.android.offline import sync_offline_bundle
from ...core.layout_config import load_layout_entries
from ...core.offline_bundle import build_offline_bundle
from ...i18n import tr
from ..button_styles import apply_button_style
from ..combo_box import AutoWidthComboBox, ComboWidthMode


class _OfflineWorker(QThread):
    done = pyqtSignal(object, str)
    progress = pyqtSignal(int, int)

    def __init__(self, serial: str, action: str, username: str, layout: str, task_id: str, parent=None):
        super().__init__(parent)
        self.serial, self.action = serial, action
        self.username, self.layout, self.task_id = username, layout, task_id

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        agent = None
        try:
            agent = connect_agent_diagnostic(AdbDevice(self.serial))
            if agent is None or agent.status.get("offline_protocol") != 1:
                raise RuntimeError(tr("无法连接离线执行接口，请先打开手机 App 并更新 APK"))
            data: dict[str, Any] = {"serial": self.serial}
            if self.action == "sync":
                with tempfile.TemporaryDirectory(prefix="lvjiang-offline-sync-") as temp:
                    archive = Path(temp) / "snapshot.zip"
                    build_offline_bundle(PROJECT_ROOT, archive, username=self.username, layout=self.layout)
                    data["operation"] = sync_offline_bundle(
                        agent, archive, cancelled=self.isInterruptionRequested, progress=self.progress.emit)
            elif self.action == "diagnostics":
                data["diagnostics"], _ = agent.call("offline_diagnostics", timeout=180)
            elif self.action != "status":
                data["operation"], _ = agent.call("offline_" + self.action, task_id=self.task_id)
            if not self.isInterruptionRequested():
                data["status"], _ = agent.call("offline_status")
                if data["status"].get("sync", {}).get("synced"):
                    data["tasks"], _ = agent.call("offline_tasks", timeout=60)
                else:
                    data["tasks"] = {"tasks": []}
            self.done.emit(data, "")
        except Exception as exc:
            self.done.emit({}, f"{type(exc).__name__}: {exc}")
        finally:
            if agent is not None:
                agent.close()


class OfflineControlPage(QWidget):
    def __init__(self, host, serial, track_worker, parent=None):
        super().__init__(parent)
        self._serial = serial
        self._track_worker = track_worker
        self._worker = None
        self._state = "idle"
        root = QVBoxLayout(self)
        hint = QLabel(tr(
            "PC 配置任务后，一键同步脚本、配置和最新 Profile DB 到手机。"
            "手机可断开 PC 独立执行；第一阶段不回传结果，下次同步会覆盖手机数据。"
        ))
        hint.setWordWrap(True)
        root.addWidget(hint)
        form = QFormLayout()
        self.user = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        manager = getattr(host, "user_manager", None)
        if manager is not None:
            for name in manager.list_users():
                self.user.addItem(name, name)
            index = self.user.findData(manager.get_active_user_name())
            if index >= 0:
                self.user.setCurrentIndex(index)
        self.layout_choice = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        for key, entry in load_layout_entries().items():
            self.layout_choice.addItem(entry.name, key)
        if host is not None and hasattr(host, "layout_combo"):
            index = self.layout_choice.findData(host.layout_combo.currentData())
            if index >= 0:
                self.layout_choice.setCurrentIndex(index)
        self.task = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        form.addRow(tr("同步执行用户"), self.user)
        form.addRow(tr("同步布局（请选择安卓布局）"), self.layout_choice)
        form.addRow(tr("手机任务"), self.task)
        root.addLayout(form)
        row = QHBoxLayout()
        self.buttons = {}
        for action, label in (
            ("sync", "一键同步"), ("status", "刷新状态"), ("diagnostics", "检查执行环境"),
            ("start", "启动任务"), ("pause", "暂停任务"), ("resume", "继续任务"), ("stop", "结束任务"),
        ):
            button = QPushButton(tr(label))
            apply_button_style(button, variant="action" if action == "sync" else "neutral")
            button.clicked.connect(lambda _checked=False, action=action: self._run(action))
            self.buttons[action] = button
            row.addWidget(button)
        root.addLayout(row)
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        root.addWidget(self.report, 1)
        self.task.currentIndexChanged.connect(lambda _: self._update_buttons())
        self._update_buttons()

    def _update_buttons(self) -> None:
        busy = self._worker is not None and self._worker.isRunning()
        active = self._state in {"running", "pausing", "paused", "stopping"}
        for button in self.buttons.values():
            button.setEnabled(not busy)
        self.buttons["sync"].setEnabled(not busy and not active and bool(self.user.currentData()))
        self.buttons["start"].setEnabled(not busy and not active and bool(self.task.currentData()))
        self.buttons["pause"].setEnabled(not busy and self._state == "running")
        self.buttons["resume"].setEnabled(not busy and self._state in {"pausing", "paused"})
        self.buttons["stop"].setEnabled(not busy and active and self._state != "stopping")
        self.user.setEnabled(not busy)
        self.layout_choice.setEnabled(not busy)

    def _run(self, action: str) -> None:
        serial = self._serial()
        if not serial:
            self.report.setPlainText(tr("请先选择在线设备"))
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if action == "sync" and QMessageBox.question(
            self, tr("同步到手机"),
            tr("将用 PC 配置和最新 DB 替换手机配置及数据，手机此前的离线结果不会回传。确认同步？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        worker = _OfflineWorker(
            serial, action, str(self.user.currentData() or ""),
            str(self.layout_choice.currentData() or ""), str(self.task.currentData() or ""), self)
        self._worker = worker
        self._track_worker(worker)
        worker.done.connect(self._done)
        worker.finished.connect(self._update_buttons)
        worker.progress.connect(lambda done, total: self.report.setPlainText(
            tr("正在传输：{done}/{total} KiB").format(done=done // 1024, total=total // 1024)))
        self.report.setPlainText(tr("正在操作…"))
        worker.start()
        self._update_buttons()

    def _done(self, data: dict, error: str) -> None:
        if error:
            self.report.setPlainText(error)
            return
        if data.get("serial") != self._serial():
            self.report.setPlainText(tr("上一设备操作已完成；当前设备已切换，请刷新状态"))
            return
        status = data.get("status", {})
        self._state = str(status.get("state", "idle"))
        previous = self.task.currentData()
        self.task.blockSignals(True)
        self.task.clear()
        for task in data.get("tasks", {}).get("tasks", []):
            self.task.addItem(task["name"], task["id"])
        index = self.task.findData(previous)
        if index >= 0:
            self.task.setCurrentIndex(index)
        self.task.blockSignals(False)
        sync = status.get("sync", {})
        lines = [tr("任务状态：{state}").format(state={
            "idle": tr("空闲"), "running": tr("运行中"), "pausing": tr("正在暂停"),
            "paused": tr("已暂停"), "stopping": tr("正在结束"), "done": tr("已完成"),
            "failed": tr("失败"), "stopped": tr("已结束"),
        }.get(self._state, self._state)), str(status.get("message", ""))]
        if sync.get("synced"):
            lines.append(tr("手机执行用户：{user}；最近同步：{time}").format(
                user=sync.get("username", ""), time=sync.get("synced_at", "")))
        lines.extend(status.get("logs", [])[-8:])
        if data.get("diagnostics"):
            lines.append(json.dumps(data["diagnostics"], ensure_ascii=False, indent=2))
        self.report.setPlainText("\n".join(lines))

    def device_changed(self) -> None:
        self._state = "idle"
        self.task.clear()
        self.report.setPlainText(tr("设备已切换，请刷新离线任务状态"))
        self._update_buttons()
