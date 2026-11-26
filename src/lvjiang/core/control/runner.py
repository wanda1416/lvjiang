"""统一自动化执行门面：覆盖 .wf 脚本、插件 class 工作流与批量任务。

本模块只负责“一次任务”的解析与执行，不维护排队状态：
- ``list_catalog``：统一列出全部 .wf、插件注册的 Python class 工作流、以及 batch.json 配置组；
- ``run_job``：按 ``kind``（``"run"`` / ``"wf"`` / ``"class"`` / ``"batch"``）调度到既有引擎
  （``WorkflowEngine`` / ``get_workflow_class`` / ``BatchScheduler``），复用现有权限锁、
  历史记录、结果落盘与停止检查，不造第二套执行器。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from ..task_cli import (
    EXIT_ENVIRONMENT,
    EXIT_INTERRUPTED,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_RUNTIME,
    EXIT_USAGE,
    CliError,
    HeadlessCallbacks,
    HeadlessPause,
    RunRequest,
    _acquire,
    _bind_user,
    _build_params,
    _finish_history,
    _outcome,
    _resolve_user,
    _save_result,
    _start_history,
    _workflow_config,
    open_adb_target,
)
from ..task_cli import (
    execute as execute_wf_request,
)


def _discover_all_scripts() -> list[dict[str, Any]]:
    """发现全部可执行脚本（含 .wf 与插件 class 工作流）。"""
    from ...workflows.discovery import discover_scripts, script_display_name

    items: list[dict[str, Any]] = []
    for cfg in discover_scripts():
        script_id = str(cfg.get("id") or "")
        if not script_id:
            continue
        cls_name = str(cfg.get("class") or "").strip()
        kind = "class" if cls_name else "wf"
        items.append({
            "id": script_id,
            "name": script_display_name(cfg),
            "kind": kind,
            "class": cls_name,
            "wf_file": str(cfg.get("wf_file") or ""),
            "scope": str(cfg.get("scope") or "daily"),
            "env": list(cfg.get("env") or []),
            "description": str(cfg.get("description") or ""),
            "parameters": list(cfg.get("parameters") or []),
            "_raw": cfg,
        })
    return items


def _discover_batches() -> list[dict[str, Any]]:
    """发现全部批量配置组（config/session/batch.json）。"""
    from ..batch.config import load_batch_config

    cfg = load_batch_config()
    items: list[dict[str, Any]] = []
    for item in cfg.configs.values():
        items.append({
            "id": item.id,
            "name": item.name,
            "kind": "batch",
            "execution_unit_key": item.execution_unit_key,
            "task_ids": list(item.task_ids),
            "default_task_ids": list(item.default_task_ids),
            "default_usernames": list(item.default_usernames),
        })
    return items


def list_catalog(
    *,
    kind: str = "",
    script_id: str = "",
    name: str = "",
) -> dict[str, Any]:
    """返回统一的可执行清单（wf / class / batch），支持按 kind/id/name 过滤。"""
    kind_filter = kind.strip().lower()
    scripts: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []

    if kind_filter in {"", "all", "wf", "class"}:
        for item in _discover_all_scripts():
            if kind_filter in {"wf", "class"} and item["kind"] != kind_filter:
                continue
            if script_id and item["id"] != script_id:
                continue
            if name and item["name"] != name:
                continue
            clean = {k: v for k, v in item.items() if k != "_raw"}
            scripts.append(clean)

    if kind_filter in {"", "all", "batch"}:
        for batch in _discover_batches():
            if script_id and batch["id"] != script_id:
                continue
            if name and batch["name"] != name:
                continue
            batches.append(batch)

    return {
        "ok": True,
        "scripts": scripts,
        "batches": batches,
    }


def _find_script_entry(
    *, script_id: str = "", script_name: str = "",
) -> dict[str, Any]:
    scripts = _discover_all_scripts()
    if script_id:
        for item in scripts:
            if item["id"] == script_id:
                return item
        raise CliError(f"未找到脚本 id={script_id!r}", EXIT_NOT_FOUND)
    if script_name:
        matches = [item for item in scripts if item["name"] == script_name]
        if not matches:
            raise CliError(f"未找到脚本 name={script_name!r}", EXIT_NOT_FOUND)
        if len(matches) > 1:
            ids = ", ".join(m["id"] for m in matches)
            raise CliError(
                f"存在多个同名脚本 {script_name!r}（{ids}），请改用 --id 指定",
                EXIT_USAGE,
            )
        return matches[0]
    raise CliError("必须指定 script_id、script_name 或 script_file", EXIT_USAGE)


def _execute_class_workflow(
    config: dict[str, Any],
    *,
    username_req: str,
    layout_name: str,
    target_spec: str,
    raw_params: dict[str, Any],
    assume_yes: bool,
    dry_run: bool,
    stop_event: threading.Event,
) -> dict[str, Any]:
    """执行插件注册的 Python class 工作流（如自动调律/采集等）。"""
    from ...workflows.engine.builders import DeviceWorkflowEngineBuilder
    from ...workflows.implementations import get_workflow_class
    from ..config import load_user_config
    from ..layout.storage import load_layout_by_key

    started = datetime.now().astimezone()
    script_id = str(config.get("id") or "")
    name = str(config.get("name") or script_id)
    class_name = str(config.get("class") or "").strip()
    wf_class = get_workflow_class(class_name)
    if wf_class is None:
        raise CliError(
            f"工作流实现 {class_name!r} 未注册（请确认已加载对应插件）",
            EXIT_NOT_FOUND,
        )

    allowed_env = list(config.get("env") or [])
    if allowed_env and "android" not in allowed_env:
        raise CliError(f"脚本 {name} 只支持环境 {', '.join(allowed_env)}", EXIT_ENVIRONMENT)

    username = _resolve_user(username_req)
    eff_layout = layout_name.strip() or "android"
    layout = load_layout_by_key(eff_layout)
    if layout is None:
        raise CliError(f"布局不存在: {eff_layout}", EXIT_ENVIRONMENT)
    params = _build_params(config, username, raw_params)
    user_config = load_user_config()

    if dry_run:
        logger.info("[预检] class 工作流 {} ({}) 通过", name, class_name)
        return _outcome(
            ok=True, config=config, username=username,
            target="dry-run", layout=eff_layout, started=started,
        )

    from ..ocr.engine import OCREngine

    target = open_adb_target(target_spec, user_config)
    pause_event = threading.Event()
    pause_event.set()
    try:
        ocr = OCREngine()
        engine = DeviceWorkflowEngineBuilder(
            capture=target.capture,
            ocr=ocr,
            input_ctrl=target.input_ctrl,
            layout=layout,
            input_sim=user_config.input_sim,
            delay_params=user_config.delay_params,
            android_apps=user_config.android_apps,
            android_device=target.device,
            run_env="android",
            stop_check=stop_event.is_set,
            unattended=True,
        ).build()
        _bind_user(engine, username)
        loader = getattr(wf_class, "CONFIG_SNAPSHOT_LOADER", None)
        if callable(loader):
            engine.workflow_config_snapshot = loader(username)
        else:
            engine.workflow_config_snapshot = _workflow_config(script_id)
        engine._ui_callback = HeadlessCallbacks(assume_yes=assume_yes)

        wf_instance = wf_class(
            capture=target.capture,
            ocr=ocr,
            input_ctrl=target.input_ctrl,
            layout=layout,
            input_sim=user_config.input_sim,
            delay_params=user_config.delay_params,
            window_left=0,
            window_top=0,
            stop_check=stop_event.is_set,
            pause_event=pause_event,
        )

        lease = _acquire(username)
        task_run = _start_history(
            username=username, config=config, params=params,
            target=target, layout=eff_layout, started=started,
        )
        try:
            capture_ctx = task_run.capture_logs() if task_run is not None else None
            if capture_ctx is not None:
                with capture_ctx, lease.authorized():
                    result = engine.execute(wf_instance, initial_variables=params)
            else:
                with lease.authorized():
                    result = engine.execute(wf_instance, initial_variables=params)
        except BaseException:
            _finish_history(task_run, "failed", result_path=None)
            raise
        finally:
            lease.release()

        interrupted = stop_event.is_set()
        result_path = _save_result(
            username, script_id, started, result,
            getattr(task_run, "task_run_id", ""), interrupted,
        )
        _finish_history(
            task_run,
            "interrupted" if interrupted else "success",
            result_path=result_path,
        )
        if interrupted:
            return _outcome(
                ok=False, config=config, username=username,
                target=target.serial, layout=eff_layout, started=started,
                result=result, result_path=result_path,
                error="运行已中断", code=EXIT_INTERRUPTED,
            )
        return _outcome(
            ok=True, config=config, username=username,
            target=target.serial, layout=eff_layout, started=started,
            result=result, result_path=result_path,
        )
    except HeadlessPause as error:
        raise CliError(f"工作流要求人工介入: {error}", EXIT_RUNTIME) from error
    finally:
        target.close()


def _execute_batch(
    payload: dict[str, Any],
    *,
    stop_event: threading.Event,
    emit: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """按 batch.json 配置组执行 BatchScheduler 批量任务。"""
    from ... import constants
    from ...workflows.discovery import discover_scripts, script_display_name
    from ..batch.config import load_batch_config
    from ..batch.exclusions import collect_exclusions
    from ..batch.models import BatchContext, BatchRunSpec, BatchScript
    from ..batch.run import load_draft
    from ..batch.scheduler import BatchScheduler
    from ..config import load_user_config
    from ..config.users import SessionManager
    from ..layout.storage import load_layout_by_key
    from ..ocr.engine import OCREngine
    from ..users.document import list_user_names, load_user_metadata

    started = datetime.now().astimezone()
    group_id = str(payload.get("group_id") or payload.get("id") or "").strip()
    group_name = str(payload.get("name") or "").strip()

    cfg = load_batch_config()
    item = None
    if group_id:
        item = next((g for g in cfg.configs.values() if g.id == group_id), None)
    elif group_name:
        matches = [g for g in cfg.configs.values() if g.name == group_name]
        if len(matches) > 1:
            ids = ", ".join(m.id for m in matches)
            raise CliError(f"存在多个同名批量配置组 {group_name!r}（{ids}），请改用 --id", EXIT_USAGE)
        item = matches[0] if matches else None
    else:
        raise CliError("批量任务必须指定 --id 或 --name", EXIT_USAGE)

    if item is None:
        raise CliError(f"未找到批量配置组: {group_id or group_name}", EXIT_NOT_FOUND)

    catalog = {str(s["id"]): s for s in discover_scripts() if s.get("id")}
    available_users = set(list_user_names())
    all_users = [name for name in item.usernames if name in available_users] or sorted(available_users)
    if item.execution_unit_key == "user":
        entry_candidates = all_users
        entry_defaults = item.default_usernames
    else:
        members: dict[str, list[str]] = {}
        for name in all_users:
            metadata = load_user_metadata(name)
            val = str(metadata.attributes.get(item.execution_unit_key, "")).strip() if metadata else ""
            if val:
                members.setdefault(val, []).append(name)
        entry_candidates = list(members)
        entry_defaults = item.default_units.get(item.execution_unit_key, entry_candidates)

    draft = load_draft(item.id)
    draft.reconcile(
        task_candidates=[tid for tid in item.task_ids if tid in catalog],
        task_defaults=item.default_task_ids,
        entry_candidates=entry_candidates,
        entry_defaults=entry_defaults,
        unit_key=item.execution_unit_key,
    )

    # CLI 覆盖：users / tasks / rounds / unattended
    override_users = payload.get("users")
    if isinstance(override_users, list) and override_users:
        entries = [str(u).strip() for u in override_users if str(u).strip()]
    else:
        entries = draft.entry_selection(item.execution_unit_key).execution_order()

    override_tasks = payload.get("tasks")
    if isinstance(override_tasks, list) and override_tasks:
        selected_task_ids = [str(t).strip() for t in override_tasks if str(t).strip()]
    else:
        selected_task_ids = draft.tasks.execution_order()

    if not entries or not selected_task_ids:
        raise CliError(
            f"批量配置组「{item.name}」没有可执行的条目或任务",
            EXIT_USAGE,
        )

    missing_tasks = [tid for tid in selected_task_ids if tid not in catalog]
    if missing_tasks:
        raise CliError(f"批量配置组包含未知任务: {', '.join(missing_tasks)}", EXIT_NOT_FOUND)

    if "rounds" in payload and payload["rounds"] is not None:
        try:
            draft.rounds = max(1, int(payload["rounds"]))
        except (TypeError, ValueError) as exc:
            raise CliError(f"无效轮次 rounds={payload['rounds']!r}", EXIT_USAGE) from exc
    if "unattended" in payload and payload["unattended"] is not None:
        draft.unattended = bool(payload["unattended"])

    scripts = [
        BatchScript(
            id=tid,
            name=script_display_name(catalog[tid]),
            wf_file=str(catalog[tid].get("wf_file") or ""),
            class_name=str(catalog[tid].get("class") or ""),
            scope=str(catalog[tid].get("scope") or "daily"),
            parameters=catalog[tid].get("parameters"),
            env=catalog[tid].get("env"),
            batch_check=str(catalog[tid].get("batch_check") or ""),
        )
        for tid in selected_task_ids
    ]
    candidates = entries if item.execution_unit_key == "user" else all_users
    excluded = collect_exclusions(candidates, selected_task_ids, constants.USERS_DIR)
    spec = BatchRunSpec.build(
        item,
        draft,
        entries=entries,
        scripts=scripts,
        candidate_usernames=candidates,
        excluded=excluded,
    )

    dry_run = bool(payload.get("dry_run", False))
    if dry_run:
        return {
            "ok": True,
            "code": EXIT_OK,
            "kind": "batch",
            "group_id": item.id,
            "name": item.name,
            "entries": list(spec.entries),
            "tasks": [s.id for s in spec.scripts],
            "rounds": spec.rounds,
            "target": "dry-run",
            "started_at": started.isoformat(),
            "finished_at": datetime.now().astimezone().isoformat(),
        }

    user_config = load_user_config()
    eff_layout = str(payload.get("layout") or "").strip() or "android"
    layout = load_layout_by_key(eff_layout)
    if layout is None:
        raise CliError(f"布局不存在: {eff_layout}", EXIT_ENVIRONMENT)

    target_spec = str(payload.get("target") or "auto").strip() or "auto"
    target = open_adb_target(target_spec, user_config)
    pause_event = threading.Event()
    pause_event.set()
    assume_yes = bool(payload.get("assume_yes", False))
    summary_holder: dict[str, Any] = {}
    try:
        ctx = BatchContext(
            capture=target.capture,
            ocr=OCREngine(),
            input_ctrl=target.input_ctrl,
            layout=layout,
            input_sim=user_config.input_sim,
            delay_params=user_config.delay_params,
            android_apps=user_config.android_apps,
            android_device=target.device,
            run_env="android",
            input_kind=target.input_kind,
            layout_name=eff_layout,
            target_id=target.serial,
            target_kind="adb",
            target_label=target.serial,
            pause_event=pause_event,
            ui_callback=HeadlessCallbacks(assume_yes=assume_yes),
            history_source="cli",
        )
        worker = BatchScheduler(
            spec,
            ctx,
            SessionManager(constants.USERS_DIR),
            stop_event.is_set,
        )
        worker.log.connect(lambda msg: emit("log", {"level": "INFO", "message": str(msg)}))
        worker.progress.connect(
            lambda r, idx, entry_lbl, sid, st: emit(
                "progress",
                {
                    "round": r,
                    "run_idx": idx,
                    "entry": entry_lbl,
                    "script_id": sid,
                    "status": st,
                },
            )
        )
        worker.finished_all.connect(lambda summary: summary_holder.update(summary or {}))
        worker.run()
    finally:
        target.close()

    interrupted = stop_event.is_set()
    failed_count = int(summary_holder.get("failed", 0) or 0)
    ok = (not interrupted) and (failed_count == 0)
    code = EXIT_INTERRUPTED if interrupted else (EXIT_OK if ok else EXIT_RUNTIME)
    return {
        "ok": ok,
        "code": code,
        "kind": "batch",
        "group_id": item.id,
        "name": item.name,
        "target": target.serial,
        "layout": eff_layout,
        "started_at": started.isoformat(),
        "finished_at": datetime.now().astimezone().isoformat(),
        "summary": summary_holder,
        "error": "运行已中断" if interrupted else ("" if ok else f"批量存在 {failed_count} 项失败"),
    }


def run_job(
    payload: dict[str, Any],
    *,
    stop_event: threading.Event,
    emit: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """执行单次控制面作业（支持 wf / class / batch 全部自动化）。"""
    kind = str(payload.get("kind") or "run").strip().lower()
    if kind == "batch":
        return _execute_batch(payload, stop_event=stop_event, emit=emit)

    script_file = str(payload.get("script_file") or "").strip()
    script_id = str(payload.get("script_id") or payload.get("id") or "").strip()
    script_name = str(payload.get("script_name") or payload.get("name") or "").strip()
    raw_params = dict(payload.get("params") or {})
    user = str(payload.get("user") or "").strip()
    layout = str(payload.get("layout") or "").strip()
    target = str(payload.get("target") or "auto").strip() or "auto"
    assume_yes = bool(payload.get("assume_yes", False))
    dry_run = bool(payload.get("dry_run", False))
    timeout = float(payload.get("timeout") or 0.0)

    # 临时 .wf 文件直接走 task_cli.execute
    if script_file:
        req = RunRequest(
            script_file=str(Path(script_file)),
            user=user,
            layout=layout,
            target=target,
            params=raw_params,
            timeout=timeout,
            assume_yes=assume_yes,
            dry_run=dry_run,
            stop_event=stop_event,
        )
        return execute_wf_request(req)

    entry = _find_script_entry(script_id=script_id, script_name=script_name)
    if entry["kind"] == "class":
        return _execute_class_workflow(
            entry["_raw"],
            username_req=user,
            layout_name=layout,
            target_spec=target,
            raw_params=raw_params,
            assume_yes=assume_yes,
            dry_run=dry_run,
            stop_event=stop_event,
        )

    req = RunRequest(
        script_id=entry["id"],
        user=user,
        layout=layout,
        target=target,
        params=raw_params,
        timeout=timeout,
        assume_yes=assume_yes,
        dry_run=dry_run,
        stop_event=stop_event,
    )
    return execute_wf_request(req)
