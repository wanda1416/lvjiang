"""跨平台本机 HTTP/SSE 控制面服务（单活跃槽位，无队列）。

设计原则：
- 仅绑定 127.0.0.1:0（内核分配空闲端口），不引入任何额外重依赖（纯标准库 ThreadingHTTPServer）；
- 所有请求强制校验 Authorization: Bearer <token>；
- 仅维护一个当前活跃自动化槽位：空闲则接单（202），繁忙直接返回 409 Conflict（不建队）；
- 捕获执行线程产生的 loguru 日志并经 SSE 推送给薄壳 CLI；
- 支持 headless 模式的空闲自动退出（idle_exit >= 0；0 表示首单跑完即退，负数表示常驻）。
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from ..task_cli import EXIT_OK, EXIT_RUNTIME, CliError
from .discovery import (
    ControlEndpoint,
    control_file_path,
    generate_token,
    now_iso,
    remove_control_file,
    write_control_file,
)
from .runner import list_catalog, run_job

JobRunner = Callable[
    [dict[str, Any], threading.Event, Callable[[str, dict[str, Any]], None]],
    dict[str, Any],
]


def _default_job_runner(
    payload: dict[str, Any],
    stop_event: threading.Event,
    emit: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    return run_job(payload, stop_event=stop_event, emit=emit)


@dataclass
class _JobRecord:
    job_id: str
    kind: str
    label: str
    payload: dict[str, Any]
    started_at: str
    status: str = "running"  # running | succeeded | failed | interrupted
    finished_at: str = ""
    outcome: dict[str, Any] | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    events_cond: threading.Condition = field(default_factory=threading.Condition)

    def push_event(self, event_name: str, data: dict[str, Any]) -> None:
        with self.events_cond:
            self.events.append((event_name, data))
            self.events_cond.notify_all()

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "label": self.label,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outcome": self.outcome,
        }


class ControlServer:
    """本机单活跃任务控制面服务。"""

    def __init__(
        self,
        *,
        mode: str = "gui",
        control_path: Path | None = None,
        instance_id: str = "",
        idle_exit: float = -1.0,
        external_busy_check: Callable[[], bool] | None = None,
        job_runner: JobRunner | None = None,
        catalog_provider: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self._mode = mode
        self._control_path = control_path or control_file_path()
        self._instance_id = instance_id or self._resolve_instance_id()
        # idle_exit: <0 表示常驻不退出；==0 表示任务完成后立即退出（启动宽限 5s）；>0 表示空闲 N 秒后退出
        self._idle_exit = float(idle_exit)
        self._external_busy_check = external_busy_check
        self._job_runner: JobRunner = job_runner or _default_job_runner
        self._catalog_provider = catalog_provider or list_catalog

        self._token = generate_token()
        self._lock = threading.Lock()
        self._active_job: _JobRecord | None = None
        self._jobs: dict[str, _JobRecord] = {}
        self._finished_job_order: list[str] = []
        self._has_run_any_job = False
        self._last_job: _JobRecord | None = None
        self._last_active_mono = time.monotonic()

        self._httpd: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._idle_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()
        self._endpoint: ControlEndpoint | None = None

    @staticmethod
    def _resolve_instance_id() -> str:
        try:
            from ..account.identity import stable_instance_id
            return stable_instance_id()
        except Exception:  # noqa: BLE001
            return f"pid-{os.getpid()}"

    @property
    def endpoint(self) -> ControlEndpoint | None:
        return self._endpoint

    @property
    def shutdown_event(self) -> threading.Event:
        return self._shutdown_event

    def has_active_job(self) -> bool:
        """仅检查控制面自身是否有活跃作业（不回查外部宿主，避免循环调用）。"""
        with self._lock:
            return self._active_job is not None and not self._active_job.done_event.is_set()

    def is_busy(self) -> bool:
        if self.has_active_job():
            return True
        if self._external_busy_check is not None:
            try:
                if self._external_busy_check():
                    return True
            except Exception:  # noqa: BLE001
                pass
        return False

    def _touch(self) -> None:
        with self._lock:
            self._last_active_mono = time.monotonic()

    def start(self) -> ControlEndpoint:
        """启动 127.0.0.1:0 HTTP 服务并原子写出 control.json。"""
        if self._httpd is not None and self._endpoint is not None:
            return self._endpoint

        server = self
        handler_cls = _make_handler(server)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        httpd.daemon_threads = True
        port = int(httpd.server_address[1])
        endpoint = ControlEndpoint(
            pid=os.getpid(),
            instance_id=self._instance_id,
            port=port,
            token=self._token,
            mode=self._mode,
            started_at=now_iso(),
        )
        self._httpd = httpd
        self._endpoint = endpoint
        self._last_active_mono = time.monotonic()

        write_control_file(endpoint, self._control_path)

        t = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.2},
            name="lvjiang-control-http",
            daemon=True,
        )
        t.start()
        self._server_thread = t

        if self._idle_exit >= 0:
            idle_t = threading.Thread(
                target=self._idle_watch_loop,
                name="lvjiang-control-idle",
                daemon=True,
            )
            idle_t.start()
            self._idle_thread = idle_t

        logger.info(
            "[control] 本地控制面已启动: http://127.0.0.1:{} (mode={}, idle_exit={}s)",
            port, self._mode, self._idle_exit,
        )
        return endpoint

    def _idle_watch_loop(self) -> None:
        while not self._shutdown_event.wait(0.2):
            if self.is_busy():
                self._touch()
                continue
            with self._lock:
                elapsed = time.monotonic() - self._last_active_mono
                ran_any = self._has_run_any_job
            # idle_exit == 0：跑完首单后立刻退出（未收到首单前保留 5s 启动宽限）
            threshold = (0.0 if ran_any else 5.0) if self._idle_exit == 0.0 else self._idle_exit
            if elapsed >= threshold:
                logger.info("[control] 无头实例空闲 {:.1f}s，触发自动退出", elapsed)
                self._shutdown_event.set()
                return

    def stop(self) -> None:
        """停止活跃任务、关闭 HTTP 服务并安全清理 control.json。"""
        self._shutdown_event.set()
        with self._lock:
            active = self._active_job
        if active is not None:
            active.stop_event.set()
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        remove_control_file(self._control_path, expected_token=self._token)

    def _check_auth(self, header: str | None) -> bool:
        if not header or not header.startswith("Bearer "):
            return False
        provided = header[7:].strip()
        return hmac.compare_digest(provided, self._token)

    def _submit_job(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self._touch()
        if not isinstance(payload, dict):
            return HTTPStatus.BAD_REQUEST, {"ok": False, "error": "请求体必须是 JSON 对象"}

        with self._lock:
            if self._active_job is not None and not self._active_job.done_event.is_set():
                return HTTPStatus.CONFLICT, {
                    "ok": False,
                    "code": "busy",
                    "error": "主实例正在执行其他自动化任务",
                    "current_job": self._active_job.summary(),
                }
            if self._external_busy_check is not None:
                try:
                    if self._external_busy_check():
                        return HTTPStatus.CONFLICT, {
                            "ok": False,
                            "code": "busy",
                            "error": "主实例界面正在执行自动化任务",
                            "current_job": None,
                        }
                except Exception:  # noqa: BLE001
                    pass

            job_id = secrets.token_hex(8)
            kind = str(payload.get("kind") or "run").strip().lower()
            label = str(
                payload.get("script_id")
                or payload.get("id")
                or payload.get("name")
                or payload.get("script_name")
                or payload.get("script_file")
                or kind
            )
            record = _JobRecord(
                job_id=job_id,
                kind=kind,
                label=label,
                payload=dict(payload),
                started_at=now_iso(),
            )
            self._active_job = record
            self._jobs[job_id] = record

        worker = threading.Thread(
            target=self._run_job_thread,
            args=(record,),
            name=f"lvjiang-job-{job_id[:6]}",
            daemon=True,
        )
        worker.start()
        return HTTPStatus.ACCEPTED, {
            "ok": True,
            "job_id": job_id,
            "status": record.status,
            "label": record.label,
        }

    def _run_job_thread(self, record: _JobRecord) -> None:
        worker_tid = threading.get_ident()
        sink_id: int | None = None

        def _sink(message: Any) -> None:
            rec = message.record
            if rec["thread"].id != worker_tid:
                return
            record.push_event(
                "log",
                {
                    "level": rec["level"].name,
                    "time": rec["time"].strftime("%H:%M:%S"),
                    "message": rec["message"],
                },
            )

        try:
            sink_id = logger.add(_sink, level="INFO")
        except Exception:  # noqa: BLE001
            sink_id = None

        timeout = float(record.payload.get("timeout") or 0.0)
        timer: threading.Timer | None = None
        if timeout > 0:
            timer = threading.Timer(timeout, record.stop_event.set)
            timer.daemon = True
            timer.start()

        outcome: dict[str, Any]
        try:
            outcome = self._job_runner(record.payload, record.stop_event, record.push_event)
        except CliError as exc:
            outcome = {
                "ok": False,
                "code": exc.code,
                "error": str(exc),
            }
        except Exception as exc:  # noqa: BLE001
            logger.opt(exception=exc).error("[control] 作业 {} 执行异常: {}", record.job_id, exc)
            outcome = {
                "ok": False,
                "code": EXIT_RUNTIME,
                "error": str(exc),
            }
        finally:
            if timer is not None:
                timer.cancel()
            if sink_id is not None:
                try:
                    logger.remove(sink_id)
                except Exception:  # noqa: BLE001
                    pass

        code = int(outcome.get("code", EXIT_OK if outcome.get("ok") else EXIT_RUNTIME))
        if record.stop_event.is_set() and not outcome.get("ok"):
            status = "interrupted"
        elif outcome.get("ok") and code == EXIT_OK:
            status = "succeeded"
        else:
            status = "failed"

        record.status = status
        record.finished_at = now_iso()
        record.outcome = outcome
        record.push_event("done", {"status": status, "code": code, "outcome": outcome})
        record.done_event.set()

        with self._lock:
            self._last_job = record
            self._has_run_any_job = True
            if self._active_job is record:
                self._active_job = None
            self._finished_job_order.append(record.job_id)
            while len(self._finished_job_order) > 20:
                old_id = self._finished_job_order.pop(0)
                if self._active_job is None or self._active_job.job_id != old_id:
                    self._jobs.pop(old_id, None)
            self._last_active_mono = time.monotonic()

    def _cancel_job(self, job_id: str = "") -> tuple[int, dict[str, Any]]:
        self._touch()
        with self._lock:
            target = self._jobs.get(job_id) if job_id else self._active_job
        if target is None or target.done_event.is_set():
            return HTTPStatus.OK, {"ok": True, "cancelled": False, "message": "当前没有运行中的任务"}
        target.stop_event.set()
        target.push_event("log", {"level": "WARNING", "message": "已收到停止请求，正在中止任务…"})
        return HTTPStatus.OK, {"ok": True, "cancelled": True, "job_id": target.job_id}

    def _status_payload(self) -> dict[str, Any]:
        self._touch()
        with self._lock:
            active = self._active_job.summary() if self._active_job is not None else None
            last = self._last_job.summary() if self._last_job is not None else None
        return {
            "ok": True,
            "instance_id": self._instance_id,
            "pid": os.getpid(),
            "mode": self._mode,
            "busy": self.is_busy(),
            "current_job": active,
            "last_job": last,
        }


def _make_handler(server: ControlServer) -> type[BaseHTTPRequestHandler]:
    class _ControlHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def _require_auth(self) -> bool:
            if not server._check_auth(self.headers.get("Authorization")):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "未授权的控制面访问"})
                return False
            return True

        def _read_json(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return None
            return parsed if isinstance(parsed, dict) else None

        def do_GET(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            qs = parse_qs(parsed.query)

            if path == "/control/health":
                self._send_json(HTTPStatus.OK, server._status_payload())
                return
            if path == "/control/status":
                self._send_json(HTTPStatus.OK, server._status_payload())
                return
            if path == "/control/catalog":
                kind = (qs.get("kind") or [""])[0]
                script_id = (qs.get("id") or [""])[0]
                name = (qs.get("name") or [""])[0]
                try:
                    data = server._catalog_provider(kind=kind, script_id=script_id, name=name)
                except Exception as exc:  # noqa: BLE001
                    self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, data)
                return

            if path.startswith("/control/jobs/"):
                rest = path[len("/control/jobs/"):]
                parts = rest.split("/")
                job_id = parts[0]
                with server._lock:
                    record = server._jobs.get(job_id)
                if record is None:
                    self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"任务不存在: {job_id}"})
                    return
                if len(parts) == 1:
                    self._send_json(HTTPStatus.OK, {"ok": True, "job": record.summary()})
                    return
                if len(parts) == 2 and parts[1] == "events":
                    self._stream_events(record)
                    return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"未知路径: {path}"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._require_auth():
                return
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"

            if path == "/control/jobs":
                body = self._read_json()
                if body is None:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "无效的 JSON 请求体"})
                    return
                status, resp = server._submit_job(body)
                self._send_json(status, resp)
                return

            if path == "/control/stop":
                status, resp = server._cancel_job("")
                self._send_json(status, resp)
                return

            if path.startswith("/control/jobs/") and path.endswith("/cancel"):
                job_id = path[len("/control/jobs/"):-len("/cancel")].strip("/")
                status, resp = server._cancel_job(job_id)
                self._send_json(status, resp)
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"未知路径: {path}"})

        def _stream_events(self, record: _JobRecord) -> None:
            self.send_response(int(HTTPStatus.OK))
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            cursor = 0
            try:
                while True:
                    with record.events_cond:
                        while cursor >= len(record.events) and not record.done_event.is_set():
                            record.events_cond.wait(timeout=1.0)
                            if cursor >= len(record.events) and not record.done_event.is_set():
                                self.wfile.write(b": keepalive\n\n")
                                self.wfile.flush()
                        batch = record.events[cursor:]
                        cursor = len(record.events)
                        finished = record.done_event.is_set()
                    for ev_name, ev_data in batch:
                        chunk = (
                            f"event: {ev_name}\n"
                            f"data: {json.dumps(ev_data, ensure_ascii=False)}\n\n"
                        ).encode("utf-8")
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    if finished and cursor >= len(record.events):
                        break
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    return _ControlHandler


# ── 进程级单例辅助（供 GUI / 无头入口挂载） ─────────────────────────
_active_lock = threading.Lock()
_active_server: ControlServer | None = None


def get_active_control_server() -> ControlServer | None:
    with _active_lock:
        return _active_server


def start_control_server(**kwargs: Any) -> ControlEndpoint:
    global _active_server
    with _active_lock:
        if _active_server is not None and _active_server.endpoint is not None:
            return _active_server.endpoint
        srv = ControlServer(**kwargs)
        endpoint = srv.start()
        _active_server = srv
        return endpoint


def stop_control_server() -> None:
    global _active_server
    with _active_lock:
        srv = _active_server
        _active_server = None
    if srv is not None:
        srv.stop()
