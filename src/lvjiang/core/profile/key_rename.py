"""Profile 身份维护：引用更新、SQL 审计及跨文件失败恢复。"""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from ..fs_util import atomic_write_bytes, atomic_write_text
from .maintenance import profile_lock
from .models import parse_sync_key

if TYPE_CHECKING:
    from .repository import ProfileDB
    from .schema import ProfileSchema


def replace_key(reference: str, renames: list[tuple[str, str, str]]) -> str:
    model, key = parse_sync_key(reference)
    for kind, old, new in renames:
        if key == old and (not model or model == kind):
            return f"{model}:{new}" if model else new
    return reference


def rename_schema_references(schema: ProfileSchema, renames: list[tuple[str, str, str]]) -> ProfileSchema:
    result = deepcopy(schema)
    for definition in result.get_all_keys():
        for target in definition.sync_targets:
            target.key = replace_key(target.key, renames)
    return result


def _journal_path(db: ProfileDB) -> Path:
    return db._db_path.with_suffix(".rename-journal.json")


def _restore_files(journal: dict) -> None:
    for filename, content in journal["files"].items():
        path = Path(filename)
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, base64.b64decode(content), prefix=".profile_restore_")


def recover_rename(db: ProfileDB) -> None:
    """SQL 未提交则恢复文件；已提交则保留新文件。异常退出后可再次执行。"""
    path = _journal_path(db)
    if not path.exists():
        return
    journal = json.loads(path.read_text(encoding="utf-8"))
    conn = db._connect()
    try:
        committed = conn.execute(
            "SELECT 1 FROM profile_key_renames WHERE operation_id=? LIMIT 1",
            (journal["operation_id"],),
        ).fetchone()
    finally:
        conn.close()
    if not committed:
        _restore_files(journal)
    path.unlink()
    from ..config import get_session_store
    from .schema import reload_profile_config
    get_session_store().reload()
    reload_profile_config()


def _rename_session(data: dict, renames: list[tuple[str, str, str]]) -> None:
    profile = data.get("profile", {})
    for group in profile.get("overview_groups", {}).values():
        group["columns"] = [replace_key(key, renames) for key in group.get("columns", [])]
    alerts = profile.get("alert_history", {})
    # 告警标识为 username:key:level:current，与引擎生成格式一致。
    for name in list(alerts):
        parts = name.split(":")
        if len(parts) == 4:
            replaced = replace_key(parts[1], renames)
            if replaced != parts[1]:
                parts[1] = replaced
                alerts[":".join(parts)] = alerts.pop(name)
    batch = data.get("ui_state", {}).get("batch", {})
    for draft in batch.get("drafts", {}).values():
        if "profile_sort_key" in draft:
            draft["profile_sort_key"] = replace_key(draft["profile_sort_key"], renames)


def _rename_batch_parameters(data: dict, renames: list[tuple[str, str, str]]) -> None:
    for group in data.get("groups", {}).values():
        if "profile_sort_key" in group:
            group["profile_sort_key"] = replace_key(group["profile_sort_key"], renames)
        for params in group.get("workflow_params", {}).values():
            if isinstance(params, dict) and isinstance(params.get("profile_key"), str):
                params["profile_key"] = replace_key(params["profile_key"], renames)


def save_renamed_definitions(
    schema: ProfileSchema, renames: list[tuple[str, str, str]], *,
    db: ProfileDB | None = None,
) -> None:
    """仅用于定义编辑器确认保存；调用前必须确认设备任务已停止。"""
    from ... import constants
    from ..access import is_readonly
    from ..config import get_session_store
    from . import schema as schema_module
    from .repository import get_profile_db
    from .triggers import _runner

    if is_readonly():
        raise ValueError("只读实例不能重命名 Profile key")
    with profile_lock:
        if _runner is not None and _runner.is_busy:
            raise ValueError("Profile 变更脚本仍在执行，请等待队列完成后重命名")
        db = db or get_profile_db()
        recover_rename(db)
        current = schema_module.get_profile_config()
        for model, old, new in renames:
            if current.get_key(old, model_type=model) is None:
                raise ValueError(f"原 key {old} 已不存在，请重新打开定义编辑器")
            declared = any(
                isinstance(rows, list) and any(
                    isinstance(row, dict) and row.get("key") == new for row in rows
                ) for rows in current.to_dict().values()
            )
            if current.get_key(new) is not None or declared:
                raise ValueError(f"目标 key {new} 已存在，不能合并")
        new_schema = rename_schema_references(schema, renames)
        paths = [schema_module._PROFILE_PATH, constants.SESSION_PATH, constants.BATCH_CONFIG_PATH]
        operation_id = uuid4().hex
        journal = {
            "operation_id": operation_id,
            "files": {str(path.resolve()): base64.b64encode(path.read_bytes()).decode()
                      if path.exists() else None for path in paths},
        }
        journal_path = _journal_path(db)
        atomic_write_text(journal_path, json.dumps(journal, ensure_ascii=False), prefix=".profile_rename_")

        def save_references() -> None:
            schema_module.save_profile_config(new_schema)
            if constants.SESSION_PATH.exists():
                data = json.loads(constants.SESSION_PATH.read_text(encoding="utf-8"))
                _rename_session(data, renames)
                atomic_write_text(constants.SESSION_PATH, json.dumps(data, ensure_ascii=False, indent=2), prefix=".profile_rename_")
            if constants.BATCH_CONFIG_PATH.exists():
                data = json.loads(constants.BATCH_CONFIG_PATH.read_text(encoding="utf-8"))
                _rename_batch_parameters(data, renames)
                atomic_write_text(constants.BATCH_CONFIG_PATH, json.dumps(data, ensure_ascii=False, indent=2), prefix=".profile_rename_")

        try:
            db.rename_keys(renames, operation_id=operation_id, save_references=save_references)
        finally:
            # 数据库审计判断提交结果，避免 commit 成功后的清理失败误回滚文件。
            recover_rename(db)
            get_session_store().reload()
            schema_module.reload_profile_config()
        from .engine import _engine
        if _engine is not None:
            with db._connect() as conn:
                users = conn.execute("SELECT DISTINCT username FROM profile_entries").fetchall()
            for (username,) in users:
                _engine.data_updated.emit(username)
