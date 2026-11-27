"""纯标准库薄壳 CLI 客户端（lvjiang-cli）。

硬约束：本模块**只允许导入 Python 标准库**，严禁导入 ``lvjiang.*``、PyQt6、
OCR、OpenCV、numpy、loguru、yaml 等任何重依赖，确保独立打包的 ``lvjiang-cli``
体积仅约 1–2 MB 且冷启动极快。

职责：
1. 发现本机活跃的 ``lvjiang`` 主实例（读取 ``config/session/control.json`` 并探活）；
2. 若主实例未启动，自动以 ``--headless-server --idle-exit <N>`` 拉起无头主实例；
3. 将单任务（.wf / 插件 class 工作流）或批量任务提交给主实例执行：
   - 遇 ``409 busy`` 且 ``--on-busy fail``（默认）：立刻以退出码 7 返回；
   - 遇 ``409 busy`` 且 ``--on-busy wait``：在 CLI 侧轮询重试，不向主实例引入队列；
4. 通过 SSE 实时输出日志与进度，捕获 ``SIGINT``/``SIGTERM`` 时发送优雅中止请求；
5. 返回标准退出码（0 成功 / 2 参数 / 3 未找到 / 4 环境 / 5 运行失败 / 6 中断 / 7 繁忙 / 8 超时），
   并兼容旧版 ``--list`` / ``--exec`` 扁平参数。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_ENVIRONMENT = 4
EXIT_RUNTIME = 5
EXIT_INTERRUPTED = 6
EXIT_BUSY = 7
EXIT_TIMEOUT = 8

CONTROL_REL_PATH = Path("config") / "session" / "control.json"
DEFAULT_IDLE_EXIT = 30.0
DEFAULT_START_TIMEOUT = 25.0
BUSY_RETRY_INTERVAL = 1.5


def _force_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _resolve_root(explicit_root: str = "") -> Path:
    if explicit_root:
        return Path(explicit_root).resolve()
    env_root = os.environ.get("LVJIANG_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 开发环境：优先当前目录（含 config/），否则回溯源码仓根
    cwd = Path.cwd()
    if (cwd / "config").is_dir():
        return cwd
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "config").is_dir():
        return repo_root
    return cwd


def _control_path(root: Path) -> Path:
    return root / CONTROL_REL_PATH


def _read_control(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        port = int(data["port"])
        token = str(data.get("token") or "").strip()
    except (KeyError, TypeError, ValueError):
        return None
    if port <= 0 or port > 65535 or not token:
        return None
    return data


def _http_json(
    port: int,
    token: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        headers = {"Authorization": f"Bearer {token}"}
        raw: bytes | None = None
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            headers["Content-Length"] = str(len(raw))
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        try:
            parsed = json.loads(data.decode("utf-8")) if data else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = {"ok": False, "error": data.decode("utf-8", errors="replace")}
        return resp.status, parsed if isinstance(parsed, dict) else {"ok": False}
    finally:
        conn.close()


def _probe_health(ctrl: dict[str, Any], *, timeout: float = 0.6) -> dict[str, Any] | None:
    try:
        status, data = _http_json(
            int(ctrl["port"]),
            str(ctrl["token"]),
            "GET",
            "/control/health",
            timeout=timeout,
        )
    except OSError:
        return None
    if status != 200 or not data.get("ok"):
        return None
    expected_id = str(ctrl.get("instance_id") or "")
    actual_id = str(data.get("instance_id") or "")
    if expected_id and actual_id and expected_id != actual_id:
        return None
    return data


def _find_main_binary(root: Path) -> list[str]:
    """定位 lvjiang 主进程可执行文件（兼容冻结包与源码开发态、三端平台）。"""
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates = (
            [exe_dir / "lvjiang.exe", root / "lvjiang.exe"]
            if sys.platform == "win32"
            else [exe_dir / "lvjiang", root / "lvjiang"]
        )
        for cand in candidates:
            if cand.is_file():
                return [str(cand)]
    return [sys.executable, "-m", "lvjiang"]


def _spawn_headless_server(root: Path, *, idle_exit: float) -> None:
    cmd = [
        *_find_main_binary(root),
        "--headless-server",
        "--all-plugins",
        "--idle-exit",
        str(idle_exit),
    ]
    env = dict(os.environ)
    env["LVJIANG_ROOT"] = str(root)
    env.pop("_MEIPASS2", None)
    orig_ld = env.pop("LD_LIBRARY_PATH_ORIG", None)
    if orig_ld:
        env["LD_LIBRARY_PATH"] = orig_ld
    elif orig_ld is not None or getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)

    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)


def _ensure_control(
    root: Path,
    *,
    autostart: bool,
    idle_exit: float,
    start_timeout: float,
    quiet: bool,
) -> dict[str, Any] | None:
    path = _control_path(root)
    ctrl = _read_control(path)
    if ctrl is not None and _probe_health(ctrl) is not None:
        return ctrl
    if not autostart:
        return None

    if not quiet:
        sys.stderr.write("[lvjiang-cli] 未检测到运行中的主实例，正在以无头模式启动…\n")
        sys.stderr.flush()
    _spawn_headless_server(root, idle_exit=idle_exit)

    deadline = time.monotonic() + max(1.0, start_timeout)
    while time.monotonic() < deadline:
        ctrl = _read_control(path)
        if ctrl is not None and _probe_health(ctrl) is not None:
            return ctrl
        time.sleep(0.25)
    return None


def _parse_kv_params(pairs: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"参数格式应为 key=value，收到 {pair!r}")
        key, val = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"参数名为空: {pair!r}")
        result[key] = val
    return result


def _emit_json(data: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def _stream_job_events(
    ctrl: dict[str, Any],
    job_id: str,
    *,
    as_json: bool,
    quiet: bool,
    interrupted_flag: list[bool],
) -> tuple[int, dict[str, Any]]:
    port = int(ctrl["port"])
    token = str(ctrl["token"])
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=None)
    final_outcome: dict[str, Any] = {"ok": False, "code": EXIT_RUNTIME, "error": "连接提前断开"}
    final_code = EXIT_RUNTIME
    try:
        conn.request(
            "GET",
            f"/control/jobs/{job_id}/events",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            raw = resp.read().decode("utf-8", errors="replace")
            return EXIT_RUNTIME, {"ok": False, "code": EXIT_RUNTIME, "error": raw}

        event_name = ""
        data_lines: list[str] = []
        while True:
            raw_line = resp.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith(":"):
                continue
            if line == "":
                if data_lines:
                    raw_data = "\n".join(data_lines)
                    try:
                        payload = json.loads(raw_data)
                    except json.JSONDecodeError:
                        payload = {"message": raw_data}
                    if event_name == "log" and not quiet and not as_json:
                        ts = payload.get("time", "")
                        lvl = payload.get("level", "INFO")
                        msg = payload.get("message", "")
                        prefix = f"{ts} | {lvl:<7} | " if ts else f"{lvl:<7} | "
                        sys.stderr.write(f"{prefix}{msg}\n")
                        sys.stderr.flush()
                    elif event_name == "progress" and not quiet and not as_json:
                        sys.stderr.write(
                            f"[进度] 第{payload.get('round', 1)}轮 "
                            f"{payload.get('entry', '')} / {payload.get('script_id', '')} -> "
                            f"{payload.get('status', '')}\n"
                        )
                        sys.stderr.flush()
                    elif event_name == "done":
                        final_outcome = payload.get("outcome") or payload
                        final_code = int(payload.get("code", EXIT_OK if final_outcome.get("ok") else EXIT_RUNTIME))
                        break
                event_name = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
    except OSError as exc:
        if interrupted_flag[0]:
            return EXIT_INTERRUPTED, {"ok": False, "code": EXIT_INTERRUPTED, "error": "运行已中断"}
        return EXIT_RUNTIME, {"ok": False, "code": EXIT_RUNTIME, "error": f"事件流中断: {exc}"}
    finally:
        conn.close()
    return final_code, final_outcome


def _submit_and_wait(
    root: Path,
    payload: dict[str, Any],
    *,
    on_busy: str,
    wait_timeout: float,
    idle_exit: float,
    start_timeout: float,
    as_json: bool,
    quiet: bool,
) -> int:
    ctrl = _ensure_control(
        root,
        autostart=True,
        idle_exit=idle_exit,
        start_timeout=start_timeout,
        quiet=quiet or as_json,
    )
    if ctrl is None:
        err = {"ok": False, "code": EXIT_TIMEOUT, "error": "等待无头主实例启动超时"}
        if as_json:
            _emit_json(err)
        else:
            sys.stderr.write(f"[错误] {err['error']}\n")
        return EXIT_TIMEOUT

    interrupted = [False]
    active_job_id = [""]
    active_ctrl = [ctrl]

    def _on_signal(_signum: int, _frame: Any) -> None:
        interrupted[0] = True
        if active_job_id[0] and active_ctrl[0]:
            try:
                _http_json(
                    int(active_ctrl[0]["port"]),
                    str(active_ctrl[0]["token"]),
                    "POST",
                    f"/control/jobs/{active_job_id[0]}/cancel",
                    timeout=2.0,
                )
            except OSError:
                pass

    prev_handlers: dict[int, Any] = {}
    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                prev_handlers[sig] = signal.getsignal(sig)
                signal.signal(sig, _on_signal)
            except (OSError, ValueError):
                pass

    try:
        deadline = time.monotonic() + wait_timeout if wait_timeout > 0 else 0.0
        announced_wait = False
        while True:
            if interrupted[0]:
                out = {"ok": False, "code": EXIT_INTERRUPTED, "error": "等待期间已中断"}
                if as_json:
                    _emit_json(out)
                return EXIT_INTERRUPTED
            try:
                status, resp = _http_json(
                    int(ctrl["port"]),
                    str(ctrl["token"]),
                    "POST",
                    "/control/jobs",
                    payload,
                    timeout=10.0,
                )
            except OSError:
                # 主实例可能刚退出，重新发现或拉起
                new_ctrl = _ensure_control(
                    root,
                    autostart=True,
                    idle_exit=idle_exit,
                    start_timeout=start_timeout,
                    quiet=quiet or as_json,
                )
                if new_ctrl is None:
                    out = {"ok": False, "code": EXIT_TIMEOUT, "error": "主实例连接中断且拉起超时"}
                    if as_json:
                        _emit_json(out)
                    else:
                        sys.stderr.write(f"[错误] {out['error']}\n")
                    return EXIT_TIMEOUT
                ctrl = new_ctrl
                active_ctrl[0] = ctrl
                continue

            if status == 202 and resp.get("ok"):
                job_id = str(resp["job_id"])
                active_job_id[0] = job_id
                code, outcome = _stream_job_events(
                    ctrl, job_id, as_json=as_json, quiet=quiet, interrupted_flag=interrupted,
                )
                if as_json:
                    _emit_json(outcome)
                elif not quiet:
                    if outcome.get("ok"):
                        sys.stdout.write(f"[完成] {resp.get('label', job_id)}\n")
                    else:
                        sys.stderr.write(f"[失败] {outcome.get('error') or '执行失败'}\n")
                    sys.stdout.flush()
                    sys.stderr.flush()
                return code

            if status == 409 and resp.get("code") == "busy":
                if on_busy != "wait":
                    out = {
                        "ok": False,
                        "code": EXIT_BUSY,
                        "error": resp.get("error") or "主实例繁忙",
                        "current_job": resp.get("current_job"),
                    }
                    if as_json:
                        _emit_json(out)
                    else:
                        cur = resp.get("current_job") or {}
                        cur_lbl = cur.get("label") or "运行中任务"
                        sys.stderr.write(f"[繁忙] 主实例正在执行「{cur_lbl}」，可使用 --on-busy wait 排队等待\n")
                    return EXIT_BUSY
                if deadline and time.monotonic() >= deadline:
                    out = {"ok": False, "code": EXIT_TIMEOUT, "error": "等待主实例空闲超时"}
                    if as_json:
                        _emit_json(out)
                    else:
                        sys.stderr.write(f"[超时] {out['error']}\n")
                    return EXIT_TIMEOUT
                if not announced_wait and not quiet and not as_json:
                    cur = resp.get("current_job") or {}
                    cur_lbl = cur.get("label") or "其他任务"
                    sys.stderr.write(f"[等待] 主实例正在执行「{cur_lbl}」，持续重试提交中…\n")
                    sys.stderr.flush()
                    announced_wait = True
                time.sleep(BUSY_RETRY_INTERVAL)
                continue

            # 其他错误（400/404/500）
            err_code = EXIT_NOT_FOUND if status == 404 else (EXIT_USAGE if status == 400 else EXIT_RUNTIME)
            out = {"ok": False, "code": err_code, "error": resp.get("error") or f"HTTP {status}"}
            if as_json:
                _emit_json(out)
            else:
                sys.stderr.write(f"[错误] {out['error']}\n")
            return err_code
    finally:
        for sig, prev in prev_handlers.items():
            try:
                signal.signal(sig, prev)
            except (OSError, ValueError):
                pass


def _cmd_list(args: argparse.Namespace, root: Path) -> int:
    ctrl = _ensure_control(
        root,
        autostart=True,
        idle_exit=args.idle_exit,
        start_timeout=args.start_timeout,
        quiet=args.quiet or args.json,
    )
    if ctrl is None:
        err = {"ok": False, "code": EXIT_TIMEOUT, "error": "等待无头主实例启动超时"}
        if args.json:
            _emit_json(err)
        else:
            sys.stderr.write(f"[错误] {err['error']}\n")
        return EXIT_TIMEOUT
    qs = urlencode({k: v for k, v in (
        ("kind", args.kind or ""),
        ("id", args.id or ""),
        ("name", args.name or ""),
    ) if v})
    path = f"/control/catalog?{qs}" if qs else "/control/catalog"
    status, data = _http_json(int(ctrl["port"]), str(ctrl["token"]), "GET", path, timeout=10.0)
    if status != 200 or not data.get("ok"):
        if args.json:
            _emit_json(data)
        else:
            sys.stderr.write(f"[错误] {data.get('error') or f'HTTP {status}'}\n")
        return EXIT_RUNTIME
    if args.json:
        _emit_json(data)
        return EXIT_OK

    scripts = data.get("scripts") or []
    batches = data.get("batches") or []
    if scripts:
        sys.stdout.write("=== 脚本与工作流 ===\n")
        for s in scripts:
            sys.stdout.write(f"  [{s.get('kind', 'wf'):5s}] {s.get('id', '')}  {s.get('name', '')}\n")
    if batches:
        sys.stdout.write("=== 批量配置组 ===\n")
        for b in batches:
            sys.stdout.write(
                f"  [batch] {b.get('id', '')}  {b.get('name', '')} "
                f"({len(b.get('task_ids') or [])} 个任务)\n"
            )
    if not scripts and not batches:
        sys.stdout.write("（无可匹配项）\n")
    sys.stdout.flush()
    return EXIT_OK


def _cmd_status(args: argparse.Namespace, root: Path) -> int:
    ctrl = _ensure_control(
        root,
        autostart=False,
        idle_exit=0.0,
        start_timeout=1.0,
        quiet=True,
    )
    if ctrl is None:
        out = {"ok": True, "running": False, "busy": False, "current_job": None}
        if args.json:
            _emit_json(out)
        else:
            sys.stdout.write("主实例未运行\n")
        return EXIT_OK
    status, data = _http_json(int(ctrl["port"]), str(ctrl["token"]), "GET", "/control/status", timeout=3.0)
    if status != 200:
        if args.json:
            _emit_json(data)
        return EXIT_RUNTIME
    data["running"] = True
    if args.json:
        _emit_json(data)
        return EXIT_OK
    cur = data.get("current_job")
    state = f"繁忙（{cur.get('label')} / {cur.get('job_id')}）" if data.get("busy") and cur else (
        "繁忙（界面任务运行中）" if data.get("busy") else "空闲"
    )
    sys.stdout.write(
        f"主实例运行中 (pid={data.get('pid')}, mode={data.get('mode')}, port={ctrl['port']}) — {state}\n"
    )
    return EXIT_OK


def _cmd_stop(args: argparse.Namespace, root: Path) -> int:
    ctrl = _ensure_control(
        root,
        autostart=False,
        idle_exit=0.0,
        start_timeout=1.0,
        quiet=True,
    )
    if ctrl is None:
        out = {"ok": True, "cancelled": False, "message": "主实例未运行"}
        if args.json:
            _emit_json(out)
        else:
            sys.stdout.write("主实例未运行\n")
        return EXIT_OK
    path = f"/control/jobs/{args.job}/cancel" if args.job else "/control/stop"
    status, data = _http_json(int(ctrl["port"]), str(ctrl["token"]), "POST", path, timeout=5.0)
    if args.json:
        _emit_json(data)
    else:
        sys.stdout.write("已发送停止信号\n" if data.get("cancelled") else f"{data.get('message', '无运行中任务')}\n")
    return EXIT_OK if status == 200 else EXIT_RUNTIME


def _normalize_legacy_argv(argv: Sequence[str]) -> list[str]:
    """兼容旧版扁平参数：--list -> list，--exec -> run。"""
    args = list(argv)
    if not args:
        return args
    if args[0] in {"list", "run", "batch", "status", "stop"}:
        return args
    if "--list" in args:
        return ["list", *[a for a in args if a != "--list"]]
    if "--exec" in args:
        return ["run", *[a for a in args if a != "--exec"]]
    return args


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--root", default="", help="指定 lvjiang 安装/工作根目录")
    p.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    p.add_argument("--quiet", "-q", action="store_true", help="静默模式，不输出过程日志")
    p.add_argument(
        "--idle-exit",
        type=float,
        default=DEFAULT_IDLE_EXIT,
        help=f"自动拉起无头实例的空闲退出秒数（默认 {int(DEFAULT_IDLE_EXIT)}）",
    )
    p.add_argument(
        "--start-timeout",
        type=float,
        default=DEFAULT_START_TIMEOUT,
        help=f"等待无头主实例就绪的超时秒数（默认 {int(DEFAULT_START_TIMEOUT)}）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lvjiang-cli",
        description="lvjiang 薄壳命令行工具：向运行中主实例（或自动拉起的无头实例）提交自动化任务",
    )
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list", help="列出可执行的 .wf、插件 class 工作流与批量配置组")
    _add_common_flags(p_list)
    p_list.add_argument("--kind", choices=["all", "wf", "class", "batch"], default="all")
    p_list.add_argument("--id", default="", help="按 ID 过滤")
    p_list.add_argument("--name", default="", help="按名称过滤")

    p_run = sub.add_parser("run", help="执行单个 .wf 脚本或插件 class 工作流")
    _add_common_flags(p_run)
    p_run.add_argument("--id", default="", help="脚本 / 工作流 ID")
    p_run.add_argument("--name", default="", help="脚本 / 工作流名称")
    p_run.add_argument("--file", default="", help="直接执行本地 .wf 文件路径")
    p_run.add_argument("--user", default="", help="目标角色名")
    p_run.add_argument("--layout", default="", help="布局名称（默认 android）")
    p_run.add_argument("--target", default="auto", help="执行目标（auto / adb / adb:serial）")
    p_run.add_argument("-p", "--param", action="append", default=[], help="覆盖参数 key=value（可多次）")
    p_run.add_argument("--timeout", type=float, default=0.0, help="任务超时秒数（0 表示不限）")
    p_run.add_argument("--yes", action="store_true", help="自动确认 confirm 弹窗")
    p_run.add_argument("--dry-run", action="store_true", help="仅预检不执行")
    p_run.add_argument("--on-busy", choices=["fail", "wait"], default="fail", help="主实例繁忙策略")
    p_run.add_argument("--wait-timeout", type=float, default=0.0, help="wait 模式最大等待秒数（0 为不限）")

    p_batch = sub.add_parser("batch", help="执行批量配置组（BatchScheduler）")
    _add_common_flags(p_batch)
    p_batch.add_argument("--id", default="", help="批量配置组 ID")
    p_batch.add_argument("--name", default="", help="批量配置组名称")
    p_batch.add_argument("--users", default="", help="覆盖角色列表（逗号分隔）")
    p_batch.add_argument("--tasks", default="", help="覆盖任务 ID 列表（逗号分隔）")
    p_batch.add_argument("--rounds", type=int, default=None, help="覆盖轮次数")
    p_batch.add_argument("--unattended", action="store_true", default=None, help="启用无人值守模式")
    p_batch.add_argument("--yes", action="store_true", help="自动确认 confirm 弹窗")
    p_batch.add_argument("--layout", default="", help="布局名称（默认 android）")
    p_batch.add_argument("--target", default="auto", help="执行目标（auto / adb / adb:serial）")
    p_batch.add_argument("--timeout", type=float, default=0.0, help="批量超时秒数（0 表示不限）")
    p_batch.add_argument("--dry-run", action="store_true", help="仅预检批量计划不执行")
    p_batch.add_argument("--on-busy", choices=["fail", "wait"], default="fail", help="主实例繁忙策略")
    p_batch.add_argument("--wait-timeout", type=float, default=0.0, help="wait 模式最大等待秒数（0 为不限）")

    p_status = sub.add_parser("status", help="查看当前主实例与活跃任务状态")
    _add_common_flags(p_status)

    p_stop = sub.add_parser("stop", help="优雅中止当前正在运行的自动化任务")
    _add_common_flags(p_stop)
    p_stop.add_argument("--job", default="", help="指定任务 ID（默认停止当前活跃任务）")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_streams()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    norm_argv = _normalize_legacy_argv(raw_argv)
    parser = build_parser()
    if not norm_argv:
        parser.print_help()
        return EXIT_USAGE
    args = parser.parse_args(norm_argv)
    root = _resolve_root(getattr(args, "root", ""))

    if args.command == "list":
        return _cmd_list(args, root)
    if args.command == "status":
        return _cmd_status(args, root)
    if args.command == "stop":
        return _cmd_stop(args, root)
    if args.command == "run":
        try:
            params = _parse_kv_params(args.param)
        except ValueError as exc:
            sys.stderr.write(f"[错误] {exc}\n")
            return EXIT_USAGE
        script_file = (
            str(Path(args.file).expanduser().resolve())
            if args.file else None
        )
        payload: dict[str, Any] = {
            "kind": "run",
            "script_id": args.id,
            "script_name": args.name,
            "script_file": script_file,
            "user": args.user,
            "layout": args.layout,
            "target": args.target,
            "params": params,
            "timeout": args.timeout,
            "assume_yes": bool(args.yes),
            "dry_run": bool(args.dry_run),
        }
        return _submit_and_wait(
            root,
            payload,
            on_busy=args.on_busy,
            wait_timeout=args.wait_timeout,
            idle_exit=args.idle_exit,
            start_timeout=args.start_timeout,
            as_json=bool(args.json),
            quiet=bool(args.quiet),
        )
    if args.command == "batch":
        payload = {
            "kind": "batch",
            "group_id": args.id,
            "name": args.name,
            "users": [u.strip() for u in args.users.split(",") if u.strip()] if args.users else [],
            "tasks": [t.strip() for t in args.tasks.split(",") if t.strip()] if args.tasks else [],
            "rounds": args.rounds,
            "unattended": args.unattended,
            "assume_yes": bool(args.yes),
            "layout": args.layout,
            "target": args.target,
            "timeout": args.timeout,
            "dry_run": bool(args.dry_run),
        }
        return _submit_and_wait(
            root,
            payload,
            on_busy=args.on_busy,
            wait_timeout=args.wait_timeout,
            idle_exit=args.idle_exit,
            start_timeout=args.start_timeout,
            as_json=bool(args.json),
            quiet=bool(args.quiet),
        )
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
