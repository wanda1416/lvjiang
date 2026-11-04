"""一次性修复：把声望绕梁定义收口到 baiye_rl_sw_of_month。

2026-11-04 的改名把数据库和总览列写成了新 key，profile.yaml 仍被重放盖回旧 key，
周期引擎随后给旧 key 补了首次初始化 reset(0)。本脚本删除这批合成行，并把
yaml 里这条定义的 key 改成新名。不重发基线 set，不改云端账本。

必须在律匠退出后运行。标准输出打印将删除的 history 行，供自行留底；
这些用户名不要抄进仓库或提交说明。

用法：python scripts/one_off/repair_baiye_rl_sw_rename.py
"""
from __future__ import annotations

import sqlite3

OLD = "baiye_raoliang_shengwang_of_month"
NEW = "baiye_rl_sw_of_month"


def main() -> int:
    from lvjiang.core.config.interface import interface_path
    from lvjiang.core.profile.key_rename import rename_schema_references
    from lvjiang.core.profile.repository import get_profile_db
    from lvjiang.core.profile.schema import (
        _PROFILE_PATH,
        get_profile_config,
        reload_profile_config,
        save_profile_config,
    )

    if not interface_path().is_file() or NEW not in interface_path().read_text(encoding="utf-8"):
        print("interface.json 里没有新 key，停止。这不是 2026-11-04 这次改名的现场。")
        return 2
    config = reload_profile_config()
    if config.get_key(NEW) is not None or config.get_key(OLD, model_type="quota") is None:
        print("profile.yaml 的 key 状态不符合「只有旧 key」，停止。")
        return 2

    db = get_profile_db()
    conn = db._connect()
    try:
        conn.row_factory = sqlite3.Row
        history = conn.execute(
            "SELECT id, ts, username, type, change_type, old_value, new_value, source "
            "FROM profile_history WHERE key=? ORDER BY id",
            (OLD,),
        ).fetchall()
        entries = conn.execute(
            "SELECT username, value, locked FROM profile_entries WHERE key=?",
            (OLD,),
        ).fetchall()
        baselines = conn.execute(
            "SELECT COUNT(*) FROM profile_history WHERE key=? AND change_type='set' "
            "AND source=?",
            (NEW, "key 改名重设"),
        ).fetchone()[0]
    finally:
        conn.close()

    if baselines < 1:
        print("新 key 上没有「key 改名重设」基线，停止。")
        return 2
    bad_history = [
        row for row in history
        if row["type"] != "quota" or row["change_type"] != "reset"
        or row["old_value"] is not None or row["new_value"] != 0
        or (row["source"] or "") != ""
    ]
    bad_entries = [
        row for row in entries if row["value"] != 0 or row["locked"]
    ]
    if bad_history or bad_entries:
        print(f"旧 key 上有非首次初始化数据：history {len(bad_history)}，entries {len(bad_entries)}。停止。")
        return 2

    print(f"将删除旧 key history {len(history)} 行、entries {len(entries)} 行。")
    for row in history:
        print(
            f"history id={row['id']} ts={row['ts']} username={row['username']} "
            f"old_value={row['old_value']} new_value={row['new_value']} source={row['source']!r}"
        )

    conn = db._connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM profile_history WHERE key=?", (OLD,))
        conn.execute("DELETE FROM profile_entries WHERE key=?", (OLD,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    save_profile_config(rename_schema_references(
        reload_profile_config(), [("quota", OLD, NEW)]))
    reloaded = reload_profile_config()
    if reloaded.get_key(NEW, model_type="quota") is None or reloaded.get_key(OLD) is not None:
        print(f"定义写回后校验失败：{_PROFILE_PATH}")
        return 1
    if get_profile_config().get_key(NEW).label != "百业绕梁之音·声望":
        print("新 key 的标签变了，停止后续操作前请检查 profile.yaml。")
        return 1
    print(f"已把定义 key 改为 {NEW}。基线 set 未重发，云端账本未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
