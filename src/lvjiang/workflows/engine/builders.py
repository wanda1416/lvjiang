"""WorkflowEngine 的两种受控运行时装配入口。"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from ...core.capture_base import CaptureBackend
from ...core.config import AndroidAppConfig, DelayParam, InputSimConfig
from ...core.config.users import SessionManager
from ...core.input_base import InputBackend
from ...core.layout_models import Layout
from ...core.ocr import OCREngine
from ...core.user_config import load_user_metadata
from .core import WorkflowEngine


class _ProfileWorkflowRuntime:
    """固定使用无锁入口的轻量运行时；不向调用方暴露策略开关。"""

    def __init__(self, engine: WorkflowEngine) -> None:
        self._engine = engine

    def execute(self, source, *, initial_variables: dict | None = None) -> dict:
        return self._engine._execute_unlocked(
            source, initial_variables=initial_variables
        )


class DeviceWorkflowEngineBuilder:
    """构建设备工作流；设备能力齐全，执行入口带用户锁。"""

    def __init__(
        self,
        *,
        capture: CaptureBackend,
        ocr: OCREngine,
        input_ctrl: InputBackend,
        layout: Layout,
        input_sim: InputSimConfig | None = None,
        delay_params: dict[str, DelayParam] | None = None,
        android_apps: dict[str, AndroidAppConfig] | None = None,
        android_device=None,
        run_env: str = "",
        window_left: int = 0,
        window_top: int = 0,
        stop_check: Callable[[], bool] | None = None,
        pause_event: threading.Event | None = None,
    ) -> None:
        self._kwargs = {
            "capture": capture,
            "ocr": ocr,
            "input_ctrl": input_ctrl,
            "layout": layout,
            "input_sim": input_sim,
            "delay_params": delay_params,
            "android_apps": android_apps,
            "android_device": android_device,
            "run_env": run_env,
            "window_left": window_left,
            "window_top": window_top,
            "stop_check": stop_check,
            "pause_event": pause_event,
        }

    def build(self) -> WorkflowEngine:
        return WorkflowEngine(**self._kwargs)


class ProfileWorkflowEngineBuilder:
    """构建一次性 Profile 脚本引擎；不装配设备资源和 Session 保存能力。"""

    def __init__(
        self,
        *,
        username: str,
        users_dir: Path,
        stop_check: Callable[[], bool] | None = None,
    ) -> None:
        self._username = username
        self._users_dir = Path(users_dir)
        self._stop_check = stop_check

    def build(self) -> _ProfileWorkflowRuntime:
        engine = WorkflowEngine(
            capture=None,
            ocr=None,
            input_ctrl=None,
            layout=None,
            stop_check=self._stop_check,
        )
        engine.run_username = self._username
        engine.users_dir = self._users_dir
        engine.session = SessionManager(self._users_dir).load(self._username)
        user = load_user_metadata(self._username, self._users_dir)
        engine.user_attributes_snapshot = {
            self._username: dict(user.attributes) if user is not None else {}
        }
        # 不绑定 _save_callback：无用户锁并行执行时，整份 Session 快照保存
        # 会覆盖普通任务的更新。Profile 数据仍通过自己的原子仓储写入。
        return _ProfileWorkflowRuntime(engine)
