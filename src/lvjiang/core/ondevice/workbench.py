"""手机工作台只读视图；业务值和参数规则由公共服务提供。"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

from ..daily_history import TaskHistoryRepository, resolve_history_path
from . import task_runner

SOURCE = "ondevice"


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def task_catalog(username: str) -> str:
    from ...workflows.discovery import list_exposed_scripts, script_display_name
    from ..task_params import parameters_for_env, resolve_task_params
    from .plugins import ensure_loaded
    from .task_settings import _adapter, validate_for_run

    try:
        ensure_loaded()
        tasks = []
        for task in list_exposed_scripts("android", device_entry=True):
            item = {"id": task["id"], "name": script_display_name(task),
                    "group": task.get("group") or "其他任务", "note": task.get("note", ""),
                    "configurable": False,
                    "source": "global", "reason": ""}
            try:
                adapter = _adapter(task)
                item["configurable"] = bool(adapter or parameters_for_env(task.get("parameters", []), "android"))
                validate_for_run(task, username)
                if item["configurable"]:
                    item["source"] = "user" if adapter else resolve_task_params(task["id"], username, task.get("parameters", []))[1]
            except Exception as exc:
                item["reason"] = str(exc)
            tasks.append(item)
        return _json({"ok": True, "tasks": tasks})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc), "tasks": []})


def profile_view(username: str) -> str:
    from ..profile.models import MODEL_LABELS, MODEL_NOTE
    from ..profile.schema import get_profile_config
    from ..profile.service import profile_read_all

    try:
        if username not in task_runner._local_user_names():
            raise ValueError("用户资料不可用")
        config = get_profile_config()
        data = profile_read_all(username)
        items = []
        for kd in config.get_all_keys():
            model = config.get_model_type(kd.key)
            entry = data.get(model, {}).get(kd.key)
            items.append({"key": kd.key, "model": model, "model_label": MODEL_LABELS.get(model or "", "数据"), "label": kd.label or kd.key,
                          "group": "默认" if kd.group == "default" else kd.group,
                          "value": (entry.get("value_text", "") if model == MODEL_NOTE else entry.get("value")) if entry else None,
                          "collected": bool(entry), "cap": kd.cap if kd.show_cap else None,
                          "updated_at": entry.get("updated_at", "") if entry else ""})
        return _json({"ok": True, "items": items, "username": username})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def profile_history(username: str, model: str, key: str, offset: int = 0) -> str:
    from ..profile.repository import db_count_history, db_get_history
    from ..profile.schema import get_profile_config

    try:
        if username not in task_runner._local_user_names() or get_profile_config().get_key(key, model) is None:
            raise ValueError("用户或数据项不可用")
        return _json({"ok": True, "records": db_get_history(username, model, key, limit=50, offset=max(0, offset)),
                      "total": db_count_history(username, model, key)})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def profile_all_users(model: str = "", key: str = "") -> str:
    """全部用户实时数据总览：按「模型 · 分组」组织，每个数据项附所有用户值。

    传入 ``model``/``key`` 时只返回该数据项（用户数据页单个 key 的「全部」入口）。
    可累加模型（quota/regen/stock）附带合计；note 是自由文本，不参与合计。
    与 profile_view 同源（profile_read_all 实时计算 regen），保证数值一致。
    """
    from ..profile.models import (
        MODEL_LABELS,
        MODEL_NOTE,
        MODEL_QUOTA,
        MODEL_REGEN,
        MODEL_STOCK,
    )
    from ..profile.schema import get_profile_config
    from ..profile.service import profile_read_all

    try:
        users = task_runner._local_user_names()
        if not users:
            return _json({"ok": True, "users": [], "groups": []})
        config = get_profile_config()
        per_user = {name: profile_read_all(name) for name in users}

        def display_value(model: str, entry: dict):
            """单个值：note 用文本，其余数值取整；返回字符串或 None。"""
            if model == MODEL_NOTE:
                text = entry.get("value_text", "")
                return text if text else None
            value = entry.get("value")
            if value is None:
                return None
            try:
                num = float(value)
                return str(int(num)) if num == int(num) else str(num)
            except (TypeError, ValueError):
                return str(value)

        summable_models = (MODEL_QUOTA, MODEL_REGEN, MODEL_STOCK)
        want_model, want_key = model, key
        groups: list[dict] = []
        group_index: dict[str, int] = {}
        for kd in config.get_all_keys():
            model = config.get_model_type(kd.key) or ""
            if want_key and (kd.key != want_key or (want_model and model != want_model)):
                continue
            model_label = MODEL_LABELS.get(model, "数据")
            group = "默认" if kd.group == "default" else kd.group
            title = f"{model_label} · {group}"
            if title not in group_index:
                group_index[title] = len(groups)
                groups.append({"title": title, "items": []})

            summable = model in summable_models
            values: list[dict] = []
            total = 0.0
            has_total = False
            for name in users:
                entry = per_user[name].get(model, {}).get(kd.key)
                value = display_value(model, entry) if entry else None
                values.append({
                    "user": name,
                    "value": value,
                    "collected": bool(entry),
                })
                if summable and entry and entry.get("value") is not None:
                    try:
                        total += float(entry["value"])
                        has_total = True
                    except (TypeError, ValueError):
                        pass

            item = {
                "key": kd.key,
                "label": kd.label or kd.key,
                "model": model,
                "summable": summable,
                "values": values,
            }
            if kd.show_cap and kd.cap is not None:
                item["cap"] = kd.cap
            if summable and has_total:
                item["total"] = str(int(total)) if total == int(total) else str(total)
            groups[group_index[title]]["items"].append(item)

        return _json({"ok": True, "users": users, "groups": groups})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def recover_interrupted(repository: TaskHistoryRepository) -> None:
    """本进程尚未开始任务时，恢复上次独立执行遗留的 running 记录。"""
    with task_runner.CONTROL_LOCK:
        if task_runner.is_running():
            return
        for record in repository.list_task_runs(source=SOURCE, status="running"):
            repository.finish_task_run(record.task_run_id, status="failed", finished_at=datetime.now().isoformat(),
                                       duration_ms=record.duration_ms, error_message="上次任务未正常结束，应用进程可能已退出；执行结果待核对")


def task_history(run_id: str = "") -> str:
    try:
        repository = TaskHistoryRepository()
        recover_interrupted(repository)
        records = repository.list_task_runs(source=SOURCE, limit=100)
        if not run_id:
            return _json({"ok": True, "records": [{k: v for k, v in asdict(r).items()
                                                   if k not in {"params", "log_path", "result_path"}} for r in records]})
        record = repository.get_task_run(run_id)
        if record is None or record.source != SOURCE:
            raise ValueError("任务记录不可用，请刷新列表")
        log = ""
        path = resolve_history_path(record.log_path)
        if path and path.is_file():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 32_000))
                log = stream.read(32_000).decode("utf-8", errors="replace")
        result = ""
        path = resolve_history_path(record.result_path)
        if path and path.is_file():
            result = path.read_text(encoding="utf-8")[:32_000]
        return _json({"ok": True, "record": asdict(record), "log": log, "result": result})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def public_content(kind: str, refresh: bool = False) -> str:
    """公开内容按需展示；二维码默认读缓存，用户可显式刷新。"""
    try:
        if kind == "about":
            from ..about import about_content
            return _json({"ok": True, **about_content()})
        if kind == "notices":
            from ..announcement import (
                applicable_notices,
                cache_manifest,
                fetch_announcement_manifest,
                get_cached_etag,
                load_cached_manifest,
            )
            from ..version_info import get_version
            cached = load_cached_manifest()
            message = ""
            try:
                fetched = fetch_announcement_manifest(etag=get_cached_etag(), cached=cached)
                cached = fetched.manifest
                cache_manifest(cached, fetched.etag)
            except Exception as exc:
                if cached is None:
                    raise
                message = f"刷新失败，显示缓存：{exc}"
            return _json({"ok": True, "message": message,
                          "notices": [asdict(n) for n in applicable_notices(cached, get_version())]})
        if kind == "qrcode":
            import base64

            from ..group_qrcode import (
                fetch_qrcode_bytes,
                load_qrcode_bytes,
                qrcode_path,
                save_qrcode,
            )
            message = ""
            try:
                if refresh:
                    payload = fetch_qrcode_bytes()
                    save_qrcode(payload)
                else:
                    payload = load_qrcode_bytes()
            except Exception as error:
                if not qrcode_path().is_file():
                    raise
                payload = qrcode_path().read_bytes()
                message = f"刷新失败，显示缓存：{error}"
            return _json({"ok": True, "message": message, "image": base64.b64encode(payload).decode("ascii")})
        if kind == "version":
            # 版本入口的站点与桌面共同维护。
            from ..public_links import RELEASES_URL
            from ..version_info import get_version
            return _json({"ok": True, "version": get_version(), "url": RELEASES_URL})
        raise ValueError("内容类型不可用")
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})
