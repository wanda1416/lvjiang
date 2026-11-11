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
    """把改名应用到定义自身的 key 和同步目标引用。重复执行结果相同。

    调用方传入的 schema 可能已经把 key 改成新名（编辑器内层保存）；磁盘上的
    schema 则可能还是旧名（崩溃重放）。两种输入都收敛到新 key，已是新名的
    定义不再改。目标 key 已有另一条定义时拒绝，避免写出两个同名 key。
    """
    result = deepcopy(schema)
    index = {(kind, old): new for kind, old, new in renames}
    occupied = {
        definition.key
        for definitions in result.keys_by_model.values()
        for definition in definitions
    }
    for model, definitions in result.keys_by_model.items():
        for definition in definitions:
            new = index.get((model, definition.key))
            if not new or new == definition.key:
                continue
            if new in occupied:
                raise ValueError(f"目标 key {new} 已存在，不能合并")
            occupied.remove(definition.key)
            occupied.add(new)
            definition.key = new
    for definitions in result.keys_by_model.values():
        for definition in definitions:
            for target in definition.sync_targets:
                target.key = replace_key(target.key, renames)
    result._rebuild_index()
    return result


def _journal_path(db: ProfileDB) -> Path:
    return db._db_path.with_suffix(".rename-journal.json")


def _write_references(schema, renames: list[tuple[str, str, str]]) -> None:
    """把改名应用到定义文件与引用文件；重复执行结果相同（幂等）。"""
    from ... import constants
    from ..config.interface import interface_path
    from . import layout as layout_module
    from . import schema as schema_module

    schema_module.save_profile_config(rename_schema_references(schema, renames))
    # 总览的列引用与列宽住在 profile.json（可见结构），不在 interface.json。
    layout_module.rename_column_keys(lambda key: replace_key(key, renames))
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
        from .schema import reload_profile_config
        # 重放只认磁盘。保存函数不更新内存单例，用改名前的单例会把旧 key 写回去。
        reload_profile_config()
        db.rename_keys(
            renames,
            save_references=lambda: _write_references(reload_profile_config(), renames))
    path.unlink()
    from ..config import get_interface_store
    from .schema import reload_profile_config
    get_interface_store().reload()
    reload_profile_config()


def _rename_interface(data: dict, renames: list[tuple[str, str, str]]) -> None:
    """interface.json 里与改名相关的只剩告警标识与批量草稿。

    总览分组的列引用与列宽已迁到 ``profile.json``（见
    :func:`lvjiang.core.profile.layout.rename_column_keys`）。
    """
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
    """批量配置组只剩定义层字段；生命周期参数已迁到运行草稿，不再处理。"""
    for group in data.get("groups", {}).values():
        if "profile_sort_key" in group:
            group["profile_sort_key"] = replace_key(group["profile_sort_key"], renames)


def save_renamed_definitions(
    schema: ProfileSchema, renames: list[tuple[str, str, str]], *,
    db: ProfileDB | None = None,
) -> None:
    """仅用于定义编辑器确认保存；调用前必须确认设备任务已停止。"""
    from ..config import get_interface_store
    from . import schema as schema_module
    from .repository import get_profile_db
    from .triggers import _runner

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
        _emit_rename_baselines(db, renames)
        from .events import notify_updated
        # _connect() 的连接必须显式关（同 _emit_rename_baselines）
        conn = db._connect()
        try:
            users = conn.execute(
                "SELECT DISTINCT username FROM profile_entries").fetchall()
        finally:
            conn.close()
        for (username,) in users:
            notify_updated(username)


def _emit_rename_baselines(db: ProfileDB,
                           renames: list[tuple[str, str, str]]) -> None:
    """为每个改名的 key 重发全量基线 set——跨端无感传播的核心。

    已推送的云端 op 仍挂旧 key 且水位不回退，其他设备（含手机）对新 key
    **没有基线**：改名后第一条 delta 会在 0 基线上算错。这里为每个拥有该
    key 的用户写一行 ``set(当前值)``（id 在水位之上，随分钟级自动同步上
    行），回放端按时间戳合并必然应用，新 key 直接落正确值——手机无需清
    数据、无需重新 bootstrap、无需手动刷新快照。
    定义先行的顺序由同步周期保证：定义推送先于账本（PC sync 通道顺序），
    手机 _sync 先拉定义再回放。
    """
    from ...i18n import tr as _tr
    from .schema import get_profile_config

    defined = get_profile_config()
    for model, old, new in renames:
        if defined.get_key(new, model_type=model) is None:
            from loguru import logger as _logger
            _logger.error(
                "[profile rename] 定义中没有 {}，跳过基线 set（{}→{}）",
                new, old, new)
            continue
        # _connect() 返回的连接必须显式关：sqlite3.connect 的上下文管理器
        # 只提交事务不关连接（与 repository._ensure_schema 的备份连接同坑）
        conn = db._connect()
        try:
            rows = conn.execute(
                "SELECT username, value, value_text, locked FROM profile_entries "
                "WHERE type=? AND key=?", (model, new)).fetchall()
        finally:
            conn.close()
        emitted = 0
        for username, value, value_text, locked in rows:
            if locked:
                continue      # 锁定条目物理失效，不参与展示与同步
            if value in (None, 0) and not value_text:
                continue      # 空占位不值得基线行
            db.upsert(username, model, new, float(value or 0),
                      value_text=value_text or "",
                      change_type="set", source=_tr("key 改名重设"))
            emitted += 1
        if emitted:
            from loguru import logger as _logger
            _logger.info("[profile rename] {}→{} 已重发 {} 位用户基线 set",
                         old, new, emitted)
