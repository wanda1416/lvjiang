"""Profile 变更脚本：默认异步 FIFO，按 key 可选择当前线程同步执行。"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from loguru import logger

from ...errors import log_failure
from .transactions import after_commit, profile_transaction, reject_write


@dataclass(frozen=True)
class ProfileTriggerEvent:
    """同步候选变化或异步已提交变化的快照。"""

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
    synchronous: bool = False

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
    synchronous: bool = False,
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
            reject_write('Profile 脚本触发循环，拒绝写入: ' + ' -> '.join((*parent.path, node_id)))
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
        synchronous=synchronous,
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
        self._inline_count = 0
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
        return self._queue.qsize() + int(self._active) + self._inline_count

    def run_inline(self, event: ProfileTriggerEvent) -> bool:
        """不入队、不等待队列，避免持有 Profile 维护锁时跨线程互等。"""
        with self._state_lock:
            if not self._accepting or not event.script:
                return False
            self._inline_count += 1
        self._emit_busy()
        try:
            self._run_event(event)
        finally:
            with self._state_lock:
                self._inline_count -= 1
            self._emit_busy()
        return True

    def _execute_event(self, event: ProfileTriggerEvent) -> None:
        mode = "同步" if event.synchronous else "异步"
        logger.debug("Profile {}脚本开始: {}.{} -> {}", mode, event.model, event.key, event.script)
        try:
            self._run_event(event)
        except Exception as error:  # noqa: BLE001 - 已落库的变更不回滚，无关事件继续执行
            log_failure(
                f"Profile {mode}变更脚本执行失败: "
                f"{event.model}.{event.key} -> {event.script}", error)
        else:
            logger.debug("Profile {}脚本结束: {}.{} -> {}", mode, event.model, event.key, event.script)

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
                self._execute_event(event)
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
            raise FileNotFoundError(f"Profile 变更脚本不存在: workflows/{event.script}")
        runtime = ProfileWorkflowEngineBuilder(
            username=event.username,
            users_dir=self._users_dir,
            stop_check=lambda: self._stop_requested,
        ).build()
        from .maintenance import profile_lock
        from .repository import get_profile_db

        with profile_lock, profile_transaction(get_profile_db()), trigger_context(event):
            runtime.execute(path, initial_variables=event.variables())
            if self._stop_requested:
                reject_write('Profile 脚本已停止，本次同步链未提交')


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
    runner = get_or_create_script_runner()
    if event.synchronous:
        if not runner.run_inline(event):
            reject_write('Profile 脚本执行器已停止，拒绝写入')
        return True
    after_commit(lambda: runner.submit(event))
    return True


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
