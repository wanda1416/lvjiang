"""MCP service lifecycle and Qt task bridge; business interaction lives in the Agent."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import Future, TimeoutError

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..... import constants
from .....core.agent_docs import AgentDocuments
from .....core.agent_mcp import LV1_REQUIRED_MESSAGE, LocalMCPServer, has_agent_access
from .....i18n import tr
from ...core.agent.service import AgentService, revision


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
        if QThread.currentThread() == self.thread():
            if self.closed:
                raise ValueError("律匠正在关闭")
            return self.perform(operation, arguments)
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
        if self.service is not None:
            self.service._ensure_enabled()
        host = self.host
        if operation == "reload_rules":
            from ...core.tuning_rules import (
                get_tuning_group_manager,
                get_tuning_rule_manager,
            )
            get_tuning_rule_manager().reload()
            get_tuning_group_manager().reload()
            return {"updated": True}
        if operation == "context":
            return {"current_user": host.user_manager.get_active_user_name(),
                    "users": host.user_manager.list_users()}
        if operation == "targets":
            return {"current_target_id": host._execution_targets.active_target_id,
                    "targets": [{"id": target.id, "name": target.display_name,
                                 "kind": target.kind, "ready": target.ready,
                                 "selected": target.id == host._execution_targets.active_target_id,
                                 "busy": host._run_manager.run_for_target(target.id) is not None}
                                for target in host._execution_targets.all()]}
        if operation == "resolve_target":
            target_id = args.get("target_id") or host._execution_targets.active_target_id
            target = host._execution_targets.get(target_id)
            if target is None or not target.ready:
                raise ValueError("执行目标未连接或不可用，请调用 list_targets 查看窗口和设备状态")
            return {"target_id": target.id}
        if operation == "validate":
            from ...core.agent.launch import prepare_tuning
            prepare_tuning(args["config"])
            target = host._execution_targets.get(args["target_id"])
            if target is None or not target.ready:
                raise ValueError("执行目标未连接或不可用，请调用 list_targets 查看状态")
            decision = host._run_manager.can_start(target_id=target.id, username=args["user"])
            if not decision.allowed:
                raise ValueError(decision.reason)
            self._select_target(target.id)
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
                self.service._check(args["user"])
            active = host._run_manager.run(run.task_run_id)
            if action != "status":
                if active is None:
                    raise ValueError("任务已经结束")
                self._select_target(run.target_id, require_ready=action != "stop")
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
            self.service._check(args["user"])
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
                # Recheck configuration and user existence immediately before launch.
                args = {**args, **self.service.validate_auto_tuning(args["user"], args["result_id"], args["target_id"])}
        target = host._execution_targets.get(args["target_id"])
        if target is None or not target.ready:
            raise ValueError("执行目标未连接或不可用，请调用 list_targets 查看状态")
        if host._run_manager.run_for_target(target.id) is not None:
            raise ValueError("执行目标正在运行任务")
        if args["user"] not in host.user_manager.list_users():
            raise ValueError("执行用户已不存在")
        self._select_target(target.id)
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


    def _select_target(self, target_id: str, *, require_ready: bool = True) -> None:
        host = self.host
        target = host._execution_targets.get(target_id)
        if target is None or (require_ready and not target.ready):
            raise ValueError("执行目标未连接或不可用")
        if host._execution_targets.active_target_id != target_id:
            host._capture_launch_draft(host._execution_targets.active_target_id)
            host._execution_targets.select(target_id)
            host._restore_active_target_view()


class AgentTuningPage(QWidget):
    """Only connection lifecycle lives here; users, targets and actions live in MCP."""

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.bridge = AgentTaskBridge(host)
        self.service = AgentService(constants.PROJECT_ROOT, dispatch=self.bridge.call)
        self.bridge.service = self.service
        self.server: LocalMCPServer | None = None
        layout = QVBoxLayout(self)
        description = QLabel(tr(
            "启动 MCP 后，在 WorkBuddy 等外部 Agent 中交流流派和养成目标。"
            "AI 可以了解当前用户与全部用户、查询装备、扫描备战方案、生成新配置并启动调律。"
            "切换用户或设备无需重新导出接入配置。"))
        description.setWordWrap(True)
        layout.addWidget(description)
        actions = QHBoxLayout()
        self.start_service = QPushButton(tr("启动 MCP 服务"))
        self.start_service.clicked.connect(self._start_service)
        actions.addWidget(self.start_service)
        self.export = QPushButton(tr("导出 MCP 接入配置"))
        self.export.clicked.connect(self._export)
        actions.addWidget(self.export)
        self.stop_service = QPushButton(tr("关闭 MCP 服务"))
        self.stop_service.clicked.connect(self._stop_service)
        actions.addWidget(self.stop_service)
        layout.addLayout(actions)
        self.entitlement = QLabel()
        self.entitlement.setWordWrap(True)
        layout.addWidget(self.entitlement)
        self.status = QLabel(tr("MCP 服务未启动。导出的配置包含私人连接令牌，请勿公开分享。"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._refresh_state)
        self.timer.start()
        self._refresh_state()
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    def _refresh_state(self):
        licensed = has_agent_access()
        running = bool(self.server and self.server.running)
        self.entitlement.setText("" if licensed else tr(LV1_REQUIRED_MESSAGE))
        self.start_service.setEnabled(licensed and not running)
        self.start_service.setToolTip("" if licensed else tr(LV1_REQUIRED_MESSAGE))
        self.export.setEnabled(running and self.service.enabled)
        self.stop_service.setEnabled(running and self.service.enabled)

    def _start_service(self):
        try:
            if not has_agent_access():
                raise PermissionError(tr(LV1_REQUIRED_MESSAGE))
            if self.server and self.server.running:
                return
            import asyncio
            import sys

            from ...core.agent.catalog import tool_catalog
            docs = AgentDocuments(constants.PROJECT_ROOT / "docs",
                                  source_mode=not getattr(sys, "frozen", False))
            server = LocalMCPServer(tool_catalog(self.service, docs), instructions=(
                "先调用 get_capabilities、list_users 和 read_doc('entry')。游戏机制读 10-game，操作指导读 60-userguide，DSL 与架构读 30-architecture，接口契约读 70-agent。"
                "开启服务即可使用全部已开放能力；用户和目标按每次调用选择。"
                "切换用户不需改连接配置；调用受限时向用户解释接口返回原因。"
                "生成新配置，不修改已有规则。"))
            docs.schema_provider = lambda: json.dumps(
                [tool.model_dump(mode="json") for tool in asyncio.run(server.mcp.list_tools())],
                ensure_ascii=False, indent=2) + "\n"
            docs.list_docs()

            @server.mcp.resource("lvjiang://docs/{doc_id}")
            async def read_document(doc_id: str) -> str:
                document = await asyncio.to_thread(docs.read_doc, doc_id)
                return document["text"]

            server.start()
            self.server = server
            self.service.set_enabled(True)
            self.status.setText(tr("MCP 服务已启动。导出配置到 WorkBuddy；用户和设备由 AI 通过接口发现，切换时无需重新导出。"))
        except Exception as exc:  # noqa: BLE001 - show actionable local setup failure
            self.status.setText(str(exc))
        self._refresh_state()

    def _stop_service(self):
        self.service.set_enabled(False)
        if self.server:
            self.server.stop()
        self.status.setText(tr("MCP 服务已关闭；已启动的游戏任务继续运行，可在调律管理中停止。"))
        self._refresh_state()

    def _export(self):
        try:
            if self.server is None:
                raise ValueError(tr("请先启动 MCP 服务"))
            text = json.dumps(self.server.connection_config(), ensure_ascii=False, indent=2)
            name, _ = QFileDialog.getSaveFileName(self, tr("导出私人接入配置"), "lvjiang-mcp.json", "JSON (*.json)")
            if name:
                from pathlib import Path
                Path(name).write_text(text + "\n", encoding="utf-8")
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def _shutdown(self):
        self.service.set_enabled(False)
        self.bridge.close()
        if self.server:
            self.server.stop()
