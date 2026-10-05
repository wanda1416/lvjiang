"""仅在 schema v7 升级时解释旧 detail，不参与正常写入。"""

from __future__ import annotations

import math
import re
import sqlite3

from loguru import logger


def migrate_structured_history(conn: sqlite3.Connection) -> None:
    conn.execute("ALTER TABLE profile_history ADD COLUMN delta_value REAL")
    conn.execute("ALTER TABLE profile_history ADD COLUMN sync_from TEXT")
    # 无法解释的原文不能猜测或丢弃；只供升级审计，不再用于业务读取。
    conn.execute("CREATE TABLE profile_history_legacy (history_id INTEGER PRIMARY KEY, detail TEXT NOT NULL)")
    key_models: dict[str, set[str]] = {}
    for model, key in conn.execute(
        "SELECT type, key FROM profile_entries UNION SELECT type, key FROM profile_history"
    ):
        key_models.setdefault(key, set()).add(model)
    numeric = re.compile(r"^(delta|regen):([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$")
    unknown = 0
    for row in conn.execute(
        "SELECT id, type, change_type, detail, old_value, new_value FROM profile_history"
    ).fetchall():
        identity, model, kind, detail, old, new = row
        detail = detail or ""
        delta = None
        sync_from = None
        match = numeric.fullmatch(detail)
        if match and math.isfinite(float(match[2])):
            delta = float(match[2])
        elif detail.startswith("sync_from:") and detail[10:]:
            sync_from = detail[10:]
            models = key_models.get(sync_from, set())
            if ":" not in sync_from and len(models) == 1:
                sync_from = f"{next(iter(models))}:{sync_from}"
            # 再生的旧值可能是实时值，无法由入库整数准确反推实际变动。
            if model != "regen" and old is not None:
                delta = new - old
        elif detail == "reset:0":
            if kind == "tick":
                kind = "reset"
            if old is not None:
                delta = new - old
        elif detail.startswith("override:"):
            try:
                recognized = math.isfinite(float(detail[9:])) and float(detail[9:]) == new
            except ValueError:
                recognized = False
            if recognized and model != "regen" and old is not None:
                delta = new - old
            if not recognized:
                conn.execute("INSERT INTO profile_history_legacy VALUES (?, ?)", (identity, detail))
                unknown += 1
        elif detail:
            conn.execute("INSERT INTO profile_history_legacy VALUES (?, ?)", (identity, detail))
            unknown += 1
        conn.execute(
            "UPDATE profile_history SET change_type=?, delta_value=?, sync_from=? WHERE id=?",
            (kind, delta, sync_from, identity),
        )
    conn.execute("ALTER TABLE profile_history DROP COLUMN detail")
    conn.execute("""CREATE TABLE profile_key_renames (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, operation_id TEXT NOT NULL,
        type TEXT NOT NULL, old_key TEXT NOT NULL, new_key TEXT NOT NULL,
        entries_count INTEGER NOT NULL, history_count INTEGER NOT NULL,
        sync_count INTEGER NOT NULL
    )""")
    if unknown:
        logger.warning(
            "Profile 历史升级：{} 条旧详情无法解释，原文已保存在 profile_history_legacy；请核对迁移备份",
            unknown,
        )
