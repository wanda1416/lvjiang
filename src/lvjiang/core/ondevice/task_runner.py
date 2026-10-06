"""设备端任务生命周期管理 — 悬浮服务的 Python 侧入口

悬浮图标要能「列出任务 → 点一个跑起来 → 看进度 → 随时停」，而 Kotlin 侧
跨语言只方便传字符串，所以这里所有对外函数都返回 JSON 文本，Kotlin 用
org.json 解析即可，不需要为每个字段设计一次桥接类型。

四个对外入口：
    list_tasks()   可执行任务清单
    start_task(id) 后台线程起一个任务（同一时刻只允许一个）
    stop_task()    请求停止（协作式，靠引擎的 stop_check 轮询生效）
    get_status()   当前状态 + 最近日志尾巴

停止是协作式的：DSL 引擎在每条语句、每轮循环前查一次 stop_check，
置位后最多等一条语句执行完就退出。不做强杀——线程中途被掐断会把
截图缓冲、OCR session 留在不确定状态，下一次任务反而更难排查。
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from typing import Any

from ...i18n import tr
from ...workflows.errors import WorkflowAbort

#: 活跃状态（包括暂停/结束中）均占用任务槽，终态才允许启动下一项。
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_PAUSING = "pausing"
STATE_PAUSED = "paused"
STATE_STOPPING = "stopping"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_STOPPED = "stopped"
ACTIVE_STATES = {STATE_RUNNING, STATE_PAUSING, STATE_PAUSED, STATE_STOPPING}
CONTROL_LOCK = threading.RLock()

#: 日志环形缓冲容量。悬浮面板只显示最后几行，留 200 行够回溯一段流程。
_LOG_CAPACITY = 200



class _TaskState:
    """任务运行状态的唯一持有者

    Kotlin 侧会从主线程轮询 get_status()，任务本身跑在另一个线程里，
    所有读写都过同一把锁。锁只护内存字段，不包住工作流执行本身，
    否则轮询会被长任务整个挡住。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = STATE_IDLE
        self._task_id = ""
        self._task_name = ""
        self._message = ""
        self._started_at = 0.0
        self._finished_at = 0.0
        self._result: dict[str, Any] = {}
        self._logs: deque[str] = deque(maxlen=_LOG_CAPACITY)
        self._log_records: deque[dict[str, Any]] = deque(maxlen=_LOG_CAPACITY)
        self._log_seq = 0
        self._log_generation = 0

    # ── 状态读写 ──────────────────────────────────────────

    def is_running(self) -> bool:
        with self._lock:
            return self._state in ACTIVE_STATES

    def should_stop(self) -> bool:
        """交给引擎的 stop_check：不加锁，Event 自身线程安全"""
        return self._stop_event.is_set()

    def log(self, line: str, level: str = "INFO") -> None:
        with self._lock:
            text = f"{time.strftime('%H:%M:%S')} {line}"
            self._logs.append(text)
            self._log_seq += 1
            self._log_records.append({"seq": self._log_seq, "text": text, "level": level})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            elapsed = (
                (self._finished_at or time.time()) - self._started_at
                if self._started_at
                else 0.0
            )
            return {
                "state": self._state,
                "task_id": self._task_id,
                "task_name": self._task_name,
                "message": self._message,
                "elapsed": round(elapsed, 1),
                "stopping": self._stop_event.is_set() and self._state in ACTIVE_STATES,
                "result": self._result,
                "logs": list(self._logs),
                "log_records": [dict(record) for record in self._log_records],
                "log_generation": self._log_generation,
            }

    def begin(self, task_id: str, task_name: str) -> None:
        with self._lock:
            self._stop_event.clear()
            self._state = STATE_RUNNING
            self._task_id = task_id
            self._task_name = task_name
            self._message = "正在启动"
            self._started_at = time.time()
            self._finished_at = 0.0
            self._result = {}
            self._logs.clear()
            self._log_records.clear()
            self._log_generation += 1
            _PAUSE_GATE.set()

    def finish(self, state: str, message: str, result: dict | None = None) -> None:
        with self._lock:
            self._state = state
            self._message = message
            self._finished_at = time.time()
            self._result = result or {}

    def set_message(self, message: str) -> None:
        with self._lock:
            self._message = message

    def request_stop(self) -> None:
        self._stop_event.set()
        _PAUSE_GATE.set()
        with self._lock:
            self._state = STATE_STOPPING
            self._message = "已请求结束，等当前步骤结束"

    def request_pause(self, message: str = "") -> bool:
        with self._lock:
            if self._state != STATE_RUNNING:
                return False
            self._state = STATE_PAUSING
            self._message = message or "正在暂停，等当前步骤结束"
            _PAUSE_GATE.clear()
            return True

    def acknowledge_pause(self) -> None:
        with self._lock:
            if self._state == STATE_PAUSING:
                self._state = STATE_PAUSED

    def resume(self) -> bool:
        with self._lock:
            if self._state not in {STATE_PAUSING, STATE_PAUSED}:
                return False
            self._state = STATE_RUNNING
            self._message = "执行中"
            _PAUSE_GATE.set()
            return True

    def bind_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._thread = thread


_STATE = _TaskState()


class _PauseGate(threading.Event):
    def wait(self, timeout=None):
        if not self.is_set():
            _STATE.acknowledge_pause()
        return super().wait(timeout)


_PAUSE_GATE = _PauseGate()
_PAUSE_GATE.set()

#: 引擎缓存。OCR 模型加载要几秒，每次点一下任务都重建等于白等。
_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _get_engine():
    """取（或首次创建）设备端引擎，跨任务复用

    stop_check 传的是模块级 _STATE 的方法，所以缓存的引擎在后续任务里
    仍然读到当轮的停止标志，不需要为了换 stop_check 重建引擎。
    """
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            from .workflow_runner import create_engine

            _ENGINE = create_engine(stop_check=_STATE.should_stop, pause_event=_PAUSE_GATE)
        return _ENGINE


def release_engine() -> None:
    global _ENGINE
    from .diagnostics import record
    from .onnx_session import close_sessions

    with _ENGINE_LOCK:
        engine, _ENGINE = _ENGINE, None
        try:
            if engine is not None:
                engine.clear_capture_snapshot()
                engine._ocr.close()
        finally:
            close_sessions()
            record("ocr_engine_released")


def _reset_engine_state(engine) -> None:
    """清掉上一轮的执行残留

    引擎是复用的，variables / output / context 若不清，上一轮的中间变量会
    被下一轮的条件判断读到，出问题时极难定位。session 是跨任务的持久状态，
    刻意保留。
    """
    engine.variables = {}
    engine.output = {}
    engine.context = {}
    engine._ui_callback = _task_ui

    # 设备端没有执行用户下拉，每次任务启动都绑定 session 中的当前用户。
    from ... import constants
    from ..config.session import get_session_store

    username = get_session_store().get_active("user", "")
    engine.run_username = username if isinstance(username, str) else ""
    engine.users_dir = constants.USERS_DIR
    if not engine.run_username:
        raise RuntimeError("尚未同步执行用户，请在 PC 移动设备窗口同步配置")
    from ..config.users import SessionManager
    from ..user_config import load_user_metadata

    manager = SessionManager(constants.USERS_DIR)
    engine.session = manager.load(engine.run_username)
    engine._save_callback = manager.save_fn(engine.run_username, engine.session)
    user = load_user_metadata(engine.run_username, constants.USERS_DIR)
    engine.user_attributes_snapshot = {engine.run_username: dict(user.attributes) if user else {}}


def _task_ui(action: str, **kwargs):
    """异常暂停必须真的停住；不支持的交互明确失败，不能静默继续。"""
    text = str(kwargs.get("message") or kwargs.get("prompt") or "请手动处理后继续")
    if action == "notify":
        _STATE.log(text)
        return None
    if action not in {"pause", "confirm"}:
        raise RuntimeError(f"手机暂不支持 {action} 交互，请在 PC 配置任务参数")
    _STATE.request_pause(text)
    while not _PAUSE_GATE.wait(0.2):
        if _STATE.should_stop():
            break
    if _STATE.should_stop():
        raise WorkflowAbort("用户已结束任务")
    return True if action == "confirm" else None


# ── 对外接口（返回 JSON 文本） ─────────────────────────────


def list_tasks(require_sync: bool = True) -> str:
    """可执行任务清单

    Returns:
        JSON 文本 ``{"ok": bool, "tasks": [{"id","name","source"}], "error": str}``
    """
    try:
        from .offline import sync_status
        if require_sync and not sync_status().get("synced"):
            return json.dumps({"ok": False, "tasks": [], "error": "请先在 PC 移动设备窗口同步离线任务配置"}, ensure_ascii=False)
        from ...workflows.discovery import list_exposed_scripts, script_display_name
        from .plugins import ensure_loaded

        # 插件必须先加载：class 来源的脚本（auto_tuning 等）依赖
        # 工作流注册表，未加载会退化成同名旧 .wf（见 plugins 模块说明）。
        ensure_loaded()

        # 共用排序、启停和改名偏好，但设备端没有桌面专用配置页，需允许
        # 作者明确声明的设备专用任务（如自动调律）进入悬浮面板。
        # 冒烟自检任务源码已内联，不再出现在清单里，由 _resolve_task 内置合成。
        items = list_exposed_scripts("android", device_entry=True)

        tasks = [
            {
                "id": item["id"],
                "name": script_display_name(item),
                "source": "class" if item.get("class") else "wf",
            }
            for item in items
            if not item.get("env") or "android" in item["env"]
        ]
        return json.dumps({"ok": True, "tasks": tasks, "error": ""}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"ok": False, "tasks": [], "error": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )


def start_task(task_id: str, initial_variables: str = "") -> str:
    with CONTROL_LOCK:
        return _start_task(task_id, initial_variables)


def _start_task(task_id: str, initial_variables: str = "") -> str:
    """启动一个任务（非阻塞，立刻返回）

    Args:
        task_id: ``list_tasks()`` 里的 id
        initial_variables: 可选的初始变量，JSON 对象文本；空串表示无

    Returns:
        JSON 文本 ``{"ok": bool, "message": str}``。ok=False 时任务未启动。
    """
    if _STATE.is_running():
        return json.dumps(
            {"ok": False, "message": "已有任务在运行，请先停止"}, ensure_ascii=False
        )
    from .offline import sync_status
    if not sync_status().get("synced"):
        return json.dumps({"ok": False, "message": "请先从 PC 同步任务配置与执行用户"}, ensure_ascii=False)

    try:
        from . import a11y

        if not a11y.is_ready():
            return json.dumps(
                {"ok": False, "message": "无障碍服务未连接，请先在设置里开启"},
                ensure_ascii=False,
            )
    except Exception as e:
        return json.dumps(
            {"ok": False, "message": f"无障碍通道检查失败: {e}"}, ensure_ascii=False
        )

    try:
        variables = json.loads(initial_variables) if initial_variables else None
        if variables is not None and not isinstance(variables, dict):
            raise ValueError(tr("initial_variables 必须是 JSON 对象"))
    except Exception as e:
        return json.dumps(
            {"ok": False, "message": f"初始变量解析失败: {e}"}, ensure_ascii=False
        )

    try:
        task = _resolve_task(task_id)
    except Exception as e:
        return json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False)

    _STATE.begin(task_id, task["name"])
    thread = threading.Thread(
        target=_run_in_thread,
        args=(task, variables),
        name=f"lvjiang-task-{task_id}",
        daemon=True,
    )
    _STATE.bind_thread(thread)
    thread.start()
    return json.dumps(
        {"ok": True, "message": f"已启动：{task['name']}"}, ensure_ascii=False
    )


def stop_task() -> str:
    """请求停止当前任务（协作式，不强杀）

    Returns:
        JSON 文本 ``{"ok": bool, "message": str}``
    """
    if not _STATE.is_running():
        return json.dumps({"ok": False, "message": "当前没有运行中的任务"}, ensure_ascii=False)
    _STATE.request_stop()
    return json.dumps({"ok": True, "message": "已请求停止"}, ensure_ascii=False)


def pause_task() -> str:
    ok = _STATE.request_pause()
    return json.dumps({"ok": ok, "message": "已请求暂停" if ok else "当前任务不能暂停"}, ensure_ascii=False)


def resume_task() -> str:
    ok = _STATE.resume()
    return json.dumps({"ok": ok, "message": "已继续" if ok else "当前没有暂停任务"}, ensure_ascii=False)


def get_status() -> str:
    """当前状态快照

    Returns:
        JSON 文本，字段见 ``_TaskState.snapshot``
    """
    from .offline import sync_status
    return json.dumps({**_STATE.snapshot(), "sync": sync_status()}, ensure_ascii=False, default=str)


def is_running() -> bool:
    """给 Kotlin 侧的轻量判据，省掉一次 JSON 解析"""
    return _STATE.is_running()


# ── 内部实现 ──────────────────────────────────────────────


def _resolve_task(task_id: str) -> dict:
    """按 id 找到任务配置，找不到就抛出带可选项的异常"""
    from ...workflows.discovery import discover_scripts
    from .plugins import ensure_loaded

    ensure_loaded()  # 同 list_tasks：class 型脚本要靠插件注册表才解析成类实现

    for item in discover_scripts():
        if item["id"] == task_id:
            if item.get("env") and "android" not in item["env"]:
                raise ValueError("此任务不支持安卓运行")
            return item
    available = ", ".join(item["id"] for item in discover_scripts()) or tr("（空）")
    raise ValueError(f"未找到任务 {task_id!r}，可选：{available}")


def _run_in_thread(task: dict, variables: dict | None) -> None:
    """任务线程主体：任何异常都收进状态，绝不让线程带着栈自己消失"""
    name = task["name"]
    sink = None
    try:
        from copy import deepcopy

        from loguru import logger

        from ..config.wf_configs import get_wf_config
        from ..task_params import resolve_task_params
        from .diagnostics import record

        worker_id = threading.get_ident()
        sink = logger.add(lambda message: _STATE.log(str(message).strip(), message.record["level"].name),
                          filter=lambda record: record["thread"].id == worker_id,
                          format="{message}", level="INFO")
        _STATE.set_message("正在初始化引擎")
        _STATE.log(f"任务开始：{name}")
        record("task_begin", task["id"])
        engine = _get_engine()
        _reset_engine_state(engine)
        engine.workflow_config_snapshot = deepcopy(get_wf_config(task["id"]))
        if task.get("class"):
            from ...workflows.implementations import get_workflow_class

            loader = getattr(get_workflow_class(task["class"]), "CONFIG_SNAPSHOT_LOADER", None)
            if loader is not None:
                engine.workflow_config_snapshot = deepcopy(loader(engine.run_username, engine.users_dir))
        params, _ = resolve_task_params(task["id"], engine.run_username, task.get("parameters", []), engine.users_dir)
        if variables:
            params.update(variables)

        source = _build_source(task, engine)
        _STATE.set_message("执行中")
        result = engine.execute(source, initial_variables=params)

        if _STATE.should_stop():
            _STATE.log("任务被停止")
            _STATE.finish(STATE_STOPPED, f"已停止：{name}", dict(result or {}))
            return

        # 工作流预检失败返回 {"error": ...}：拒绝启动，按失败收场
        if isinstance(result, dict) and result.get("error"):
            _STATE.log(f"启动被拒绝：{result['error']}")
            _STATE.finish(STATE_FAILED, f"启动被拒绝：{result['error']}")
            return

        collected = len(result or {})
        _STATE.log(f"任务完成，收集 {collected} 项")
        _STATE.finish(STATE_DONE, f"已完成：{name}", dict(result or {}))
    except MemoryError as exc:
        _STATE.log(f"内存不足，任务已中止：{exc}", "ERROR")
        _STATE.finish(STATE_FAILED, "手机 OCR 内存不足，已中止任务，请查看诊断日志")
        release_engine()
    except WorkflowAbort as exc:
        _STATE.log(str(exc))
        _STATE.finish(STATE_STOPPED if _STATE.should_stop() else STATE_FAILED, str(exc))
    except Exception as e:
        from ...workflows.errors import WorkflowExecutionError

        detail = traceback.format_exc().rstrip()
        _STATE.log(f"任务异常：{type(e).__name__}: {e}", "ERROR")
        for line in detail.splitlines()[-8:]:
            _STATE.log(line, "ERROR")
        cause = e.__cause__ if isinstance(e, WorkflowExecutionError) else None
        reason = f"{type(e).__name__}: {e}"
        if cause is not None:
            reason += f"：{cause}"
        if isinstance(cause, MemoryError):
            release_engine()
        _STATE.finish(STATE_STOPPED if _STATE.should_stop() else STATE_FAILED,
                      "用户已结束任务" if _STATE.should_stop() else reason)
    finally:
        from .diagnostics import record
        # from last 仅属于本轮；终态不再留整屏图像等下一次任务。
        if _ENGINE is not None:
            _ENGINE.clear_capture_snapshot()
        record("task_end", json.dumps({"task": task["id"], "state": _STATE.snapshot()["state"]}, ensure_ascii=False))
        if sink is not None:
            from loguru import logger
            logger.remove(sink)


def _build_source(task: dict, engine):
    """把任务配置换成 engine.execute 能吃的 source

    .wf 任务给路径，内置类实现给 BaseWorkflow 实例——引擎的 execute
    本来就同时接受这两种，这里只负责挑对。
    """
    if task.get("class"):
        from ...workflows.implementations import get_workflow_class

        cls = get_workflow_class(task["class"])
        return cls(
            capture=engine._capture,
            ocr=engine._ocr,
            input_ctrl=engine._input,
            layout=engine._layout,
            input_sim=engine._input_sim,
            delay_params=engine._delay_params,
            window_left=engine._window_left,
            window_top=engine._window_top,
            stop_check=_STATE.should_stop,
            pause_event=_PAUSE_GATE,
        )

    from ..config.resolver import get_resolver

    wf_path = get_resolver().resolve_read(f"workflows/{task['wf_file']}")
    if wf_path is None:
        raise FileNotFoundError(f"工作流文件不存在: {task['wf_file']}")
    return wf_path
