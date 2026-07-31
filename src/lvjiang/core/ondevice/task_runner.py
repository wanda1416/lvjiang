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

#: 状态机取值。running 之外都是终态，可直接再起下一个任务。
STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_STOPPED = "stopped"

#: 日志环形缓冲容量。悬浮面板只显示最后几行，留 200 行够回溯一段流程。
_LOG_CAPACITY = 200

#: 冒烟自检任务 id（config/system/workflows/device_smoke_test.wf）。它不在日常
#: 暴露列表里，但设备端自检链路 smoke.py 要靠它出现在清单中，故 list_tasks 单独补入。
_SMOKE_TASK_ID = "device_smoke_test"


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

    # ── 状态读写 ──────────────────────────────────────────

    def is_running(self) -> bool:
        with self._lock:
            return self._state == STATE_RUNNING

    def should_stop(self) -> bool:
        """交给引擎的 stop_check：不加锁，Event 自身线程安全"""
        return self._stop_event.is_set()

    def log(self, line: str) -> None:
        with self._lock:
            self._logs.append(f"{time.strftime('%H:%M:%S')} {line}")

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
                "stopping": self._stop_event.is_set() and self._state == STATE_RUNNING,
                "result": self._result,
                "logs": list(self._logs),
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
        self.set_message("已请求停止，等当前步骤结束")

    def bind_thread(self, thread: threading.Thread) -> None:
        with self._lock:
            self._thread = thread


_STATE = _TaskState()

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

            _ENGINE = create_engine(stop_check=_STATE.should_stop)
        return _ENGINE


def _reset_engine_state(engine) -> None:
    """清掉上一轮的执行残留

    引擎是复用的，variables / output / context 若不清，上一轮的中间变量会
    被下一轮的条件判断读到，出问题时极难定位。session 是跨任务的持久状态，
    刻意保留。
    """
    engine.variables = {}
    engine.output = {}
    engine.context = {}


# ── 对外接口（返回 JSON 文本） ─────────────────────────────


def list_tasks() -> str:
    """可执行任务清单

    Returns:
        JSON 文本 ``{"ok": bool, "tasks": [{"id","name","source"}], "error": str}``
    """
    try:
        from ...workflows.discovery import discover_scripts, list_exposed_scripts

        # 日常清单只给 workflows.yaml 暴露的脚本（含中文显示名），与桌面下拉一致；
        # 冒烟自检任务不暴露，但自检链路要用，未暴露时从全集里补一条到末尾。
        items = list_exposed_scripts()
        if not any(item["id"] == _SMOKE_TASK_ID for item in items):
            for cfg in discover_scripts():
                if cfg["id"] == _SMOKE_TASK_ID:
                    items = [*items, cfg]
                    break

        tasks = [
            {
                "id": item["id"],
                "name": item["name"],
                "source": "class" if item.get("class") else "wf",
            }
            for item in items
        ]
        return json.dumps({"ok": True, "tasks": tasks, "error": ""}, ensure_ascii=False)
    except Exception as e:
        return json.dumps(
            {"ok": False, "tasks": [], "error": f"{type(e).__name__}: {e}"},
            ensure_ascii=False,
        )


def start_task(task_id: str, initial_variables: str = "") -> str:
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
            raise ValueError("initial_variables 必须是 JSON 对象")
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


def get_status() -> str:
    """当前状态快照

    Returns:
        JSON 文本，字段见 ``_TaskState.snapshot``
    """
    return json.dumps(_STATE.snapshot(), ensure_ascii=False)


def is_running() -> bool:
    """给 Kotlin 侧的轻量判据，省掉一次 JSON 解析"""
    return _STATE.is_running()


# ── 内部实现 ──────────────────────────────────────────────


def _resolve_task(task_id: str) -> dict:
    """按 id 找到任务配置，找不到就抛出带可选项的异常"""
    from ...workflows.discovery import discover_scripts

    for item in discover_scripts():
        if item["id"] == task_id:
            return item
    available = ", ".join(item["id"] for item in discover_scripts()) or "（空）"
    raise ValueError(f"未找到任务 {task_id!r}，可选：{available}")


def _run_in_thread(task: dict, variables: dict | None) -> None:
    """任务线程主体：任何异常都收进状态，绝不让线程带着栈自己消失"""
    name = task["name"]
    try:
        _STATE.set_message("正在初始化引擎")
        _STATE.log(f"任务开始：{name}")
        engine = _get_engine()
        _reset_engine_state(engine)

        source = _build_source(task, engine)
        _STATE.set_message("执行中")
        result = engine.execute(source, initial_variables=variables)

        if _STATE.should_stop():
            _STATE.log("任务被停止")
            _STATE.finish(STATE_STOPPED, f"已停止：{name}", dict(result or {}))
            return

        collected = len(result or {})
        _STATE.log(f"任务完成，收集 {collected} 项")
        _STATE.finish(STATE_DONE, f"已完成：{name}", dict(result or {}))
    except Exception as e:
        detail = traceback.format_exc().rstrip()
        _STATE.log(f"任务异常：{type(e).__name__}: {e}")
        for line in detail.splitlines()[-8:]:
            _STATE.log(line)
        _STATE.finish(STATE_FAILED, f"{type(e).__name__}: {e}")


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
        )

    from ..config_resolver import get_resolver

    wf_path = get_resolver().resolve_read(f"workflows/{task['wf_file']}")
    if wf_path is None:
        raise FileNotFoundError(f"工作流文件不存在: {task['wf_file']}")
    return wf_path
