"""Profile 数据变更脚本的异步串行调度。"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from loguru import logger


@dataclass(frozen=True)
class ProfileTriggerEvent:
    """一次已经落库的 Profile 变化快照。"""

    username: str
    model: str
    key: str
    old_value: float | str | None
    new_value: float | str | None
    delta: float | None
    source: str
    change_type: str
    script: str = ""
    origin_key: str = ""
    origin_model: str = ""
    path: tuple[str, ...] = ()

    @property
    def node_id(self) -> str:
        return f"{self.model}:{self.key}"

    def variables(self) -> dict:
        return {
            "origin_key": self.origin_key,
            "origin_model": self.origin_model,
            "key": self.key,
            "model": self.model,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "delta": self.delta,
            "source": self.source,
            "change_type": self.change_type,
        }


_trigger_context: ContextVar[ProfileTriggerEvent | None] = ContextVar(
    "profile_trigger_context", default=None
)


def current_trigger_event() -> ProfileTriggerEvent | None:
    return _trigger_context.get()


@contextmanager
def trigger_context(event: ProfileTriggerEvent) -> Iterator[None]:
    token = _trigger_context.set(event)
    try:
        yield
    finally:
        _trigger_context.reset(token)


def prepare_trigger_event(
    *,
    username: str,
    model: str,
    key: str,
    old_value: float | str | None,
    new_value: float | str | None,
    delta: float | None,
    source: str,
    change_type: str,
    script: str,
) -> ProfileTriggerEvent | None:
    """继承当前触发链；检测到回环时在写入前返回 None。"""
    parent = current_trigger_event()
    node_id = f"{model}:{key}"
    if parent is not None and parent.username == username:
        if node_id in parent.path:
            logger.error(
                f"Profile 脚本触发循环，拒绝写入: "
                f"{' -> '.join((*parent.path, node_id))}"
            )
            return None
        origin_key = parent.origin_key
        origin_model = parent.origin_model
        path = (*parent.path, node_id)
    else:
        origin_key = key
        origin_model = model
        path = (node_id,)
    return ProfileTriggerEvent(
        username=username,
        model=model,
        key=key,
        old_value=old_value,
        new_value=new_value,
        delta=delta,
        source=source,
        change_type=change_type,
        script=script,
        origin_key=origin_key,
        origin_model=origin_model,
        path=path,
    )


class ProfileScriptRunner(threading.Thread):
    """无用户锁、单队列 FIFO 执行 Profile 关联脚本。"""

    def __init__(self, users_dir: Path) -> None:
        super().__init__(name="profile-script-runner", daemon=True)
        self._users_dir = Path(users_dir)
        self._queue: queue.Queue[ProfileTriggerEvent] = queue.Queue()
        self._state_lock = threading.Lock()
        self._accepting = True
        self._stop_requested = False
        self._active = False
        self._listeners: list[Callable[[bool, int], None]] = []

    def submit(self, event: ProfileTriggerEvent) -> bool:
        with self._state_lock:
            if not self._accepting or not event.script:
                return False
            self._queue.put(event)
            should_start = not self.is_alive()
            if should_start:
                self.start()
        self._emit_busy()
        return True

    @property
    def pending_count(self) -> int:
        return self._queue.qsize() + int(self._active)

    @property
    def is_busy(self) -> bool:
        return self.pending_count > 0

    def _emit_busy(self) -> None:
        pending = self.pending_count
        with self._state_lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(pending > 0, pending)
            except Exception:  # noqa: BLE001 - 状态观察者不能打断业务队列
                logger.exception("Profile 脚本队列状态回调失败")

    def add_busy_listener(self, listener: Callable[[bool, int], None]) -> None:
        with self._state_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_busy_listener(self, listener: Callable[[bool, int], None]) -> None:
        with self._state_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def request_stop(self) -> None:
        with self._state_lock:
            self._accepting = False
            self._stop_requested = True
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break
        self._emit_busy()

    def run(self) -> None:
        logger.info("Profile 脚本队列已启动")
        while not self._stop_requested:
            try:
                event = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._active = True
            self._emit_busy()
            try:
                self._run_event(event)
            except Exception:  # noqa: BLE001 - 单个脚本失败不得中断队列
                logger.exception(
                    f"Profile 变更脚本执行失败: {event.username}."
                    f"{event.model}.{event.key} -> {event.script}"
                )
            finally:
                self._active = False
                self._queue.task_done()
                self._emit_busy()
        logger.info("Profile 脚本队列已停止")

    def _run_event(self, event: ProfileTriggerEvent) -> None:
        from ...workflows.discovery import resolve_workflow_path
        from ...workflows.engine.builders import ProfileWorkflowEngineBuilder

        path, _ = resolve_workflow_path(event.script)
        if path is None:
            logger.error(f"Profile 变更脚本不存在: workflows/{event.script}")
            return
        runtime = ProfileWorkflowEngineBuilder(
            username=event.username,
            users_dir=self._users_dir,
            stop_check=lambda: self._stop_requested,
        ).build()
        with trigger_context(event):
            runtime.execute(path, initial_variables=event.variables())


_runner: ProfileScriptRunner | None = None
_runner_lock = threading.Lock()


def get_or_create_script_runner(users_dir: Path | None = None) -> ProfileScriptRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            if users_dir is None:
                from ...constants import USERS_DIR
                users_dir = USERS_DIR
            _runner = ProfileScriptRunner(Path(users_dir))
        return _runner

def submit_profile_trigger(event: ProfileTriggerEvent) -> bool:
    if not event.script:
        return False
    return get_or_create_script_runner().submit(event)


def enqueue_profile_change(
    *,
    username: str,
    model: str,
    key: str,
    old_value: float | str | None,
    new_value: float | str | None,
    source: str,
    change_type: str,
    script: str,
) -> bool:
    """供 Profile 后台计算等非 service 写入路径提交触发事件。"""
    delta = (
        float(new_value) - float(old_value or 0)
        if isinstance(new_value, (int, float))
        and (old_value is None or isinstance(old_value, (int, float)))
        else None
    )
    event = prepare_trigger_event(
        username=username,
        model=model,
        key=key,
        old_value=old_value,
        new_value=new_value,
        delta=delta,
        source=source,
        change_type=change_type,
        script=script,
    )
    return event is not None and submit_profile_trigger(event)


def stop_script_runner() -> None:
    global _runner
    with _runner_lock:
        runner = _runner
        _runner = None
    if runner is None:
        return
    runner.request_stop()
    if runner.is_alive():
        runner.join(timeout=5)
    if runner.is_alive():
        logger.warning("Profile 脚本队列 5 秒内未退出，将随进程销毁")
