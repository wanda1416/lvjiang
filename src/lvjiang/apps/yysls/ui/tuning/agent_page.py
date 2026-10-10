"""MCP service lifecycle and Qt task bridge; business interaction lives in the Agent."""
from __future__ import annotations

import copy
import json
import threading
from concurrent.futures import Future, TimeoutError
from pathlib import Path

from PyQt6.QtCore import QObject, QSignalBlocker, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..... import constants
from .....core.access import is_readonly
from .....core.agent_connection import AgentConnection, export_agent
from .....core.agent_docs import AgentDocuments
from .....core.agent_mcp import LV1_REQUIRED_MESSAGE, LocalMCPServer, has_agent_access
from .....core.agent_settings import AgentSettings, load_agent_settings
from .....i18n import tr
from .....ui.button_styles import apply_button_style
from .....ui.combo_box import AutoWidthComboBox, ComboWidthMode
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
        self.root = constants.PROJECT_ROOT
        settings_error = ""
        try:
            self.connection = AgentConnection.load(self.root)
        except (OSError, ValueError) as exc:
            self.connection = AgentConnection()
            settings_error = str(exc)
        self.bridge = AgentTaskBridge(host)
        self.service = AgentService(constants.PROJECT_ROOT, dispatch=self.bridge.call)
        self.bridge.service = self.service
        self.server: LocalMCPServer | None = None
        layout = QVBoxLayout(self)
        description = QLabel(tr(
            "实验性功能：启动 MCP 后，在 WorkBuddy 等外部 Agent 中交流流派和养成目标。"
            "AI 可以了解当前用户与全部用户、查询装备、扫描备战方案、生成新配置并启动调律。"
            "启用后每次主实例启动时自动开启服务并同步接入配置。"))
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.connection.port)
        self.port.valueChanged.connect(self._save_port)
        form.addRow(tr("MCP 端口"), self.port)
        self.agent = AutoWidthComboBox(width_mode=ComboWidthMode.STRETCH)
        self.agent.currentIndexChanged.connect(self._select_agent)
        form.addRow(tr("使用 Agent"), self.agent)
        self.export_path = QLineEdit()
        self.export_path.editingFinished.connect(self._save_export_path)
        path_row = QHBoxLayout()
        path_row.addWidget(self.export_path, 1)
        self.choose_path = QPushButton(tr("选择文件"))
        self.choose_path.clicked.connect(self._choose_export_file)
        apply_button_style(self.choose_path, variant="neutral")
        path_row.addWidget(self.choose_path)
        form.addRow(tr("导出文件"), path_row)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.start_service = QPushButton(tr("启用智能调律"))
        self.start_service.clicked.connect(self._start_service)
        actions.addWidget(self.start_service)
        self.export = QPushButton(tr("同步接入配置"))
        self.export.clicked.connect(self._export)
        actions.addWidget(self.export)
        self.download_agent = QPushButton(tr("下载 Agent"))
        self.download_agent.clicked.connect(self._download_agent)
        actions.addWidget(self.download_agent)
        self.stop_service = QPushButton(tr("停用智能调律"))
        self.stop_service.clicked.connect(self._stop_service)
        actions.addWidget(self.stop_service)
        layout.addLayout(actions)
        for button in (self.start_service, self.export, self.download_agent, self.stop_service):
            apply_button_style(button, variant="action" if button is self.start_service else "neutral")
        self.entitlement = QLabel()
        self.entitlement.setWordWrap(True)
        layout.addWidget(self.entitlement)
        self.status = QLabel(tr("MCP 服务未启动。导出的配置包含私人连接令牌，请勿公开分享。"))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.agent_settings = AgentSettings()
        self.reload_agents()
        layout.addStretch()
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self._refresh_state)
        self.timer.start()
        self._refresh_state()
        if settings_error:
            self.status.setText(tr("无法读取 MCP 接入配置：") + settings_error)
        elif self.connection.enabled and not is_readonly():
            QTimer.singleShot(0, self._start_service)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown)

    def _refresh_state(self):
        licensed = has_agent_access()
        running = bool(self.server and self.server.running)
        readonly = is_readonly()
        self.entitlement.setText(tr("只读实例无法启用 MCP 服务，请使用主实例") if readonly
                                 else "" if licensed else tr(LV1_REQUIRED_MESSAGE))
        self.start_service.setEnabled(licensed and not running and not readonly)
        self.start_service.setToolTip("" if licensed else tr(LV1_REQUIRED_MESSAGE))
        self.export.setEnabled(running and self.service.enabled)
        self.agent.setEnabled(not readonly)
        self.export_path.setEnabled(not readonly)
        self.choose_path.setEnabled(not readonly)
        self.download_agent.setEnabled(bool(self.agent_settings.download_url))
        self.stop_service.setEnabled((running or self.connection.enabled) and not readonly)
        self.port.setEnabled(not running and not readonly)

    def _save_port(self, port):
        try:
            self.connection.port = port
            self.connection.save(self.root)
        except (OSError, PermissionError) as exc:
            self.status.setText(str(exc))

    def reload_agents(self):
        try:
            settings = load_agent_settings()
            with QSignalBlocker(self.agent):
                self.agent.clear()
                for item in settings.items:
                    self.agent.addItem(item.name, item.key)
                index = self.agent.findData(self.connection.agent_key)
                self.agent.setCurrentIndex(index if index >= 0 else 0)
            self.agent_settings = settings
            self._show_export_path()
        except (ValueError, OSError) as exc:
            self.status.setText(tr("Agent 设置无法读取：") + str(exc))

    def _selected_agent(self):
        return next((item for item in self.agent_settings.items
                     if item.key == self.agent.currentData()), None)

    def _show_export_path(self):
        target = self._selected_agent()
        self.export_path.setText(self.connection.export_paths.get(target.key, target.export_path) if target else "")

    def _select_agent(self):
        if is_readonly():
            return
        self.connection.agent_key = self.agent.currentData() or ""
        self._show_export_path()
        try:
            self.connection.save(self.root)
            if self.server and self.server.running and self.service.enabled:
                self._export()
        except OSError as exc:
            self.status.setText(str(exc))

    def _save_export_path(self):
        target = self._selected_agent()
        if target is None or is_readonly():
            return
        try:
            path = self.export_path.text().strip()
            if not Path(path).expanduser().is_absolute():
                raise ValueError(tr("导出路径需为绝对路径或以 ~/ 开头"))
            if path == target.export_path:
                self.connection.export_paths.pop(target.key, None)
            else:
                self.connection.export_paths[target.key] = path
            self.connection.save(self.root)
            if self.server and self.server.running and self.service.enabled:
                self._export()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def _download_agent(self):
        if self.agent_settings.download_url:
            QDesktopServices.openUrl(QUrl(self.agent_settings.download_url))

    def _start_service(self):
        try:
            if is_readonly():
                raise PermissionError(tr("只有主实例可以启动 MCP 服务"))
            if not has_agent_access():
                raise PermissionError(tr(LV1_REQUIRED_MESSAGE))
            if self.server and self.server.running:
                return
            self.connection.enabled = True
            self.connection.port = self.port.value()
            self.connection.agent_key = self.agent.currentData() or ""
            self.connection.save(self.root)
            import asyncio
            import sys

            from ...core.agent.catalog import tool_catalog
            docs = AgentDocuments(constants.PROJECT_ROOT / "docs",
                                  source_mode=not getattr(sys, "frozen", False))
            server = LocalMCPServer(tool_catalog(self.service, docs), instructions=(
                "先调用 get_capabilities、list_users 和 read_doc('entry')。游戏机制读 10-game，操作指导读 60-userguide，DSL 与架构读 30-architecture，接口契约读 70-agent。"
                "开启服务即可使用全部已开放能力；用户和目标按每次调用选择。"
                "切换用户不需改连接配置；调用受限时向用户解释接口返回原因。"
                "生成前先调用 get_tuning_config，遵守 rule_contract 中 patterns 部位归并与行为 parts 的区别；不要从模板或报错猜测合法部位。"
                "生成新配置，不修改已有规则。"), port=self.connection.port, token=self.connection.token)
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
            self._export()
        except Exception as exc:  # noqa: BLE001 - show actionable local setup failure
            self.status.setText(str(exc))
        self._refresh_state()

    def _stop_service(self):
        if is_readonly():
            return
        self.connection.enabled = False
        try:
            self.connection.save(self.root)
        except OSError as exc:
            self.status.setText(tr("停用状态未保存：") + str(exc))
            return
        self.service.set_enabled(False)
        if self.server:
            self.server.stop()
        self.status.setText(tr("智能调律已停用，下次启动不会开启 MCP；已启动的游戏任务继续运行。"))
        self._refresh_state()

    def _export(self):
        try:
            if self.server is None:
                raise ValueError(tr("请先启动 MCP 服务"))
            config = self.server.connection_config()
            target = self._selected_agent()
            if target is None:
                raise ValueError(tr("请先在 AI 设置的外部 Agent 中添加接入配置"))
            path = Path(self.connection.export_paths.get(target.key, target.export_path)).expanduser()
            export_agent(config, path, server_name=self.agent_settings.server_name, transport=target.transport)
            self.status.setText(tr("MCP 服务已启动，{agent} 接入配置已同步；重启无需重新导出。").format(agent=target.name))
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def _choose_export_file(self):
        if is_readonly():
            return
        name, _ = QFileDialog.getSaveFileName(self, tr("选择 Agent 接入配置文件"),
                                            self.export_path.text(), "JSON (*.json)")
        if name:
            self.export_path.setText(name)
            self._save_export_path()

    def _shutdown(self):
        self.service.set_enabled(False)
        self.bridge.close()
        if self.server:
            self.server.stop()


class AgentTuningDialog(QDialog):
    """菜单对话框关闭只隐藏，服务生命周期属于主窗口。"""

    def __init__(self, host):
        super().__init__(host)
        self.setWindowTitle(tr("智能调律 Agent（实验性）"))
        self.resize(760, 330)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.page = AgentTuningPage(host)
        layout.addWidget(self.page)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if button is not None:
            apply_button_style(button, variant="neutral")
        layout.addWidget(buttons)

    def showEvent(self, event):  # noqa: N802 - Qt API
        self.page.reload_agents()
        self.page._refresh_state()
        super().showEvent(event)
