"""主窗口内部状态到插件公开运行接口的适配器。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from ...core.config.models import HotkeyConfig
from ...core.plugin_runtime import RuntimeRun, RuntimeTarget


class PluginRuntime:
    def __init__(self, host: Any):
        self._host = host

    def hotkeys(self) -> HotkeyConfig:
        return deepcopy(self._host._user_config.hotkeys)

    @property
    def current_target_id(self) -> str:
        return self._host._execution_targets.active_target_id

    def _describe(self, target) -> RuntimeTarget:
        return RuntimeTarget(target.id, target.display_name, str(target.kind), target.ready,
                             target.id == self.current_target_id,
                             self._host._run_manager.run_for_target(target.id) is not None)

    def targets(self) -> tuple[RuntimeTarget, ...]:
        return tuple(self._describe(target) for target in self._host._execution_targets.all())

    def resolve_target(self, target_id: str = "", *, require_ready: bool = True) -> RuntimeTarget:
        target = self._host._execution_targets.get(target_id or self.current_target_id)
        if target is None or (require_ready and not target.ready):
            raise ValueError("执行目标未连接或不可用，请查看窗口和设备状态")
        return self._describe(target)

    def select_target(self, target_id: str, *, require_ready: bool = True) -> None:
        target = self.resolve_target(target_id, require_ready=require_ready)
        if self.current_target_id != target.id:
            self._host._capture_launch_draft(self.current_target_id)
            self._host._execution_targets.select(target.id)
            self._host._restore_active_target_view()

    def prepare_start(self, target_id: str, username: str) -> None:
        target = self.resolve_target(target_id)
        decision = self._host._run_manager.can_start(target_id=target.id, username=username)
        if not decision.allowed:
            raise ValueError(decision.reason)
        self.select_target(target.id)
        if not self._host._backend_ready() or not self._host._plan_allows_backend():
            raise ValueError("当前连接方案或执行环境不可用")

    def run(self, task_run_id: str) -> RuntimeRun | None:
        return self._host._run_manager.run(task_run_id)

    def current_run(self) -> RuntimeRun | None:
        return self._host._current_run_context

    def pending_prompt(self, task_run_id: str) -> str | None:
        run = self._host._run_manager.run(task_run_id)
        dialog = getattr(run.ui_helper, "_active_dialog", None) if run is not None else None
        if dialog is None:
            return None
        return dialog.text() if hasattr(dialog, "text") else dialog.windowTitle()

    def control_run(self, task_run_id: str, action: Literal["stop", "pause", "resume"]) -> None:
        run = self.run(task_run_id)
        if run is None:
            raise ValueError("任务已经结束")
        self.select_target(run.target_id, require_ready=action != "stop")
        if action == "stop":
            self._host._request_stop(stop_confirmed=True)
        elif action == "pause":
            self._host._request_pause()
        elif action == "resume":
            if self.pending_prompt(task_run_id) is not None:
                raise ValueError("请先在律匠处理人工提示")
            if str(run.state) != "paused":
                raise ValueError("任务当前未暂停")
            self._host._on_pause_resume()
        else:
            raise ValueError("不支持的任务控制操作")

    def start_workflow(self, config: Any, username: str, overrides: dict) -> None:
        self._host._on_run_workflow(flow_config=config, execution_username=username,
                                    parameter_overrides=overrides)

    def pages(self) -> tuple[Any, ...]:
        tabs = self._host._left_tabs
        return tuple(tabs.widget(index) for index in range(tabs.count()))
