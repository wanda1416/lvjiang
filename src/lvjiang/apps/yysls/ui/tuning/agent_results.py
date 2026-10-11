"""从生成结果启动，保持目标方案覆盖与 MCP 启动一致。"""
from __future__ import annotations

import uuid

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from .....core.agent_mcp import LV1_REQUIRED_MESSAGE, has_agent_access
from .....i18n import tr
from .....ui.button_styles import apply_button_style
from .....ui.combo_box import AutoWidthComboBox, ComboWidthMode


class AgentResultsDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("智能调律生成方案"))
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        self.results = AutoWidthComboBox(width_mode=ComboWidthMode.STRETCH)
        self.results.currentIndexChanged.connect(self._show_result)
        layout.addWidget(self.results)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        refresh = QPushButton(tr("刷新生成方案"))
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        self.start = QPushButton(tr("启动选中方案"))
        self.start.clicked.connect(self._start)
        buttons.addWidget(self.start)
        layout.addLayout(buttons)
        apply_button_style(refresh, variant="neutral")
        apply_button_style(self.start, variant="action")

    def _check_access(self):
        if not has_agent_access():
            raise PermissionError(tr(LV1_REQUIRED_MESSAGE))
        self.service._ensure_enabled()

    def refresh(self):
        try:
            self._check_access()
            current = self.results.currentData() or {}
            selected = -1
            with QSignalBlocker(self.results):
                self.results.clear()
                for user in self.service.list_users()["users"]:
                    for record in self.service.list_generated_tuning(user)["results"]:
                        self.results.addItem(f"{user} · {record['name']} · {record['created_at']}", record)
                        if record["id"] == current.get("id"):
                            selected = self.results.count() - 1
                self.results.setCurrentIndex(selected if selected >= 0 else 0)
            self._show_result()
        except (ValueError, OSError) as exc:
            self.details.clear()
            self.status.setText(str(exc))
            self.start.setEnabled(False)

    def _show_result(self):
        try:
            self._check_access()
            self._render_result()
        except (ValueError, OSError) as exc:
            self.details.clear()
            self.status.setText(str(exc))
            self.start.setEnabled(False)

    def _render_result(self):
        record = self.results.currentData()
        self.start.setEnabled(False)
        if not record:
            self.details.clear()
            self.status.setText(tr("还没有生成方案，请先在 Agent 中生成。"))
            return
        prepared = self.service.prepare_generated_tuning(record["user"], record["id"])
        plans = prepared["smart_state"]["plans"]
        smart = prepared["smart_tuning"]
        lines = [tr("执行用户：") + record["user"], tr("目标：") + record["goal"]]
        if not smart["required"]:
            lines.append(tr("普通规则调律：此基础组未启用毕业率判定。"))
        else:
            infos = smart["plans"]
            participating = list(dict.fromkeys(info["plan_id"] for info in infos if info["status"] == "ready"))
            lines.append(tr("实际参与智能判定的方案："))
            for plan_id in participating:
                plan = plans[plan_id]
                lines.append(" · ".join(str(plan.get(key) or "—") for key in
                                        ("name", "playstyle", "graduation_scheme", "base_attribute")))
            if not participating:
                lines.append(tr("无"))
            excluded = [f"{info['plan_name']} · {info['rule_name']}：{info['reason']}"
                        for info in infos if info["status"] != "ready"]
            mentioned = {info["plan_id"] for info in infos}
            excluded.extend(f"{plan['name']}：" + tr("未匹配本次规则玩法")
                            for plan_id, plan in plans.items() if plan_id not in mentioned)
            if excluded:
                lines.extend([tr("未参与的方案或规则及原因："), *excluded])
        lines.extend([tr("培养建议："), *record["suggestions"],
                      tr("启动时会再次核对判定范围，并执行当前选中的设备；不会改变默认选择。")])
        self.details.setPlainText("\n".join(lines))
        self.start.setEnabled(prepared["ready"])
        self.status.setText("" if prepared["ready"] else smart["reason"])

    def _start(self):
        try:
            self._check_access()
            record = self.results.currentData()
            if not record:
                raise ValueError(tr("请选择生成方案"))
            task = self.service.start_auto_tuning(record["user"], record["id"], uuid.uuid4().hex)
            self.status.setText(tr("调律已启动，请在调律管理中查看进度。") if task.get("task_id") else tr("未能启动调律"))
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))
