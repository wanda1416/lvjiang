"""External Agent connection and generated tuning results; Qt owns task launching."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import Future, TimeoutError

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..... import constants
from .....core.agent_docs import AgentDocuments
from .....core.agent_mcp import LV1_REQUIRED_MESSAGE, LocalMCPServer, has_agent_access
from .....i18n import tr
from .....ui.combo_box import AutoWidthComboBox
from .....ui.execution_user_selector import ExecutionUserSelector
from ...core.agent.service import AgentGrant, AgentService, revision


class AgentUserSelector(ExecutionUserSelector):
    """Authorization is a fixed user, not a view that follows the main editor."""

    def __init__(self, user_manager, parent=None):
        self._initial_user = user_manager.get_active_user_name()
        super().__init__(user_manager, parent)

    def refresh_users(self) -> None:
        selected = (self.combo.currentData() if self.combo.count()
                    else self._initial_user)
        self._initial_user = ""
        self.combo.blockSignals(True)
        self.combo.clear()
        for username in self._user_manager.list_users():
            self.combo.addItem(str(username), str(username))
        self.combo.setCurrentIndex(self.combo.findData(selected))
        self.combo.blockSignals(False)

    def resolve_username(self) -> str:
        selected = self.combo.currentData()
        return str(selected) if selected in self._user_manager.list_users() else ""


class AgentTaskBridge(QObject):
    request = pyqtSignal(object)

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.tasks: dict = {}
        self.requests: dict = {}
        self.service: AgentService | None = None
        self._pending: set[Future] = set()
        self._pending_lock = threading.Lock()
        self.closed = False
        self.request.connect(self._handle)

    def call(self, operation: str, arguments: dict) -> dict:
        future: Future = Future()
        with self._pending_lock:
            if self.closed:
                raise ValueError("律匠正在关闭")
            self._pending.add(future)
        self.request.emit((operation, arguments, future))
        try:
            return future.result(timeout=30)
        except TimeoutError:
            future.cancel()
            raise ValueError("律匠主界面未响应，请检查程序状态") from None
        finally:
            with self._pending_lock:
                self._pending.discard(future)

    def close(self):
        with self._pending_lock:
            self.closed = True
            for future in self._pending:
                future.cancel()

    def _handle(self, request):
        operation, arguments, future = request
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(self.perform(operation, arguments))
        except Exception as exc:  # noqa: BLE001 - resolve every cross-thread request
            future.set_exception(exc)

    def perform(self, operation: str, args: dict) -> dict:
        host = self.host
        if operation == "reload_rules":
            from ...core.tuning_rules import (
                get_tuning_group_manager,
                get_tuning_rule_manager,
            )
            get_tuning_rule_manager().reload()
            get_tuning_group_manager().reload()
            return {"updated": True}
        if operation == "targets":
            return {"targets": [{"id": target.id, "name": target.display_name,
                                  "kind": target.kind, "ready": target.ready,
                                  "selected": target.id == host._execution_targets.active_target_id}
                                 for target in host._execution_targets.all()
                                 if target.id == args["target_id"]]}
        if operation == "validate":
            from ...core.agent.launch import prepare_tuning
            prepare_tuning(args["config"])
            target = host._current_execution_target()
            if target is None or target.id != args["target_id"] or not target.ready:
                raise ValueError("请先在律匠连接并选中已授权的执行目标")
            decision = host._run_manager.can_start(target_id=target.id, username=args["user"])
            if not decision.allowed:
                raise ValueError(decision.reason)
            if not host._backend_ready() or not host._plan_allows_backend():
                raise ValueError("当前连接方案或执行环境不可用")
            from .tuning_tab import TuningTab
            if TuningTab._missing_tuning_output_fields():
                raise ValueError("当前图库缺少调律必需字段，请先完成标定")
            return {"ready": True}
        if operation == "task":
            task = self.tasks.get(args["task_id"])
            if task is None and self.service is not None and args["action"] == "status":
                self.service._check(args["user"])
                saved = self.service.store.load("tasks").get(args["task_id"])
                if saved and saved["user"] == args["user"]:
                    terminal = saved["state"] in {"completed", "failed", "interrupted"}
                    return {**saved, "state": saved["state"] if terminal else "unknown",
                            "note": "未自动恢复旧任务；状态未知时请查看任务历史。"}
            if task is None or task["user"] != args["user"]:
                raise ValueError("没有本连接启动的任务")
            run = task["run"]
            action = args["action"]
            if self.service is not None:
                grant = self.service._check(args["user"], "read_data" if action == "status" else (
                    "scan" if task["kind"] == "scan" else "execute"))
                if action != "status" and grant.target_id != run.target_id:
                    raise PermissionError("该任务目标已不在授权范围")
            active = host._run_manager.run(run.task_run_id)
            if action != "status":
                if active is None:
                    raise ValueError("任务已经结束")
                if host._current_run_context is not active:
                    raise ValueError("请先在律匠中选中该任务的执行目标再控制")
                if action == "stop":
                    host._request_stop(stop_confirmed=True)
                elif action == "pause":
                    host._request_pause()
                elif action == "resume":
                    if getattr(run.ui_helper, "_active_dialog", None) is not None:
                        raise ValueError("请先在律匠处理人工提示")
                    if str(active.state) != "paused":
                        raise ValueError("任务当前未暂停")
                    host._on_pause_resume()
            result = {"task_id": run.task_run_id, "state": str(run.state), "kind": task["kind"]}
            if active is not None and getattr(run.ui_helper, "_active_dialog", None) is not None:
                result.update(state="waiting_user", reason="请在律匠处理当前提示，停止入口仍可用")
            if active is None and task["kind"] == "scan":
                variables = getattr(run.engine, "variables", {})
                result["scan"] = {key: copy.deepcopy(variables.get(key)) for key in (
                    "scan_results", "completed", "attempted", "skipped_existing", "skipped_base_attrs")}
                result["coverage"] = "visible_game_plan_grid"
            return result
        if operation not in {"scan", "start_tuning"}:
            raise ValueError("不支持的任务操作")
        if self.service is not None:
            grant = self.service._check(args["user"], "scan" if operation == "scan" else "execute")
            if grant.target_id != args["target_id"]:
                raise PermissionError("执行目标授权已改变，请重新预检")
        if not args.get("request_id"):
            raise ValueError("启动必须提供幂等请求 ID")
        request_key = (args["user"], args["request_id"])
        digest = revision([operation, {k: v for k, v in args.items() if k != "smart_state"}])
        if request_key in self.requests:
            old_digest, result = self.requests[request_key]
            if old_digest != digest:
                raise ValueError("启动请求 ID 已用于不同内容")
            return result
        if self.service is not None:
            for saved in self.service.store.load("tasks").values():
                if saved["user"] == args["user"] and saved["request_id"] == args["request_id"]:
                    if saved["digest"] != digest:
                        raise ValueError("启动请求 ID 已用于不同内容")
                    return {"task_id": saved["task_id"], "state": saved["state"], "kind": saved["kind"],
                            "note": "返回原任务，未重新启动。"}
            if operation == "start_tuning":
                # Recheck after queued UI edits/revocation, immediately before launch.
                args = {**args, **self.service.validate_auto_tuning(args["user"], args["result_id"])}
        target = host._current_execution_target()
        if target is None or target.id != args["target_id"] or not target.ready:
            raise ValueError("请先在律匠中连接并选中已授权的执行目标")
        if host._run_manager.run_for_target(target.id) is not None:
            raise ValueError("执行目标正在运行任务")
        if args["user"] not in host.user_manager.list_users():
            raise ValueError("执行用户已不存在")
        if operation == "scan":
            from .....core.config.resolver import get_resolver
            from .....workflows.metadata import build_flow_config
            path = get_resolver().resolve_read("workflows/scan_all_loadouts.wf")
            if path is None:
                raise ValueError("全部备战方案扫描未安装")
            config = build_flow_config(path)
            host._on_run_workflow(flow_config=config, execution_username=args["user"],
                                  parameter_overrides={"skip_existing": args["skip_existing"],
                                                       "skip_existing_base_attrs": False})
        else:
            from .tuning_tab import TuningTab
            page = next((host._left_tabs.widget(i) for i in range(host._left_tabs.count())
                         if isinstance(host._left_tabs.widget(i), TuningTab)), None)
            if page is None:
                raise ValueError("调律页面不可用")
            from ...core.loadout.models import LoadoutState
            page._start_tuning(execution_username=args["user"], config=args["config"],
                               smart_state=LoadoutState.from_dict(args["smart_state"]))
        run = host._current_run_context
        if run is None or not host._run_manager.run(run.task_run_id):
            raise ValueError("任务启动被预检拒绝，请查看律匠提示")
        kind = "scan" if operation == "scan" else "tuning"
        self.tasks[run.task_run_id] = {"run": run, "user": args["user"], "kind": kind}
        result = {"task_id": run.task_run_id, "state": str(run.state), "kind": kind}
        self.requests[request_key] = (digest, result)
        if self.service is not None:
            saved = {**result, "user": args["user"], "target_id": args["target_id"],
                     "request_id": args["request_id"], "digest": digest}
            self.service.store.mutate("tasks", lambda records: {**records, run.task_run_id: saved})

            def complete():
                data = {**saved, "state": str(run.state)}
                if kind == "scan":
                    variables = getattr(run.engine, "variables", {})
                    data["scan"] = {key: copy.deepcopy(variables.get(key)) for key in (
                        "scan_results", "completed", "attempted", "skipped_existing", "skipped_base_attrs")}
                    data["coverage"] = "visible_game_plan_grid"
                try:
                    self.service.store.mutate("tasks", lambda records: {**records, run.task_run_id: data})
                except (OSError, ValueError) as exc:
                    host.append_log(tr("Agent 任务状态保存失败：") + str(exc))
            run.worker.finished.connect(complete)
            if not run.worker.isRunning():
                QTimer.singleShot(0, complete)
        return result


class AgentTuningPage(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.bridge = AgentTaskBridge(host)
        self.service = AgentService(constants.PROJECT_ROOT, dispatch=self.bridge.call)
        self.bridge.service = self.service
        self._records: dict = {}
        self.server: LocalMCPServer | None = None
        layout = QVBoxLayout(self)
        description = QLabel(tr(
            "在 WorkBuddy 等外部 Agent 中交流流派和养成目标，扫描备战方案，"
            "分析装备并生成新的调律配置，再回到这里一键启动。"))
        description.setWordWrap(True)
        layout.addWidget(description)
        self.user = AgentUserSelector(host.user_manager)
        layout.addWidget(self.user)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel(tr("授权执行目标：")))
        self.target = AutoWidthComboBox(width_mode="popup")
        target_row.addWidget(self.target, 1)
        refresh = QPushButton(tr("刷新目标"))
        refresh.clicked.connect(self._refresh_targets)
        target_row.addWidget(refresh)
        layout.addLayout(target_row)
        self.permissions = {}
        for key, label in (("read_data", "读取方案与装备"), ("scan", "扫描并更新备战方案"),
                           ("generate", "创建新的调律配置"), ("execute", "启动和控制任务"),
                           ("save_selection", "保存用户调律选择"),
                           ("recycle", "允许回收"), ("reset", "允许重置"),
                           ("food", "允许使用狗粮"), ("lock", "允许锁定装备")):
            checkbox = QCheckBox(tr(label))
            checkbox.setChecked(key == "read_data")
            checkbox.toggled.connect(self._authorize)
            self.permissions[key] = checkbox
            layout.addWidget(checkbox)
        actions = QHBoxLayout()
        self.toggle = QPushButton(tr("开启 MCP 接入"))
        self.toggle.clicked.connect(self._toggle)
        actions.addWidget(self.toggle)
        copy_button = QPushButton(tr("复制 WorkBuddy 配置"))
        copy_button.clicked.connect(lambda: self._connection(False))
        actions.addWidget(copy_button)
        export = QPushButton(tr("导出接入配置"))
        export.clicked.connect(lambda: self._connection(True))
        actions.addWidget(export)
        layout.addLayout(actions)
        self.status = QLabel(tr("接入未开启；勾选的权限只对指定用户和执行目标生效。"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.entitlement = QLabel()
        self.entitlement.setWordWrap(True)
        layout.addWidget(self.entitlement)
        self.results = AutoWidthComboBox(width_mode="popup")
        self.results.currentIndexChanged.connect(self._show_result)
        layout.addWidget(self.results)
        self.detail = QTextBrowser()
        layout.addWidget(self.detail, 1)
        self.start = QPushButton(tr("一键启动调律"))
        self.start.clicked.connect(self._start)
        self.start.setEnabled(False)
        layout.addWidget(self.start)
        self.user.resolved_user_changed.connect(self._authorize)
        if hasattr(host, "user_changed"):
            host.user_changed.connect(self._refresh_users)
        self.target.currentIndexChanged.connect(self._authorize)
        self._refresh_targets()
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._refresh_results)
        self.timer.start()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    def _authorize(self, *_args):
        old_user = self.service.grant.user
        self.service.authorize(AgentGrant(
            user=self.user.resolve_username(), target_id=str(self.target.currentData() or ""),
            **{key: value.isChecked() for key, value in self.permissions.items()},
        ))
        if old_user != self.service.grant.user and hasattr(self, "results"):
            self._records = {}
            self.results.clear()
        if hasattr(self, "start"):
            self._show_result()

    def _refresh_users(self, *_args):
        self.user.refresh_users()
        self._authorize()

    def _refresh_targets(self):
        selected = self.target.currentData() or self.host._execution_targets.active_target_id
        self.target.blockSignals(True)
        self.target.clear()
        for target in self.host._execution_targets.all():
            self.target.addItem(target.display_name, target.id)
        self.target.setCurrentIndex(self.target.findData(selected))
        self.target.blockSignals(False)
        self._authorize()

    def _toggle(self):
        try:
            if self.server and self.server.running:
                self.server.stop()
                self.service.authorize(AgentGrant(read_data=False))
                self.toggle.setText(tr("开启 MCP 接入"))
                self.status.setText(tr("接入已关闭；游戏任务按原流程继续，可在调律管理中停止。"))
                return
            if not has_agent_access():
                raise PermissionError(tr(LV1_REQUIRED_MESSAGE))
            self._authorize()
            docs = AgentDocuments(constants.PROJECT_ROOT / "agent")
            docs.list_docs()  # Fail before exposing an incomplete installation.
            from ...core.agent.catalog import tool_catalog
            methods = tool_catalog(self.service, docs)
            self.server = LocalMCPServer(methods, instructions="先调用 get_capabilities、read_doc('entry')。仅在用户授权内操作，生成新配置，不修改已有规则。")
            @self.server.mcp.resource("lvjiang://docs/{doc_id}")
            def read_document(doc_id: str) -> str:
                return docs.read_doc(doc_id)["text"]
            self.server.start()
            self.toggle.setText(tr("关闭 MCP 接入"))
            self.status.setText(tr("接入已开启。复制配置到 WorkBuddy 的自定义 MCP；配置为私人连接信息，重启后需重新导出。"))
        except Exception as exc:  # noqa: BLE001 - show actionable local setup failure
            self.status.setText(str(exc))

    def _connection(self, export: bool):
        try:
            if self.server is None:
                raise ValueError(tr("请先开启 MCP 接入"))
            text = json.dumps(self.server.connection_config(), ensure_ascii=False, indent=2)
            if export:
                name, _ = QFileDialog.getSaveFileName(self, tr("导出私人接入配置"), "lvjiang-mcp.json", "JSON (*.json)")
                if name:
                    from pathlib import Path
                    Path(name).write_text(text + "\n", encoding="utf-8")
            else:
                clipboard = QApplication.clipboard()
                if clipboard is not None:
                    clipboard.setText(text)
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def _refresh_results(self):
        if not self.isVisible():
            return
        self._show_result()
        user = self.user.resolve_username()
        if not user:
            return
        try:
            records = {r["id"]: r for r in self.service.store.load("results").values() if r["user"] == user}
        except (ValueError, OSError) as exc:
            self.status.setText(tr("无法读取生成结果：") + str(exc))
            return
        if records == self._records:
            return
        self._records = records
        selected = self.results.currentData()
        self.results.blockSignals(True)
        self.results.clear()
        for key, record in reversed(list(records.items())):
            self.results.addItem(record["name"], key)
        index = self.results.findData(selected)
        if index >= 0:
            self.results.setCurrentIndex(index)
        self.results.blockSignals(False)
        self._show_result()

    def _show_result(self, *_args):
        record = self._records.get(self.results.currentData())
        allowed = self.permissions["execute"].isChecked()
        licensed = has_agent_access()
        self.entitlement.setText("" if licensed else tr(LV1_REQUIRED_MESSAGE))
        self.toggle.setEnabled(licensed or bool(self.server and self.server.running))
        self.toggle.setToolTip("" if licensed else tr(LV1_REQUIRED_MESSAGE))
        self.start.setEnabled(record is not None and allowed and licensed)
        self.start.setToolTip(tr(LV1_REQUIRED_MESSAGE) if not licensed else (
            "" if allowed else tr("请先允许启动和控制任务")))
        if record:
            from ...config.tune_slots import SLOT_LABELS
            slots = "、".join(SLOT_LABELS.get(key, key) for key in record["run_config"]["selected_slots"])
            lines = [record["goal"], tr("调律部位：") + slots,
                     tr("调律规则：") + str(len(record["run_config"]["rules"])) + tr(" 条"), ""]
            lines.extend(record["suggestions"])
            self.detail.setPlainText("\n".join(lines))
        else:
            self.detail.setPlainText(tr("尚无生成结果。接入 Agent 后，可以先扫描备战方案并讨论养成目标。"))

    def _start(self):
        try:
            if not has_agent_access():
                raise PermissionError(tr(LV1_REQUIRED_MESSAGE))
            self._authorize()
            user = self.user.resolve_username()
            result = self.service.validate_auto_tuning(user, str(self.results.currentData() or ""))
            import uuid
            started = self.bridge.perform("start_tuning", {
                **result, "user": user, "request_id": uuid.uuid4().hex,
            })
            self.status.setText(tr("调律已启动，请在调律管理中查看进度。") + f" ({started['state']})")
        except (ValueError, PermissionError) as exc:
            self.status.setText(str(exc))

    def _shutdown(self):
        self.bridge.close()
        self.service.authorize(AgentGrant(read_data=False))
        if self.server:
            self.server.stop()
