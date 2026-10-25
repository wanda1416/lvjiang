"""手机批量页面、准备计划及后台执行适配器。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict
from uuid import uuid4

from loguru import logger

from ...errors import log_failure
from ..batch_config import declares_unit_prepare, load_batch_config
from ..batch_run import (
    BatchRunDraft,
    active_group_id,
    load_draft,
    save_draft,
    set_active_group_id,
)
from ..batch_scheduler import (
    ST_FAILED,
    ST_PENDING,
    ST_SKIPPED,
    ST_SUCCESS,
    BatchContext,
    BatchRunSpec,
    BatchScheduler,
    BatchScript,
)
from ..config.users import SessionManager
from ..daily_history import TaskHistoryRepository, resolve_history_path
from . import task_runner


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _groups():
    return load_batch_config().configs


def _state(group_id: str = ""):
    from ...workflows.discovery import list_exposed_scripts, script_display_name
    from ..user_config import load_user_metadata
    from .plugins import ensure_loaded
    ensure_loaded()
    groups = _groups()
    key = group_id or active_group_id()
    if key not in groups:
        key = next(iter(groups), "")
    item = groups.get(key)
    if item is None:
        raise ValueError("尚无批量配置组，可新建配置或从电脑同步")
    tasks = {t["id"]: t for t in list_exposed_scripts("android", device_entry=True)
             if t.get("batchable") and (not t.get("env") or "android" in t["env"])}
    available_users = set(task_runner._local_user_names())
    usernames = [name for name in item.usernames if name in available_users]
    members: dict[str, list[str]] = {}
    if item.execution_unit_key == "user":
        entries = usernames
        defaults = item.default_usernames
    else:
        for name in usernames:
            metadata = load_user_metadata(name)
            value = str(metadata.attributes.get(item.execution_unit_key, "")).strip() if metadata else ""
            if value:
                members.setdefault(value, []).append(name)
        entries = list(members)
        defaults = item.default_units.get(item.execution_unit_key, entries)
    draft = load_draft(key)
    candidates = [i for i in item.task_ids if i in tasks]
    draft.reconcile(task_candidates=candidates, task_defaults=item.default_task_ids,
                    entry_candidates=entries, entry_defaults=defaults, unit_key=item.execution_unit_key)
    if not item.workflows.recover_unattended:
        draft.unattended = False
    view = {"ok": True, "group_id": key, "groups": [{"id": g.id, "name": g.name} for g in groups.values()],
            "name": item.name, "unit_key": item.execution_unit_key, "entry_candidates": entries, "draft": draft.to_dict(),
            "tasks": [{"id": i, "name": script_display_name(tasks[i])} for i in draft.tasks.order],
            "entries": [{"id": i, "name": i, "members": members.get(i, [i])}
                        for i in draft.entry_selection(item.execution_unit_key).order],
            "workflows": item.workflows.to_dict(), "can_unattended": bool(item.workflows.recover_unattended),
            "readonly": task_runner.is_running(),
            "unavailable_tasks": [i for i in item.task_ids if i not in tasks],
            "unavailable_users": len(item.usernames) - len(usernames)}
    from pathlib import Path

    from ...workflows.metadata import metadata_for_script_config
    from ..config.resolver import get_resolver
    labels = {}
    for phase, name in item.workflows.to_dict().items():
        path = get_resolver().resolve_read(f"workflows/{name}") if name else None
        meta, _ = metadata_for_script_config(path) if path else ({}, "")
        labels[phase] = meta.get("name") or (Path(name).stem if name else "")
    view["workflow_names"] = labels
    if item.execution_unit_key == "user" and item.profile_sort_key:
        from ..profile.schema import get_profile_config
        try:
            definition = get_profile_config().get_key(item.profile_sort_key)
        except (OSError, ValueError) as exc:
            logger.warning(f"批量指定排序的资料定义不可用：{exc}")
            definition = None
        view["profile_sort"] = {"enabled": definition is not None, "label": definition.label if definition else "指定排序数据项不可用"}
    view["definition_token"] = _definition_token(item)
    view["draft"]["definition_token"] = view["definition_token"]
    return item, draft, tasks, usernames, view


def view(group_id: str = "") -> str:
    with task_runner.CONTROL_LOCK:
        try:
            return _json(_state(group_id)[-1])
        except Exception as exc:
            return _json({"ok": False, "message": str(exc), "groups": []})


def save(group_id: str, payload: str, reset: bool = False) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if task_runner.is_running():
                raise ValueError("结束执行后可修改批量选择")
            item, draft, tasks, users, current = _state(group_id)
            if reset:
                draft = BatchRunDraft()
            else:
                values = json.loads(payload)
                if values.get("definition_token") != current["definition_token"]:
                    raise ValueError("配置组已变更，请重新加载后编辑")
                draft = BatchRunDraft.from_dict(values)
                if draft.unattended and not item.workflows.recover_unattended:
                    raise ValueError("配置组未设置异常恢复工作流，不能开启无人值守")
            draft.reconcile(task_candidates=[i for i in item.task_ids if i in tasks],
                            task_defaults=item.default_task_ids, entry_candidates=current["entry_candidates"],
                            entry_defaults=(item.default_usernames if item.execution_unit_key == "user" else item.default_units.get(item.execution_unit_key, [e["id"] for e in current["entries"]])),
                            unit_key=item.execution_unit_key)
            save_draft(item.id, draft)
            set_active_group_id(item.id)
            return view(item.id)
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def _plan(group_id: str):
    from ... import constants
    from ...workflows.discovery import script_display_name
    from .task_settings import validate_for_run
    item, draft, catalog, users, current = _state(group_id)
    entries = draft.entry_selection(item.execution_unit_key).execution_order()
    selected = draft.tasks.execution_order()
    if not entries or not selected:
        raise ValueError("请至少选择一个任务和一个执行单元")
    if item.execution_unit_key != "user" and not declares_unit_prepare(item.workflows.prepare_item):
        raise ValueError("属性单元需要声明支持选人协议的条目准备工作流")
    from ..config.resolver import get_resolver
    for phase, name in item.workflows.to_dict().items():
        if name and get_resolver().resolve_read(f"workflows/{name}") is None:
            raise ValueError(f"生命周期工作流不存在：{phase}")
    scripts = [BatchScript(id=i, name=script_display_name(catalog[i]), wf_file=catalog[i].get("wf_file", ""),
                           class_name=catalog[i].get("class", ""), scope=catalog[i].get("scope", "daily"),
                           parameters=catalog[i].get("parameters"), env=catalog[i].get("env"),
                           batch_check=catalog[i].get("batch_check", "")) for i in selected]
    candidates = entries if item.execution_unit_key == "user" else users
    for username in candidates:
        for i in selected:
            validate_for_run(catalog[i], username, None)
    spec = BatchRunSpec.build(item, draft, entries=entries, scripts=scripts, candidate_usernames=candidates)
    ctx = BatchContext(capture=None, ocr=None, input_ctrl=None, layout=None, run_env="android",
                       target_id="ondevice", target_kind="android", target_label="手机独立执行",
                       history_source="ondevice")
    worker = BatchScheduler(spec, ctx, SessionManager(constants.USERS_DIR), task_runner._STATE.should_stop)
    from .offline import configuration_status
    environment = configuration_status()
    fingerprint = {"environment": {key: environment.get(key) for key in ("ready", "source", "layout", "synced_at")}, "definition": item.to_dict(), "draft": draft.to_dict(),
                   "spec": asdict(spec), "users": worker._user_attributes,
                   "plans": [asdict(p) for p in (*worker._task_plan.values(), *worker._member_task_plan.values())],
                   "params": worker._workflow_configs, "dedicated": list(worker._dedicated_configs.items()), "lifecycle": worker._lifecycle_params}
    token = hashlib.sha256(json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return worker, token, current


def prepare(group_id: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if task_runner.is_running():
                raise ValueError("已有执行正在运行，请先结束")
            worker, token, current = _plan(group_id)
            set_active_group_id(worker._spec.group_id)
            return _json({"ok": True, "token": token, "group_id": worker._spec.group_id,
                          "name": worker._spec.name, "entries": len(worker._spec.entries),
                          "tasks": len(worker._spec.scripts), "rounds": worker._spec.rounds,
                          "message": "已准备，回到游戏后从悬浮窗启动"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def start(group_id: str, token: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if task_runner.is_running():
                raise ValueError("已有执行正在运行，请先结束")
            worker, fresh, _ = _plan(group_id)
            if not token or fresh != token:
                raise ValueError("配置或参数已变化，请重新准备批量执行")
            from . import a11y
            if not a11y.is_ready():
                raise ValueError("请先开启无障碍服务")
            _recover_interrupted()
            task_runner._STATE.begin("batch:" + group_id, worker._spec.name)
            task_runner._STATE.set_batch({"group_id": group_id, "rows": [], "total": len(worker._spec.entries) * len(worker._spec.scripts) * worker._spec.rounds})
            thread = threading.Thread(target=_run, args=(worker,), name="lvjiang-batch", daemon=True)
            task_runner._STATE.bind_thread(thread)
            thread.start()
            return _json({"ok": True, "message": "批量执行已启动"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def _run(worker):
    sink = None
    state = task_runner._STATE
    try:
        engine = task_runner._get_engine()
        task_runner._reset_engine_state(engine)
        ctx = worker._ctx
        ctx.capture, ctx.ocr, ctx.input_ctrl, ctx.layout = engine._capture, engine._ocr, engine._input, engine._layout
        ctx.input_sim, ctx.delay_params = engine._input_sim, engine._delay_params
        ctx.android_apps, ctx.android_device = engine._android_apps, engine._android_device
        ctx.pause_event, ctx.ui_callback = task_runner._PAUSE_GATE, task_runner._task_ui
        ctx.layout_name = engine._layout.key
        ctx.task_run_id = uuid4().hex
        thread_id = threading.get_ident()
        def capture_log(msg):
            state.log(msg.record["message"], msg.record["level"].name)
            if msg.record["message"].startswith("[AndroidApp]"):
                state.set_message(msg.record["message"])
        sink = logger.add(capture_log,
                          filter=lambda record: record["thread"].id == thread_id)
        rows = {}
        for number in range(1, worker._spec.rounds + 1):
            for index, entry in enumerate(worker._spec.entries):
                label = entry if worker._spec.rounds == 1 else f"第 {number} 轮 · {entry}"
                for script in worker._scripts:
                    rows[(index, number, script.id)] = {"entry": label, "task_id": script.id, "task_name": script.name, "status": ST_PENDING}
        state.set_batch({"rows": list(rows.values()), "processed": 0})
        def progress(index, label, task_id, status):
            match = re.search(r"第 (\d+) (?:轮|次)", label)
            if status == ST_PENDING and worker._spec.rounds > 1 and match is None:
                return
            number = int(match.group(1)) if match else 1
            key = (index, number, task_id)
            rows[key] = {"entry": label, "task_id": task_id,
                         "task_name": next((s.name for s in worker._scripts if s.id == task_id), task_id), "status": status}
            state.set_batch({"rows": list(rows.values()), "current_entry": label,
                             "current_task": rows[key]["task_name"], "round": number, "rounds": worker._spec.rounds, "processed": sum(r["status"] in {ST_SUCCESS, ST_FAILED, ST_SKIPPED} for r in rows.values())})
            state.set_message(f"{label} · {rows[key]['task_name']} · {status}")
        worker.progress.connect(progress)
        def log(message):
            state.log(message)
            state.set_message(message)
        worker.log.connect(log)
        worker.finished_all.connect(lambda result: state.finish(
            task_runner.STATE_STOPPED if state.should_stop() or result.get("stopped") else task_runner.STATE_FAILED if result.get("error") or any(
                r["status"] == "失败" for r in rows.values()) or any(e.get("prepare") == "失败" or e.get("finish") == "失败" for e in result.get("entries", {}).values()) or any(v == "failed" for v in result.get("lifecycle", {}).values()) else task_runner.STATE_DONE,
            result.get("error") or "批量执行已结束", result))
        worker.run()
    except Exception as exc:
        log_failure("手机批量执行失败", exc)
        state.finish(task_runner.STATE_STOPPED if state.should_stop() else task_runner.STATE_FAILED, str(exc))
    finally:
        if sink is not None:
            logger.remove(sink)
        task_runner._PAUSE_GATE.set()


def _recover_interrupted():
    from datetime import datetime
    repo = TaskHistoryRepository()
    for record in repo.list_batch_runs(target_kind="android", status="running"):
        if record.target_id != "ondevice":
            continue
        repo.finish_batch_run(record.batch_run_id, status="failed", finished_at=datetime.now().isoformat(),
                              duration_ms=record.duration_ms, error_message="上次批量未正常结束，结果待核对")
    for record in repo.list_task_runs(source="ondevice", status="running"):
        repo.finish_task_run(record.task_run_id, status="failed", finished_at=datetime.now().isoformat(),
                             duration_ms=record.duration_ms, error_message="上次批量任务中断，结果待核对")


def history(run_id: str = "") -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if not task_runner.is_running():
                _recover_interrupted()
            repo = TaskHistoryRepository()
            records = [r for r in repo.list_batch_runs(target_kind="android", limit=100) if r.target_id == "ondevice"]
            if not run_id:
                return _json({"ok": True, "records": [asdict(r) for r in records]})
            record = next((r for r in records if r.batch_run_id == run_id), None)
            if record is None:
                raise ValueError("批次记录不可用，请刷新")
            path = resolve_history_path(record.report_path)
            return _json({"ok": True, "record": asdict(record),
                          "tasks": [asdict(r) for r in repo.list_task_runs(batch_run_id=run_id)],
                          "report": path.read_text(encoding="utf-8")[:32000] if path and path.is_file() else ""})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def editor(group_id: str = "") -> str:
    """配置定义编辑器；读取与切换编辑对象均不修改活动选择。"""
    with task_runner.CONTROL_LOCK:
        try:
            from ...workflows.discovery import discover_scripts, script_display_name
            from ...workflows.file_tree import list_workflow_files
            from ...workflows.metadata import metadata_for_script_config
            from ..batch_config import BatchConfigItem
            from ..config.resolver import get_resolver
            from ..profile.schema import get_profile_config
            from ..user_config import load_user_metadata
            from .plugins import ensure_loaded
            ensure_loaded()
            groups = _groups()
            item = groups.get(group_id) if group_id else None
            if group_id and item is None:
                raise ValueError("配置组已删除，请重新加载")
            definition = item.to_dict() if item else BatchConfigItem(name="").to_dict()
            tasks = {i["id"]: i for i in discover_scripts() if i.get("batchable")}
            names = task_runner._local_user_names()
            fields = {"user": "用户名"}
            attributes = {}
            for name in names:
                user = load_user_metadata(name)
                attributes[name] = user.attributes if user else {}
                for key, value in attributes[name].items():
                    if value:
                        fields.setdefault(key, key)
            if item:
                fields.setdefault(item.execution_unit_key, item.execution_unit_key)
            workflows = []
            for file in list_workflow_files():
                path = get_resolver().resolve_read(f"workflows/{file.rel_path}")
                if path is None:
                    continue
                meta, _ = metadata_for_script_config(path)
                workflows.append({"id": file.rel_path, "name": meta.get("name") or path.stem})
            try:
                profile_keys = [{"id": key.key, "name": key.label} for key in get_profile_config().get_all_keys()]
            except (OSError, ValueError) as exc:
                logger.warning(f"批量指定排序的资料定义不可用：{exc}")
                profile_keys = []
            return _json({"ok": True, "groups": [{"id": g.id, "name": g.name} for g in groups.values()],
                          "definition": definition, "token": _definition_token(item) if item else "",
                          "tasks": [{"id": i, "name": script_display_name(task)} for i, task in tasks.items()],
                          "users": [{"id": name, "name": name, "attributes": attributes[name]} for name in names],
                          "profile_keys": profile_keys,
                          "unit_keys": [{"id": key, "name": label} for key, label in fields.items()],
                          "workflows": workflows, "readonly": task_runner.is_running()})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def _definition_token(item) -> str:
    return hashlib.sha256(_json(item.to_dict()).encode("utf-8")).hexdigest()


def lifecycle_preview(workflows: str, values: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            from ..batch_config import BatchWorkflows, lifecycle_parameter_definitions
            from ..task_params import merge_task_params, parameters_for_values
            from .task_settings import _validate
            wf = BatchWorkflows.from_dict(json.loads(workflows))
            source = json.loads(values)
            result = {}
            for phase, definitions in lifecycle_parameter_definitions(wf).items():
                params, _ = merge_task_params(definitions, source.get(phase, {}), None)
                for definition in definitions:
                    key = definition["name"]
                    if key in params:
                        try:
                            params[key] = _validate(definition, params[key])
                        except (ValueError, TypeError):
                            pass  # 草稿尚未填写完；保存时严格校验。
                result[phase] = {"definitions": parameters_for_values(definitions, params), "values": params}
            return _json({"ok": True, "phases": result})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def save_definition(group_id: str, token: str, payload: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            from ..batch_config import (
                BatchConfigItem,
                lifecycle_parameter_definitions,
                mutate_batch_config,
            )
            from ..task_params import merge_task_params, parameters_for_values
            from .task_settings import _validate
            if task_runner.is_running():
                raise ValueError("结束执行后可修改配置组")
            current = _groups().get(group_id) if group_id else None
            if group_id and (current is None or _definition_token(current) != token):
                raise ValueError("配置组已变化，请重新加载后编辑")
            data = json.loads(payload)
            name = str(data.get("name", "")).strip()
            if not name:
                raise ValueError("请输入配置组名称")
            item = BatchConfigItem.from_dict(name, data)
            if group_id:
                item.id = group_id
            else:
                item.id = uuid4().hex
            catalog = json.loads(editor(group_id))
            tasks = {x["id"] for x in catalog.get("tasks", [])} | set(current.task_ids if current else [])
            users = {x["id"] for x in catalog.get("users", [])} | set(current.usernames if current else [])
            if set(item.task_ids) - tasks or set(item.usernames) - users:
                raise ValueError("任务或用户已变化，请重新加载")
            if item.execution_unit_key not in {x["id"] for x in catalog.get("unit_keys", [])}:
                raise ValueError("调度单元不可用，请重新选择")
            paths = {x["id"] for x in catalog.get("workflows", [])}
            if any(path and path not in paths for path in item.workflows.to_dict().values()):
                raise ValueError("生命周期工作流不可用，请重新选择")
            for phase, definitions in lifecycle_parameter_definitions(item.workflows).items():
                params, _ = merge_task_params(definitions, item.workflow_params.get(phase, {}), None)
                visible = {definition["name"] for definition in parameters_for_values(definitions, params)}
                for definition in definitions:
                    key = definition["name"]
                    if key in params:
                        try:
                            params[key] = _validate(definition, params[key])
                        except (ValueError, TypeError):
                            if key in visible:
                                label = next((wf["name"] for wf in catalog["workflows"] if wf["id"] == item.workflows.to_dict()[phase]), "生命周期")
                                raise ValueError(f"{label} · {definition.get('label', key)}：参数不符合当前定义") from None
                item.workflow_params[phase] = params
            def merge(config):
                live = config.get(group_id) if group_id else None
                if group_id and (live is None or _definition_token(live) != token):
                    raise ValueError("配置组已变化，请重新加载后编辑")
                if any(g.name == name and g.id != item.id for g in config.configs.values()):
                    raise ValueError("配置组名称已存在")
                config.configs[item.id] = item
            mutate_batch_config(merge)
            # 保存定义不重建本次草稿，不改变活动组。
            return _json({"ok": True, "group_id": item.id, "message": "配置组已保存"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def delete_definition(group_id: str, token: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            from ..batch_config import mutate_batch_config
            from ..batch_run import forget_draft
            if task_runner.is_running():
                raise ValueError("结束执行后可删除配置组")
            def remove(config):
                item = config.get(group_id)
                if item is None or _definition_token(item) != token:
                    raise ValueError("配置组已变化，请重新加载")
                del config.configs[group_id]
            updated = mutate_batch_config(remove)
            forget_draft(group_id, updated.first_id())
            return _json({"ok": True, "message": "配置组已删除"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def sort_users(group_id: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            from ..batch_run import profile_user_order
            if task_runner.is_running():
                raise ValueError("结束执行后可调整顺序")
            item, draft, _, _, _ = _state(group_id)
            if item.execution_unit_key != "user" or not item.profile_sort_key:
                raise ValueError("当前配置组没有设置用户指定排序")
            draft.users.order, missing = profile_user_order(draft.users.order, item.profile_sort_key, item.profile_sort_direction)
            save_draft(item.id, draft)
            set_active_group_id(item.id)
            return _json({"ok": True, "message": "已调整本次用户顺序" + (f"，{missing} 个缺失值放在末尾" if missing else "")})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})
