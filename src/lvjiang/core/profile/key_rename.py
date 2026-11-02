"""Profile 身份维护：引用更新、SQL 审计及跨文件失败恢复。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

from ..fs_util import atomic_write_text
from ..json_files import formatted_json
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


def _write_references(schema, renames: list[tuple[str, str, str]]) -> None:
    """把改名应用到定义文件与引用文件；重复执行结果相同（幂等）。"""
    from ... import constants
    from ..config.interface import interface_path
    from . import schema as schema_module

    schema_module.save_profile_config(rename_schema_references(schema, renames))
    if interface_path().exists():
        data = json.loads(interface_path().read_text(encoding="utf-8"))
        _rename_interface(data, renames)
        atomic_write_text(interface_path(), json.dumps(data, ensure_ascii=False, indent=2),
                          prefix=".profile_rename_")
    if constants.BATCH_CONFIG_PATH.exists():
        data = json.loads(constants.BATCH_CONFIG_PATH.read_text(encoding="utf-8"))
        _rename_batch_parameters(data, renames)
        atomic_write_text(constants.BATCH_CONFIG_PATH,
                          json.dumps(data, ensure_ascii=False, indent=2),
                          prefix=".profile_rename_")


def recover_rename(db: ProfileDB) -> None:
    """重放未完成的改名。

    journal 只记 renames 列表，不存文件快照：DB 的 ``UPDATE ... WHERE key=old``
    与文件里的 ``replace_key`` 都可重复执行，因此无论崩溃发生在事务提交前还是
    提交后，重放都收敛到同一状态，不需要再判断「SQL 是否已提交」。
    """
    path = _journal_path(db)
    if not path.exists():
        return
    journal = json.loads(path.read_text(encoding="utf-8"))
    renames = [tuple(item) for item in journal.get("renames", [])]
    if renames:
        from .schema import get_profile_config
        db.rename_keys(
            renames,
            save_references=lambda: _write_references(get_profile_config(), renames))
    path.unlink()
    from ..config import get_interface_store
    from .schema import reload_profile_config
    get_interface_store().reload()
    reload_profile_config()


def _rename_interface(data: dict, renames: list[tuple[str, str, str]]) -> None:
    profile = data.get("profile", {})
    for group in profile.get("overview_groups", {}).values():
        group["columns"] = [replace_key(key, renames) for key in group.get("columns", [])]
    alerts = data.get("alert_history", {})
    # 告警标识为 username:key:level:current，与引擎生成格式一致。
    for name in list(alerts):
        parts = name.split(":")
        if len(parts) == 4:
            replaced = replace_key(parts[1], renames)
            if replaced != parts[1]:
                parts[1] = replaced
                alerts[":".join(parts)] = alerts.pop(name)
    batch = data.get("batch", {})
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
    from ..access import is_readonly
    from ..config import get_interface_store
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
        get_interface_store()  # 在引用维护前完成存储迁移。
        journal_path = _journal_path(db)
        atomic_write_text(journal_path, formatted_json({"renames": renames}),
                          prefix=".profile_rename_")

        def save_references() -> None:
            _write_references(schema, renames)

        try:
            db.rename_keys(renames, save_references=save_references)
        finally:
            # 幂等重放：中断在任何位置都能收敛到同一状态。
            recover_rename(db)
            get_interface_store().reload()
            schema_module.reload_profile_config()
        from .events import notify_updated
        with db._connect() as conn:
            users = conn.execute("SELECT DISTINCT username FROM profile_entries").fetchall()
        for (username,) in users:
            notify_updated(username)
