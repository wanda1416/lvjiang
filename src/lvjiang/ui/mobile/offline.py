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
from ...core.android.history_transfer import pull_history
from ...core.android.offline import require_offline_sync_access, sync_offline_bundle
from ...core.history_transfer import history_channels
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
                 *, preserve_task_params: bool = True, preserve_batch_config: bool = True, history_selected: list[str] | None = None):
        super().__init__(parent)
        self.serial, self.action = serial, action
        self.username, self.layout, self.task_id = username, layout, task_id
        self.preserve_task_params = preserve_task_params
        self.preserve_batch_config = preserve_batch_config
        self.history_selected = history_selected or []

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        agent = None
        data: dict[str, Any] = {"serial": self.serial}
        try:
            if self.action == "sync":
                require_offline_sync_access()
            agent = connect_agent_diagnostic(AdbDevice(self.serial))
            if agent is None or agent.status.get("offline_protocol") not in (1, 2, 3, 4):
                raise RuntimeError(tr("无法连接离线执行接口，请先打开手机 App 并更新 APK"))
            if self.action == "sync":
                if agent.status.get("offline_protocol") != 4:
                    raise RuntimeError(tr("请先更新 APK：旧版本不支持独立保留任务参数和批量配置"))
                with tempfile.TemporaryDirectory(prefix="lvjiang-offline-sync-") as temp:
                    archive = Path(temp) / "snapshot.zip"
                    build_offline_bundle(PROJECT_ROOT, archive, username=self.username, layout=self.layout)
                    data["operation"] = sync_offline_bundle(
                        agent, archive, cancelled=self.isInterruptionRequested, progress=self.progress.emit,
                        preserve_task_params=self.preserve_task_params, preserve_batch_config=self.preserve_batch_config)
            elif self.action == "history":
                data["history"] = pull_history(
                    agent, PROJECT_ROOT, self.history_selected,
                    cancelled=self.isInterruptionRequested, progress=self.progress.emit)
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
            if "history" in data:
                data["refresh_error"] = str(exc)
                self.done.emit(data, "")
            else:
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
            "手机内置任务，可独立配置和执行，无需先连接 PC。"
            "可向手机下发脚本、配置和 Profile，也可独立回传手机历史与日志。"
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
        scope = QLabel(tr("下发全部用户、任务配置和 Profile，仅携带所选布局及其继承依赖；保留手机历史、日志和结果。"))
        scope.setWordWrap(True)
        sync_layout.addWidget(scope)
        self.preserve_task_params = QCheckBox(tr("保留手机任务参数"))
        self.preserve_task_params.setChecked(True)
        self.preserve_task_params.setToolTip(tr("保留各用户的任务设置（含自动调律）和手机独有用户；脚本、布局和 Profile DB 仍更新。"))
        preservation_row = QHBoxLayout()
        preservation_row.addWidget(self.preserve_task_params)
        self.preserve_batch_config = QCheckBox(tr("保留手机批量配置"))
        self.preserve_batch_config.setChecked(True)
        self.preserve_batch_config.setToolTip(tr("保留手机整份批量配置组；取消后使用电脑配置组。本次运行草稿仍保留。"))
        preservation_row.addWidget(self.preserve_batch_config)
        preservation_row.addStretch()
        sync_layout.addLayout(preservation_row)
        root.addWidget(sync_group)
        history_group = QGroupBox(tr("回传到电脑"))
        history_layout = QVBoxLayout(history_group)
        history_hint = QLabel(tr("仅合并已结束的手机记录；参数、Profile 和装备数据不回传。重复回传不会重复导入。"))
        history_hint.setWordWrap(True)
        history_layout.addWidget(history_hint)
        history_row = QHBoxLayout()
        self.history_choices = {}
        for key, label in [(key, channel.label) for key, channel in history_channels().items()] + [("logs", "任务日志")]:
            check = QCheckBox(tr(label))
            check.setChecked(True)
            check.toggled.connect(self._save_history_choices)
            self.history_choices[key] = check
            history_row.addWidget(check)
        history_row.addStretch()
        history_layout.addLayout(history_row)
        self._load_history_choices()
        root.addWidget(history_group)
        execution_group = QGroupBox(tr("远程执行"))
        execution_layout = QVBoxLayout(execution_group)
        form = QFormLayout()
        form.addRow(tr("执行用户（手机资料）"), self.user)
        form.addRow(tr("执行任务"), self.task)
        execution_layout.addLayout(form)
        row = QHBoxLayout()
        self.buttons = {}
        for action, label in (
            ("sync", "下发配置"), ("history", "回传历史与日志"), ("status", "刷新状态"), ("diagnostics", "检查执行环境"),
            ("start", "启动任务"), ("pause", "暂停任务"), ("resume", "继续任务"), ("stop", "结束任务"),
        ):
            button = QPushButton(tr(label))
            apply_button_style(button, variant="action" if action == "sync" else "neutral")
            button.clicked.connect(lambda _checked=False, action=action: self._run(action))
            self.buttons[action] = button
            if action == "sync":
                sync_layout.addWidget(button)
            elif action == "history":
                history_layout.addWidget(button)
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
        from ...core.access import is_readonly
        # 回传只导出终态历史，运行中的任务会被导出查询跳过，因此任务运行中也可用。
        self.buttons["history"].setEnabled(
            not busy and not is_readonly()
            and any(check.isChecked() for check in self.history_choices.values()))
        self.buttons["history"].setToolTip(tr("请在主节点回传历史") if is_readonly() else "")
        for check in self.history_choices.values():
            check.setEnabled(not busy and not is_readonly())
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
        self.preserve_batch_config.setEnabled(not busy and not active)

    def _history_preferences(self):
        from ...core.config.session import SessionStore
        from ...core.paths import session_directory
        return SessionStore(session_directory() / "history-transfer" / "preferences.json")

    def _load_history_choices(self) -> None:
        choices = self._history_preferences().get_node(str(self._serial() or ""), {})
        for key, check in self.history_choices.items():
            check.blockSignals(True)
            check.setChecked(bool(choices.get(key, True)))
            check.blockSignals(False)

    def _save_history_choices(self) -> None:
        serial = self._serial()
        if serial:
            self._history_preferences().update_node(str(serial), {
                key: check.isChecked() for key, check in self.history_choices.items()})
        self._update_buttons()

    def _sync_username(self) -> str:
        manager = getattr(self._host, "user_manager", None)
        if manager is not None:
            return str(manager.get_active_user_name() or "")
        from ...core.user_config import resolve_active_username
        return str(resolve_active_username() or "")

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
            "\n".join([
                tr("将更新脚本、布局和用户数据，Profile 由 PC 快照覆盖。"),
                tr("保留手机任务参数及手机独有用户。") if self.preserve_task_params.isChecked()
                else tr("用 PC 配置覆盖手机任务参数和用户名册。"),
                tr("保留手机批量配置。") if self.preserve_batch_config.isChecked()
                else tr("用 PC 配置覆盖手机批量配置。"),
                tr("手机历史、日志和结果保留；回传需单独操作。确认下发？"),
            ]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        worker = _OfflineWorker(
            serial, action, self._sync_username() if action == "sync" else str(self.user.currentData() or ""),
            str(self.layout_choice.currentData() or ""), str(self.task.currentData() or ""), self,
            preserve_task_params=self.preserve_task_params.isChecked(),
            preserve_batch_config=self.preserve_batch_config.isChecked(),
            history_selected=[key for key, check in self.history_choices.items() if check.isChecked()])
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
        elif sync.get("ready"):
            lines.append(tr("使用应用预置配置；执行用户：{user}").format(user=sync.get("execution_username", "")))
        if "history" in data:
            outcome = data["history"]
            labels = {key: channel.label for key, channel in history_channels().items()}
            for key, count in outcome["imported"].items():
                lines.append(tr("{label}：新增 {count} 条记录").format(label=tr(labels[key]), count=count))
            lines.append(tr("新增附件：{count} 个").format(count=outcome["files"]))
            for key, message in outcome["errors"].items():
                lines.append(tr("{label}回传失败：{message}").format(label=tr(labels[key]), message=message))
            if outcome["missing"]:
                lines.append(tr("手机缺失 {count} 个附件，相关历史不完整；请检查手机原文件。").format(count=len(outcome["missing"])))
            for key, count in outcome["unattributed"].items():
                lines.append(tr("{label}有 {count} 条旧记录缺少可确认的手机来源，未导入；原记录保留在手机。").format(
                    label=tr(labels[key]), count=count))
            if not outcome["errors"] and not outcome["missing"] and not outcome["unattributed"]:
                lines.append(tr("回传完成；重复记录已跳过，手机原件保留。"))
        if data.get("refresh_error"):
            lines.append(tr("回传已处理，但刷新手机状态失败：{message}").format(message=data["refresh_error"]))
        lines.extend(status.get("logs", [])[-8:])
        if data.get("diagnostics"):
            lines.append(json.dumps(data["diagnostics"], ensure_ascii=False, indent=2))
        self.report.setPlainText("\n".join(lines))
        self._update_buttons()

    def device_changed(self) -> None:
        self._load_history_choices()
        self._state = "idle"
        self.task.clear()
        self.user.clear()
        self.report.setPlainText(tr("设备已切换，请刷新离线任务状态"))
        self._update_buttons()
