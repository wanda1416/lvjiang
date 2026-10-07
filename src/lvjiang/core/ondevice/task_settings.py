"""手机任务配置桥：编辑用户显式指定，写入与启动/同步互斥。"""
from __future__ import annotations

import hashlib
import importlib
import json
from typing import Any

from ..task_params import parameters_for_env, parameters_for_values, resolve_task_params
from ..user_config import (
    delete_user_workflow_params,
    get_user_workflow_params,
    set_user_workflow_params,
)
from . import task_runner
from .offline import sync_status


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _tasks() -> list[dict]:
    from ...workflows.discovery import list_exposed_scripts
    from .plugins import ensure_loaded
    ensure_loaded()
    if not sync_status().get("synced"):
        raise ValueError("请先从 PC 同步任务配置")
    return list_exposed_scripts("android", device_entry=True)


def _adapter(task: dict):
    if not task.get("class"):
        return None
    from ...workflows import implementations
    cls = implementations.get_workflow_class(task["class"])
    module = getattr(cls, "DEVICE_SETTINGS_MODULE", "")
    return importlib.import_module(module) if module else None


def _task(task_id: str) -> dict:
    for task in _tasks():
        if task["id"] == task_id:
            return task
    raise ValueError("任务已不可用，请重新加载设置列表")


def _user(username: str) -> None:
    if username not in task_runner._synced_user_names():
        raise ValueError("用户资料不可用，请重新从 PC 同步")


def _view(username: str, task_id: str) -> dict:
    _user(username)
    task = _task(task_id)
    adapter = _adapter(task)
    if adapter:
        view = adapter.get_settings(username)
        values = view["values"]
        source = "user"
    else:
        definitions = task.get("parameters", [])
        values, source = resolve_task_params(task_id, username, definitions)
        from ...workflows.builtins._coerce import to_bool
        for definition in parameters_for_env(definitions, "android"):
            name = definition["name"]
            kind = definition.get("type", "select")
            if kind == "bool":
                values[name] = to_bool(values.get(name))
            elif kind == "checkgroup":
                saved = values.get(name)
                saved = saved if isinstance(saved, dict) else {}
                values[name] = {option if isinstance(option, str) else option["value"]: bool(
                    saved.get(option if isinstance(option, str) else option["value"], True)
                ) for option in definition.get("options", [])}
        view = {"kind": "parameters", "definitions": parameters_for_env(definitions, "android"),
                "values": values}
    # 同步、元数据、共享配置和该任务的用户覆盖都属于编辑基线。
    token = hashlib.sha256(_json({"sync": sync_status(), "view": view,
                                  "override": get_user_workflow_params(username, task_id)}).encode()).hexdigest()
    return {**view, "ok": True, "username": username, "task_id": task_id,
            "name": task.get("name", task_id), "note": task.get("note", ""), "source": source, "token": token,
            "readonly": task_runner.is_running()}


def list_settings(username: str) -> str:
    from ...workflows.discovery import script_display_name
    with task_runner.CONTROL_LOCK:
        try:
            _user(username)
            items = []
            for task in _tasks():
                try:
                    adapter = _adapter(task)
                    if adapter or parameters_for_env(task.get("parameters", []), "android"):
                        items.append({"id": task["id"], "name": script_display_name(task),
                                      "dedicated": bool(adapter),
                                      "source": "user" if adapter or get_user_workflow_params(
                                          username, task["id"]) is not None else "global"})
                except Exception as exc:
                    items.append({"id": task["id"], "name": script_display_name(task),
                                  "error": f"配置不可用：{exc}"})
            return _json({"ok": True, "tasks": items, "readonly": task_runner.is_running()})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def get_settings(username: str, task_id: str) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            return _json(_view(username, task_id))
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def preview_parameters(username: str, task_id: str, payload: str) -> str:
    """只计算草稿依赖，不落盘、不切换活动用户。"""
    with task_runner.CONTROL_LOCK:
        try:
            _user(username)
            definitions = parameters_for_env(_task(task_id).get("parameters", []), "android")
            values = json.loads(payload)
            if not isinstance(values, dict):
                raise ValueError("参数必须是对象")
            effective, _ = resolve_task_params(task_id, username, _task(task_id).get("parameters", []))
            for definition in definitions:
                name = definition["name"]
                if name in values:
                    try:
                        effective[name] = _validate(definition, values[name])
                    except (ValueError, TypeError):
                        pass
            visible = parameters_for_values(definitions, effective)
            return _json({"ok": True, "visible": [item["name"] for item in visible]})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})


def _validate(definition: dict, value: Any) -> Any:
    kind = definition.get("type", "select")
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("请输入整数")
        try:
            value = int(value)
        except ValueError:
            raise ValueError("请输入整数") from None
        minimum, maximum = definition.get("min", 0), definition.get("max", 999999)
        if not minimum <= value <= maximum:
            raise ValueError(f"请输入 {minimum}～{maximum} 之间的整数")
    elif kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("请选择开关状态")
    elif kind == "text":
        if not isinstance(value, str):
            raise ValueError("请输入文本")
    else:
        options = {item if isinstance(item, str) else item["value"] for item in definition.get("options", [])}
        if kind == "select" and value not in options:
            raise ValueError("请选择有效选项")
        if kind == "checkgroup":
            if not isinstance(value, dict) or set(value) - options or any(
                not isinstance(item, bool) for item in value.values()
            ):
                raise ValueError("请选择有效项目")
    return value


def save_settings(username: str, task_id: str, token: str, payload: str, reset: bool = False) -> str:
    with task_runner.CONTROL_LOCK:
        try:
            if task_runner.is_running():
                raise ValueError("停止任务后可修改参数")
            view = _view(username, task_id)
            if not token or view["token"] != token:
                return _json({"ok": False, "conflict": True,
                              "message": "配置已变更或重新同步，请重新加载后编辑"})
            adapter = _adapter(_task(task_id))
            if reset:
                if adapter:
                    raise ValueError("该任务使用用户专属配置，不能恢复通用配置")
                delete_user_workflow_params(username, task_id)
            else:
                values = json.loads(payload)
                if not isinstance(values, dict):
                    raise ValueError("参数必须是对象")
                if adapter:
                    adapter.save_settings(username, values)
                else:
                    definitions = view["definitions"]
                    # 先规范化有效控制参数，再计算 require，避免数值输入字符串影响依赖。
                    normalized = dict(view["values"])
                    errors = {}
                    for definition in definitions:
                        name = definition["name"]
                        if name in values:
                            try:
                                normalized[name] = _validate(definition, values[name])
                            except (ValueError, TypeError):
                                pass
                    visible = parameters_for_values(definitions, normalized)
                    patch = {}
                    for definition in visible:
                        name = definition["name"]
                        try:
                            patch[name] = _validate(definition, values.get(name, view["values"].get(name)))
                        except (ValueError, TypeError) as exc:
                            errors[name] = str(exc)
                    if errors:
                        return _json({"ok": False, "message": "请检查标记的参数", "errors": errors})
                    old = get_user_workflow_params(username, task_id) or {}
                    # 固定当前生效值为用户独立配置，保留不可见及桌面字段。
                    set_user_workflow_params(username, task_id, {**view["values"], **old, **patch})
            return _json({"ok": True, "message": "已恢复通用配置" if reset else "已保存"})
        except Exception as exc:
            return _json({"ok": False, "message": str(exc)})
