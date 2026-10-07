"""PC 离线任务控制页：配置单向同步，运行控制不依赖持续连接。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
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
from ...core.android.offline import require_offline_sync_access, sync_offline_bundle
from ...core.layout_config import load_layout_entries
from ...core.license import has_feature
from ...core.offline_bundle import build_offline_bundle
from ...i18n import tr
from ..button_styles import apply_button_style
from ..combo_box import AutoWidthComboBox, ComboWidthMode


class _OfflineWorker(QThread):
    done = pyqtSignal(object, str)
    progress = pyqtSignal(int, int)

    def __init__(self, serial: str, action: str, username: str, layout: str, task_id: str, parent=None,
                 *, preserve_task_params: bool = True):
        super().__init__(parent)
        self.serial, self.action = serial, action
        self.username, self.layout, self.task_id = username, layout, task_id
        self.preserve_task_params = preserve_task_params

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        agent = None
        try:
            if self.action == "sync":
                require_offline_sync_access()
            agent = connect_agent_diagnostic(AdbDevice(self.serial))
            if agent is None or agent.status.get("offline_protocol") not in (1, 2):
                raise RuntimeError(tr("无法连接离线执行接口，请先打开手机 App 并更新 APK"))
            data: dict[str, Any] = {"serial": self.serial}
            if self.action == "sync":
                if agent.status.get("offline_protocol") != 2:
                    raise RuntimeError(tr("请先更新 APK：旧版本不支持保留手机任务参数"))
                with tempfile.TemporaryDirectory(prefix="lvjiang-offline-sync-") as temp:
                    archive = Path(temp) / "snapshot.zip"
                    build_offline_bundle(PROJECT_ROOT, archive, username=self.username, layout=self.layout)
                    data["operation"] = sync_offline_bundle(
                        agent, archive, cancelled=self.isInterruptionRequested, progress=self.progress.emit,
                        preserve_task_params=self.preserve_task_params)
            elif self.action == "diagnostics":
                data["diagnostics"], _ = agent.call("offline_diagnostics", timeout=180)
            elif self.action != "status":
                data["operation"], _ = agent.call("offline_" + self.action, task_id=self.task_id,
                                                 username=self.username if self.action == "start" else "")
            if not self.isInterruptionRequested():
                data["status"], _ = agent.call("offline_status")
                if data["status"].get("sync", {}).get("ready") or data["status"].get("sync", {}).get("synced"):
                    data["tasks"], _ = agent.call("offline_tasks", timeout=60)
                    data["users"], _ = agent.call("offline_users")
                else:
                    data["tasks"] = {"tasks": []}
                    data["users"] = {"users": []}
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
        self._host = host
        root = QVBoxLayout(self)
        hint = QLabel(tr(
            "PC 配置任务后，一键同步脚本、配置和最新 Profile DB 到手机。"
            "手机可断开 PC 独立执行；第一阶段不回传结果，下次同步会覆盖手机数据。"
        ))
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.user = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        sync_group = QGroupBox(tr("同步配置"))
        sync_layout = QVBoxLayout(sync_group)
        sync_form = QFormLayout()
        self.layout_choice = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        for key, entry in load_layout_entries().items():
            self.layout_choice.addItem(entry.name, key)
        if host is not None and hasattr(host, "layout_combo"):
            index = self.layout_choice.findData(host.layout_combo.currentData())
            if index >= 0:
                self.layout_choice.setCurrentIndex(index)
        self.task = AutoWidthComboBox(width_mode=ComboWidthMode.POPUP)
        sync_form.addRow(tr("同步布局（请选择安卓布局）"), self.layout_choice)
        sync_layout.addLayout(sync_form)
        scope = QLabel(tr("下发全部用户、任务配置和最新 DB，仅携带所选布局及其继承依赖。用户与任务在执行时选择。"))
        scope.setWordWrap(True)
        sync_layout.addWidget(scope)
        self.preserve_task_params = QCheckBox(tr("保留手机任务参数"))
        self.preserve_task_params.setChecked(True)
        self.preserve_task_params.setToolTip(tr("保留各用户的任务设置（含自动调律）和手机独有用户；脚本、布局和 Profile DB 仍更新。"))
        sync_layout.addWidget(self.preserve_task_params)
        root.addWidget(sync_group)
        execution_group = QGroupBox(tr("远程执行"))
        execution_layout = QVBoxLayout(execution_group)
        form = QFormLayout()
        form.addRow(tr("执行用户（手机资料）"), self.user)
        form.addRow(tr("执行任务"), self.task)
        execution_layout.addLayout(form)
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
            if action == "sync":
                sync_layout.addWidget(button)
            else:
                row.addWidget(button)
        execution_layout.addLayout(row)
        root.addWidget(execution_group)
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        root.addWidget(self.report, 1)
        self.task.currentIndexChanged.connect(lambda _: self._update_buttons())
        self.user.currentIndexChanged.connect(lambda _: self._update_buttons())
        self.layout_choice.currentIndexChanged.connect(lambda _: self._update_buttons())
        self._update_buttons()

    def _update_buttons(self) -> None:
        busy = self._worker is not None and self._worker.isRunning()
        active = self._state in {"running", "pausing", "paused", "stopping"}
        for button in self.buttons.values():
            button.setEnabled(not busy)
        sync_allowed = has_feature("lv1")
        self.buttons["sync"].setEnabled(sync_allowed and not busy and not active
                                        and bool(self.layout_choice.currentData()) and bool(self._sync_username()))
        self.buttons["sync"].setToolTip(
            "" if sync_allowed else tr("向手机下发配置需要激活 Lv1，请在设置的「功能激活」中激活"))
        self.buttons["start"].setEnabled(not busy and not active and bool(self.task.currentData())
                                         and bool(self.user.currentData()))
        self.buttons["pause"].setEnabled(not busy and self._state == "running")
        self.buttons["resume"].setEnabled(not busy and self._state in {"pausing", "paused"})
        self.buttons["stop"].setEnabled(not busy and active and self._state != "stopping")
        self.user.setEnabled(not busy and not active)
        self.task.setEnabled(not busy and not active)
        self.layout_choice.setEnabled(not busy and not active)
        self.preserve_task_params.setEnabled(not busy and not active)

    def _sync_username(self) -> str:
        manager = getattr(self._host, "user_manager", None)
        if manager is not None:
            return str(manager.get_active_user_name() or "")
        from ...core.config.session import get_session_store
        return str(get_session_store().get_active("user", "") or "")

    def _run(self, action: str) -> None:
        if action == "sync":
            try:
                require_offline_sync_access()
            except PermissionError as exc:
                self.report.setPlainText(tr(str(exc)))
                self._update_buttons()
                return
        serial = self._serial()
        if not serial:
            self.report.setPlainText(tr("请先选择在线设备"))
            return
        if self._worker is not None and self._worker.isRunning():
            return
        if action == "sync" and QMessageBox.question(
            self, tr("同步到手机"),
            tr("将更新脚本、布局和用户数据，保留手机任务参数及手机独有用户；Profile DB 仍由 PC 快照覆盖，离线结果不会回传。确认同步？")
            if self.preserve_task_params.isChecked() else
            tr("将用 PC 配置和最新 DB 替换手机配置及数据，包括手机任务参数和用户名册；手机此前的离线结果不会回传。确认同步？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        worker = _OfflineWorker(
            serial, action, self._sync_username() if action == "sync" else str(self.user.currentData() or ""),
            str(self.layout_choice.currentData() or ""), str(self.task.currentData() or ""), self,
            preserve_task_params=self.preserve_task_params.isChecked())
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
        previous_user = self.user.currentData()
        self.user.blockSignals(True)
        self.user.clear()
        for name in data.get("users", {}).get("users", []):
            self.user.addItem(name, name)
        current_user = data.get("users", {}).get("selected", "")
        selected_user = current_user if self._state in {"running", "pausing", "paused", "stopping"} else previous_user or current_user
        index = self.user.findData(selected_user)
        if index >= 0:
            self.user.setCurrentIndex(index)
        self.user.blockSignals(False)
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
        }.get(self._state, self._state))]
        message = str(status.get("message", "")).strip()
        if message and not (self._state == "running" and message in {"执行中", "运行中"}):
            lines.append(message)
        if sync.get("synced"):
            lines.append(tr("手机执行用户：{user}；最近同步：{time}").format(
                user=sync.get("execution_username", sync.get("username", "")), time=sync.get("synced_at", "")))
        lines.extend(status.get("logs", [])[-8:])
        if data.get("diagnostics"):
            lines.append(json.dumps(data["diagnostics"], ensure_ascii=False, indent=2))
        self.report.setPlainText("\n".join(lines))
        self._update_buttons()

    def device_changed(self) -> None:
        self._state = "idle"
        self.task.clear()
        self.user.clear()
        self.report.setPlainText(tr("设备已切换，请刷新离线任务状态"))
        self._update_buttons()
