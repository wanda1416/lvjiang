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


def public_content(kind: str) -> str:
    """仅由用户手动刷新调用，复用桌面的校验和版本筛选。"""
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

            from ... import constants
            from ..group_qrcode import fetch_qrcode_bytes, save_qrcode
            path = constants.SESSION_CONFIG_DIR / "feedback-qrcode.jpg"
            message = ""
            try:
                payload = fetch_qrcode_bytes()
                save_qrcode(payload, path=path)
            except Exception as exc:
                if not path.is_file():
                    raise
                payload = path.read_bytes()
                message = f"刷新失败，显示上次二维码：{exc}"
            return _json({"ok": True, "message": message, "image": base64.b64encode(payload).decode("ascii")})
        if kind == "version":
            # 版本入口的站点与桌面共同维护。
            from ..public_links import RELEASES_URL
            from ..version_info import get_version
            return _json({"ok": True, "version": get_version(), "url": RELEASES_URL})
        raise ValueError("内容类型不可用")
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})
