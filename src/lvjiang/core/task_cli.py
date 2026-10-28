"""命令行执行工作流：参数解析、目标装配、无界面运行与结果输出。

用法::

    python -m lvjiang --script --id haoyun_lianji --user 宛元芷 --target adb
    python -m lvjiang --script --name 好运连叽摇骰子 --yes
    python -m lvjiang --script --file D:/tmp/临时脚本.wf --dry-run
    lvjiang-cli --id haoyun_lianji --target adb:192.168.0.100:5555 --json
    lvjiang-cli --list

命令行走 ``--`` 选项：``--script`` 只是「进入脚本执行模式」的开关，
``lvjiang-cli`` 下可以省略；脚本用 ``--id`` / ``--name`` / ``--file`` 三选一指定。

设计边界：
- 只做「一次运行」：参数解析 → 目标装配 → 引擎执行 → 结果落盘；
- 不启动 Qt，不读写界面偏好；
- 需要人工介入的指令（``pause``）直接终止本次运行，由调用方决定是否重试。
- 运行时装配仍然复用 ``WorkflowEngine`` 与 ``DeviceWorkflowEngineBuilder``，
  命令行只是另一种入口，不是第二套执行器。
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from loguru import logger

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_ENVIRONMENT = 4
EXIT_RUNTIME = 5
EXIT_INTERRUPTED = 6

CURRENT_CMD = "lvjiang-cli"


class CliError(RuntimeError):
    """命令行可直接呈现的失败；``code`` 决定进程退出码。"""

    def __init__(self, message: str, code: int = EXIT_RUNTIME) -> None:
        super().__init__(message)
        self.code = code


class HeadlessPause(RuntimeError):
    """工作流要求人工介入（``pause``），无人值守模式下不能继续。"""


class HeadlessCallbacks:
    """替代 Qt 主线程回调；绝不弹出原生对话框。"""

    def __init__(self, *, assume_yes: bool = False) -> None:
        self.assume_yes = assume_yes

    def __call__(self, action: str, **payload: Any) -> Any:
        message = str(payload.get("message") or payload.get("prompt") or "")
        if action == "confirm":
            logger.info("[交互] 确认: {} -> {}", message, self.assume_yes)
            return self.assume_yes
        if action == "pause":
            raise HeadlessPause(message or "工作流要求人工介入")
        if action == "notify":
            logger.warning("[通知] {}", message)
            return None
        if action == "input":
            logger.warning("[交互] 需要输入但当前无人值守，返回空: {}", message)
            return None
        logger.debug("[交互] 忽略未知动作: {}", action)
        return None


@dataclass
class RunRequest:
    script_id: str = ""
    script_name: str = ""
    script_file: str = ""
    user: str = ""
    layout: str = ""
    target: str = "auto"
    params: dict[str, Any] = field(default_factory=dict)
    timeout: float = 0.0
    assume_yes: bool = False
    dry_run: bool = False
    stop_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False)


@dataclass
class AdbTarget:
    serial: str
    device: Any
    capture: Any
    input_ctrl: Any
    agent: Any = None
    input_kind: str = "adb"

    def close(self) -> None:
        for item in (self.capture, self.agent):
            closer = getattr(item, "stop", None) or getattr(item, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception as error:  # noqa: BLE001
                    logger.debug("关闭后端失败: {}", error)


# ─── 参数 ────────────────────────────────────────────────────

def _truthy(text: str) -> bool:
    return text.strip().lower() in {"1", "true", "yes", "on"}


def _option_values(definition: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for option in definition.get("options") or []:
        if isinstance(option, str):
            values.add(option)
        elif isinstance(option, dict) and "value" in option:
            values.add(str(option["value"]))
    return values


def coerce_param(definition: dict[str, Any], raw: str) -> Any:
    """把命令行文本转成参数定义声明的类型。"""
    name = str(definition.get("name") or "")
    kind = str(definition.get("type") or "select")
    if kind == "number":
        try:
            value = int(raw)
        except ValueError as error:
            raise CliError(f"参数 {name} 需要整数，收到 {raw!r}", EXIT_USAGE) from error
        minimum = definition.get("min")
        maximum = definition.get("max")
        if isinstance(minimum, int) and value < minimum:
            raise CliError(f"参数 {name} 不能小于 {minimum}", EXIT_USAGE)
        if isinstance(maximum, int) and value > maximum:
            raise CliError(f"参数 {name} 不能大于 {maximum}", EXIT_USAGE)
        return value
    if kind == "bool":
        return _truthy(raw)
    if kind == "checkgroup":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise CliError(f"参数 {name} 需要 JSON 对象，收到 {raw!r}", EXIT_USAGE) from error
        if not isinstance(value, dict):
            raise CliError(f"参数 {name} 需要 JSON 对象，收到 {raw!r}", EXIT_USAGE)
        return {str(key): bool(item) for key, item in value.items()}
    if kind == "select":
        allowed = _option_values(definition)
        if allowed and raw not in allowed:
            raise CliError(
                f"参数 {name} 只能是 {' / '.join(sorted(allowed))}，收到 {raw!r}",
                EXIT_USAGE)
    return raw


def apply_overrides(
    definitions: Sequence[dict[str, Any]],
    overrides: dict[str, str],
) -> dict[str, Any]:
    """按声明强类型化命令参数；未声明的参数直接报错，避免静默失效。"""
    declared = {str(item.get("name")): item for item in definitions if item.get("name")}
    unknown = [name for name in overrides if name not in declared]
    if unknown:
        raise CliError(
            f"脚本没有声明参数: {', '.join(unknown)}；"
            f"可用参数: {', '.join(sorted(declared)) or '无'}",
            EXIT_USAGE)
    return {name: coerce_param(declared[name], value) for name, value in overrides.items()}


def parse_assignments(pairs: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise CliError(f"参数格式应为 KEY=VALUE，收到 {pair!r}", EXIT_USAGE)
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise CliError(f"参数格式应为 KEY=VALUE，收到 {pair!r}", EXIT_USAGE)
        result[key] = value
    return result


# ─── 脚本解析 ────────────────────────────────────────────────

def find_script(
    scripts: Sequence[dict[str, Any]], token: str, *, by_id: bool = False,
) -> dict[str, Any]:
    """按 id 或唯一显示名解析脚本；``by_id`` 时只接受 id。"""
    token = token.strip()
    if by_id or any(config.get("id") == token for config in scripts):
        for config in scripts:
            if config.get("id") == token:
                return config
        if by_id:
            raise CliError(f"没有找到脚本 id: {token}", EXIT_NOT_FOUND)
    matched = [config for config in scripts if config.get("name") == token]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise CliError(f"没有找到脚本: {token}", EXIT_NOT_FOUND)
    ids = ", ".join(sorted(str(item.get("id")) for item in matched))
    raise CliError(f"脚本名 {token} 对应多个脚本，请改用 --id: {ids}", EXIT_USAGE)


def resolve_selection(
    *, script_id: str = "", script_name: str = "", script_file: str = "",
) -> tuple[dict[str, Any], Path]:
    """把 ``--id/--name/--file`` 解析成脚本配置与 .wf 路径。

    ``--file`` 直接执行任意位置的 .wf；脚本内的 ``import`` 仍按 workflows 根
    （config/local → config/system）解析，与文件所在目录无关。
    """
    chosen = [value for value in (script_id, script_name, script_file) if value.strip()]
    if len(chosen) != 1:
        raise CliError("--id、--name、--file 必须且只能指定一个", EXIT_USAGE)

    if script_file.strip():
        file_path = Path(script_file.strip()).expanduser()
        if not file_path.is_absolute():
            file_path = (Path.cwd() / file_path).resolve()
        if not file_path.is_file() or file_path.suffix.lower() != ".wf":
            raise CliError(f"工作流文件不存在: {file_path}", EXIT_NOT_FOUND)
        from ..workflows.metadata import metadata_for_script_config, script_traits

        metadata, warning = metadata_for_script_config(file_path)
        if warning:
            raise CliError(f"脚本元数据无效: {warning}", EXIT_USAGE)
        traits = script_traits(metadata)
        return {
            "id": metadata.get("id") or file_path.stem,
            "name": metadata.get("name") or file_path.stem,
            "wf_file": str(file_path),
            "class": "",
            "parameters": metadata.get("parameters") or [],
            "env": metadata.get("env") or [],
            "scope": traits["scope"],
            "hidden": traits["hidden"],
            "batchable": traits["batchable"],
            "runnable": True,
            "source_layer": "file",
        }, file_path

    from ..workflows.discovery import discover_scripts, resolve_workflow_path

    scripts = discover_scripts()
    if script_id.strip():
        config = find_script(scripts, script_id, by_id=True)
    else:
        config = find_script(scripts, script_name)
    if config.get("class"):
        raise CliError(
            f"内置类脚本暂不支持命令行执行: {config.get('id')}；请改用 DSL 脚本或界面启动",
            EXIT_ENVIRONMENT)
    path, _wf_file = resolve_workflow_path(
        str(config.get("wf_file") or ""), str(config.get("id") or ""))
    if path is None:
        raise CliError(f"工作流文件不存在: {config.get('wf_file')}", EXIT_NOT_FOUND)
    return config, path


# ─── 目标 ────────────────────────────────────────────────────

def _resolve_serial(spec: str) -> str:
    from .android import list_adb_devices

    if spec in {"", "auto", "adb"}:
        devices = list_adb_devices()
        if not devices:
            raise CliError("没有检测到已连接的 ADB 设备", EXIT_ENVIRONMENT)
        return str(devices[0]["serial"])
    if spec.startswith("adb:"):
        serial = spec[4:].strip()
        if serial:
            return serial
        devices = list_adb_devices()
        if not devices:
            raise CliError("没有检测到已连接的 ADB 设备", EXIT_ENVIRONMENT)
        return str(devices[0]["serial"])
    raise CliError(f"暂只支持 adb 目标，收到 {spec!r}", EXIT_USAGE)


def open_adb_target(spec: str, user_config: Any) -> AdbTarget:
    from .android import (
        AdbDevice,
        connect_agent,
        create_capture_backend,
        create_input_backend,
    )

    serial = _resolve_serial(spec)
    device = AdbDevice(serial=serial)
    width, height = device.get_resolution()
    if width <= 0 or height <= 0:
        raise CliError(f"无法获取设备分辨率: {serial}", EXIT_ENVIRONMENT)
    device.get_stable_identity()

    agent = None
    if getattr(user_config, "android_input_method", "adb") == "device_gesture":
        agent = connect_agent(device)
        if agent is None:
            allowed = bool(getattr(user_config, "android_recovery", None)
                           and user_config.android_recovery.allow_adb_fallback)
            if not allowed:
                raise CliError("设备端代理不可用，且已禁用 ADB 输入降级", EXIT_ENVIRONMENT)
            logger.warning("设备端代理不可用，回退 adb shell input")

    method = str(getattr(user_config, "android_capture_method", "screencap") or "screencap")
    capture = create_capture_backend(device=device, method=method)
    if not capture.start():
        reason = str(getattr(capture, "last_error", "") or "")
        if agent is not None:
            agent.close()
        raise CliError(f"{method} 截图后端不可用{': ' + reason if reason else ''}",
                       EXIT_ENVIRONMENT)
    input_ctrl = create_input_backend(
        device=device, input_sim=getattr(user_config, "input_sim", None), agent=agent)
    return AdbTarget(
        serial=serial, device=device, capture=capture, input_ctrl=input_ctrl,
        agent=agent, input_kind="agent" if agent is not None else "adb")


# ─── 运行 ────────────────────────────────────────────────────

def _resolve_user(requested: str) -> str:
    from .user_config import load_user_metadata, resolve_active_username

    name = requested.strip() or resolve_active_username()
    if not name:
        raise CliError("没有可用的执行用户", EXIT_USAGE)
    if load_user_metadata(name) is None:
        raise CliError(f"用户不存在: {name}", EXIT_USAGE)
    return name


def _build_params(config: dict[str, Any], username: str,
                  overrides: dict[str, str]) -> dict[str, Any]:
    from .config.wf_configs import get_wf_config
    from .task_params import merge_task_params, resolve_task_params

    definitions = list(config.get("parameters") or [])
    if (config.get("scope") or "daily") == "daily":
        params, _source = resolve_task_params(
            str(config["id"]), username, definitions)
    else:
        params, _source = merge_task_params(
            definitions, get_wf_config(str(config["id"])), None)
    params.update(apply_overrides(definitions, overrides))
    return params


def _bind_user(engine: Any, username: str) -> None:
    from .config.users import SessionManager
    from .user_config import load_user_metadata

    manager = SessionManager()
    engine.session = manager.load(username)
    engine.run_username = username
    metadata = load_user_metadata(username)
    engine.user_attributes_snapshot = {
        username: dict(metadata.attributes) if metadata is not None else {}
    }
    engine._save_callback = manager.save_fn(username, engine.session)


def _save_result(username: str, script_id: str, started_at: datetime,
                 result: Any, task_run_id: str, interrupted: bool) -> Path | None:
    from .fs_util import task_result_path

    if not isinstance(result, (dict, list)):
        return None
    path = task_result_path(
        username, script_id, started_at,
        task_run_id=task_run_id, interrupted=interrupted)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    return path


def _outcome(*, ok: bool, config: dict[str, Any], username: str,
             target: str, layout: str, started: datetime,
             result: Any = None, result_path: Path | None = None,
             error: str = "", code: int = EXIT_OK) -> dict[str, Any]:
    return {
        "ok": ok,
        "code": code,
        "script": str(config.get("id") or ""),
        "name": str(config.get("name") or ""),
        "user": username,
        "target": target,
        "layout": layout,
        "started_at": started.isoformat(timespec="milliseconds"),
        "duration_ms": int((datetime.now().astimezone() - started).total_seconds() * 1000),
        "result_path": str(result_path) if result_path else "",
        "result": result,
        "error": error,
    }


def execute(request: RunRequest) -> dict[str, Any]:
    """执行一次脚本；失败通过 :class:`CliError` 表达。"""
    from ..workflows.engine.builders import DeviceWorkflowEngineBuilder

    started = datetime.now().astimezone()
    config, path = resolve_selection(
        script_id=request.script_id,
        script_name=request.script_name,
        script_file=request.script_file,
    )
    script_id = str(config.get("id") or "")
    name = str(config.get("name") or script_id)
    allowed_env = list(config.get("env") or [])
    if allowed_env and "android" not in allowed_env:
        raise CliError(f"脚本 {name} 只支持环境 {', '.join(allowed_env)}", EXIT_ENVIRONMENT)

    username = _resolve_user(request.user)
    layout_name = request.layout.strip() or "android"
    from .layout_storage import load_layout_by_key
    layout = load_layout_by_key(layout_name)
    if layout is None:
        raise CliError(f"布局不存在: {layout_name}", EXIT_ENVIRONMENT)
    params = _build_params(config, username, request.params)

    from .config import load_user_config
    user_config = load_user_config()

    if request.dry_run:
        from ..workflows.engine.core import WorkflowEngine
        engine = WorkflowEngine(
            capture=None, ocr=None, input_ctrl=None, layout=layout,
            input_sim=user_config.input_sim,
            delay_params=user_config.delay_params,
            android_apps=user_config.android_apps,
            run_env="android")
        engine.workflow_config_snapshot = _workflow_config(script_id)
        engine.validate_only(path)
        logger.info("[预检] {} 通过", name)
        return _outcome(ok=True, config=config, username=username,
                        target="dry-run", layout=layout_name, started=started)

    from .ocr import OCREngine
    target = open_adb_target(request.target, user_config)
    try:
        engine = DeviceWorkflowEngineBuilder(
            capture=target.capture,
            ocr=OCREngine(),
            input_ctrl=target.input_ctrl,
            layout=layout,
            input_sim=user_config.input_sim,
            delay_params=user_config.delay_params,
            android_apps=user_config.android_apps,
            android_device=target.device,
            run_env="android",
            stop_check=lambda: request.stop_event.is_set(),
        ).build()
        _bind_user(engine, username)
        engine.workflow_config_snapshot = _workflow_config(script_id)
        engine._ui_callback = HeadlessCallbacks(assume_yes=request.assume_yes)

        lease = _acquire(username)
        task_run = _start_history(
            username=username, config=config, params=params,
            target=target, layout=layout_name, started=started)
        try:
            with lease.authorized():
                result = engine.execute(path, initial_variables=params)
        except BaseException:
            _finish_history(task_run, "failed", result_path=None)
            raise
        finally:
            lease.release()
        interrupted = request.stop_event.is_set()
        result_path = _save_result(
            username, script_id, started, result,
            getattr(task_run, "task_run_id", ""), interrupted)
        _finish_history(task_run, "interrupted" if interrupted else "success",
                        result_path=result_path)
        if interrupted:
            return _outcome(ok=False, config=config, username=username,
                            target=target.serial, layout=layout_name, started=started,
                            result=result, result_path=result_path,
                            error="运行已中断", code=EXIT_INTERRUPTED)
        return _outcome(ok=True, config=config, username=username,
                        target=target.serial, layout=layout_name, started=started,
                        result=result, result_path=result_path)
    except HeadlessPause as error:
        raise CliError(f"工作流要求人工介入: {error}", EXIT_RUNTIME) from error
    finally:
        target.close()


def _workflow_config(script_id: str) -> dict[str, Any]:
    from .config.wf_configs import get_wf_config

    return get_wf_config(script_id) or {}


def _acquire(username: str) -> Any:
    from .access import AccessDeniedError, acquire_user

    try:
        return acquire_user(username)
    except AccessDeniedError as error:
        raise CliError(str(error), EXIT_ENVIRONMENT) from error


def _start_history(*, username: str, config: dict[str, Any], params: dict[str, Any],
                   target: AdbTarget, layout: str, started: datetime) -> Any:
    from .daily_history import try_create_task_run

    return try_create_task_run(
        username=username,
        task_id=str(config.get("id") or ""),
        task_name=str(config.get("name") or ""),
        task_scope=str(config.get("scope") or "daily"),
        params=params,
        source="cli",
        target_id=target.serial,
        target_kind="adb",
        target_label=target.serial,
        environment="android",
        layout=layout,
        input_kind=target.input_kind,
        started_at=started,
    )


def _finish_history(task_run: Any, status: str, *, result_path: Path | None) -> None:
    if task_run is None:
        return
    try:
        task_run.finish(status=status, result_path=result_path)
    except Exception as error:  # noqa: BLE001
        logger.warning("任务历史写入失败，继续收尾: {}", error)


# ─── 命令行 ──────────────────────────────────────────────────

Bootstrap = Callable[..., Any]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=CURRENT_CMD, description="律匠命令行执行入口")
    parser.add_argument("--read-only", action="store_true",
                        help="不争取主节点：不可安装更新包或回传历史")
    parser.add_argument("-reg", "--register", action="append", dest="apps",
                        metavar="APP", help="加载插件，可多次使用")
    parser.add_argument("--all", "--all-plugins", action="store_true", dest="all_plugins",
                        help="加载内置与 plugins/ 目录中的全部插件")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行脚本")
    run_sel = run.add_mutually_exclusive_group(required=True)
    run_sel.add_argument("--id", dest="script_id", default="", help="脚本 id")
    run_sel.add_argument("--name", dest="script_name", default="", help="脚本显示名")
    run_sel.add_argument("--file", dest="script_file", default="",
                         help="直接执行任意位置的 .wf")
    run.add_argument("-p", "--param", action="append", default=[], metavar="K=V",
                     help="覆盖脚本参数，可多次使用")
    run.add_argument("--params-json", default="", metavar="JSON",
                     help="以 JSON 对象提供参数，--param 优先")
    run.add_argument("--user", default="", help="执行用户，默认当前用户")
    run.add_argument("--layout", default="", help="布局 key，默认 android")
    run.add_argument("--target", default="auto",
                     help="执行目标：auto、adb 或 adb:<serial>")
    run.add_argument("--timeout", type=float, default=0.0,
                     help="超时秒数，0 表示不限")
    run.add_argument("--yes", action="store_true",
                     help="confirm() 一律返回 true")
    run.add_argument("--dry-run", action="store_true",
                     help="只做静态校验，不连接设备")
    run.add_argument("--json", action="store_true", dest="as_json",
                     help="stdout 输出单行 JSON 结果")
    run.add_argument("--result", default="", metavar="PATH",
                     help="额外把结果写到指定路径")
    run.add_argument("--quiet", action="store_true", help="只输出错误")
    run.add_argument("--log-level", default="",
                     choices=["", "DEBUG", "INFO", "WARNING", "ERROR"],
                     help="控制台日志级别")

    listing = sub.add_parser("list", help="列出可运行脚本")
    listing.add_argument("--json", action="store_true", dest="as_json")
    listing.add_argument("--quiet", action="store_true")

    schema = sub.add_parser("schema", help="输出脚本参数定义")
    schema_sel = schema.add_mutually_exclusive_group(required=True)
    schema_sel.add_argument("--id", dest="script_id", default="", help="脚本 id")
    schema_sel.add_argument("--name", dest="script_name", default="", help="脚本显示名")
    schema.add_argument("--json", action="store_true", dest="as_json")
    schema.add_argument("--quiet", action="store_true")
    return parser


def _force_utf8_streams() -> None:
    """命令行输出固定 UTF-8：中文 Windows 默认 cp936，管道与 JSON 解析会乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _console_level(args: argparse.Namespace) -> str:
    if getattr(args, "log_level", ""):
        return str(args.log_level)
    return "ERROR" if getattr(args, "quiet", False) else "INFO"


def _emit_outcome(outcome: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(outcome, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _print_human(outcome: dict[str, Any]) -> None:
    if outcome.get("ok"):
        line = f"[完成] {outcome['name']} ({outcome['script']})"
    else:
        line = f"[失败] {outcome.get('name') or outcome.get('script')}: {outcome.get('error')}"
    extra = f" 用户={outcome['user']} 目标={outcome['target']}"
    timing = f" 用时={outcome['duration_ms'] / 1000:.1f}s"
    sys.stdout.write(line + extra + timing + "\n")
    if outcome.get("result_path"):
        sys.stdout.write(f"结果: {outcome['result_path']}\n")
    sys.stdout.flush()


def _list_scripts(as_json: bool) -> int:
    from ..workflows.discovery import discover_scripts

    scripts = [item for item in discover_scripts() if item.get("runnable")]
    rows: list[dict[str, Any]] = [{
        "id": item.get("id"),
        "name": item.get("name"),
        "scope": item.get("scope") or "daily",
        "env": [str(value) for value in (item.get("env") or [])],
        "batchable": bool(item.get("batchable")),
        "group": item.get("group") or "",
        "source": item.get("source_layer") or "",
    } for item in scripts]
    rows.sort(key=lambda row: str(row["id"]))
    if as_json:
        sys.stdout.write(json.dumps(rows, ensure_ascii=False) + "\n")
    else:
        for row in rows:
            env = ",".join(row["env"]) or "-"
            sys.stdout.write(
                f"{row['id']:<24} {row['name']:<16} "
                f"{row['scope']:<9} {env:<8} {row['group']}\n")
    sys.stdout.flush()
    return EXIT_OK


def _print_schema(script_id: str, script_name: str, as_json: bool) -> int:
    from ..workflows.discovery import discover_scripts

    scripts = discover_scripts()
    config = (find_script(scripts, script_id, by_id=True) if script_id.strip()
              else find_script(scripts, script_name))
    parameters: list[dict[str, Any]] = [
        item for item in (config.get("parameters") or []) if isinstance(item, dict)]
    note = str(config.get("note") or "")
    payload: dict[str, Any] = {
        "id": config.get("id"),
        "name": config.get("name"),
        "scope": config.get("scope") or "daily",
        "env": [str(value) for value in (config.get("env") or [])],
        "note": note,
        "parameters": parameters,
    }
    if as_json:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"{payload['id']}  {payload['name']}\n")
        if note:
            sys.stdout.write(f"{note}\n")
        for item in parameters:
            kind = item.get("type") or "select"
            default = item.get("default")
            suffix = f" 默认={default}" if default is not None else ""
            sys.stdout.write(
                f"  {item.get('name'):<20} {kind:<10}{suffix}  {item.get('label') or ''}\n")
    sys.stdout.flush()
    return EXIT_OK


def _parse_params(args: argparse.Namespace) -> dict[str, Any]:
    raw: dict[str, str] = {}
    if getattr(args, "params_json", ""):
        try:
            data = json.loads(args.params_json)
        except json.JSONDecodeError as error:
            raise CliError(f"--params-json 不是合法 JSON: {error}", EXIT_USAGE) from error
        if not isinstance(data, dict):
            raise CliError("--params-json 必须是 JSON 对象", EXIT_USAGE)
        raw.update({str(key): str(value) for key, value in data.items()})
    raw.update(parse_assignments(args.param or []))
    return raw


def _install_signal(stop: threading.Event) -> Callable[[], None]:
    def handler(signum, frame):  # noqa: ARG001
        if stop.is_set():
            raise KeyboardInterrupt
        stop.set()
        logger.warning("收到中断信号，正在停止当前任务…")

    previous = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, handler)
    except ValueError:  # 非主线程（理论上不会发生）
        return lambda: None

    def restore() -> None:
        signal.signal(signal.SIGINT, previous)

    return restore


def cli_main(argv: Sequence[str], *, bootstrap: Bootstrap) -> int:
    """命令行入口；``bootstrap`` 由 ``__main__`` 提供，负责插件与日志装配。"""
    _force_utf8_streams()
    parser = build_parser()
    try:
        args = parser.parse_args(list(argv))
    except SystemExit as exit_error:
        return int(exit_error.code or EXIT_USAGE)

    # 命令行与安装后的界面一致：默认加载内置插件与 plugins/ 目录，
    # 否则脚本引用的插件场景/实现会缺失。
    args.all_plugins = True

    if args.command in {"list", "schema"}:
        args.read_only = True
        bootstrap(args, console_level=_console_level(args), recover=False)
        try:
            if args.command == "list":
                return _list_scripts(bool(getattr(args, "as_json", False)))
            return _print_schema(
                args.script_id, args.script_name,
                bool(getattr(args, "as_json", False)))
        except CliError as error:
            logger.error("{}", error)
            return error.code

    try:
        raw_params = _parse_params(args)
        request = RunRequest(
            script_id=args.script_id,
            script_name=args.script_name,
            script_file=args.script_file,
            user=args.user,
            layout=args.layout,
            target=args.target,
            params=raw_params,
            timeout=max(0.0, float(args.timeout or 0.0)),
            assume_yes=bool(args.yes),
            dry_run=bool(args.dry_run),
        )
    except CliError as error:
        logger.error("{}", error)
        return error.code

    bootstrap(args, console_level=_console_level(args), recover=True)
    stop = request.stop_event
    restore = _install_signal(stop)
    timer = None
    if request.timeout > 0:
        timer = threading.Timer(request.timeout, stop.set)
        timer.daemon = True
        timer.start()
    try:
        outcome = execute(request)
    except CliError as error:
        logger.error("{}", error)
        if getattr(args, "as_json", False):
            _emit_outcome({"ok": False, "code": error.code, "error": str(error)})
        return error.code
    except KeyboardInterrupt:
        logger.error("已中断")
        if getattr(args, "as_json", False):
            _emit_outcome({"ok": False, "code": EXIT_INTERRUPTED, "error": "运行已中断"})
        return EXIT_INTERRUPTED
    except Exception as error:  # noqa: BLE001
        logger.exception("执行失败: {}", error)
        if getattr(args, "as_json", False):
            _emit_outcome({"ok": False, "code": EXIT_RUNTIME, "error": str(error)})
        return EXIT_RUNTIME
    finally:
        if timer is not None:
            timer.cancel()
        restore()

    if getattr(args, "result", ""):
        Path(args.result).write_text(
            json.dumps(outcome, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    if args.as_json:
        _emit_outcome(outcome)
    else:
        _print_human(outcome)
    return int(outcome.get("code", EXIT_OK))
