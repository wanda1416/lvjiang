"""变更记录查询：条件构造与维度表达式。

历史对话框的筛选、聚合候选、跳页都走这一组 SQL。SQL 只在这里拼一次，别处不得
再写一套 WHERE，否则同一个界面会出现两种筛选语义。

本模块不依赖数据库连接：输入 :class:`~.history_query.HistoryQuery`，输出 WHERE
子句与参数，可以直接单测。
"""

from __future__ import annotations

from .history_query import HistoryQuery

# ─── 变更记录查询：条件构造与维度表达式 ─────────────────────────
#
# 历史对话框的筛选、聚合候选、跳页都走这三张表。SQL 只在这里拼一次，
# 别处不得再写一套 WHERE，否则同一个界面会出现两种筛选语义。

#: SELECT 的列顺序；``_history_row`` 与它一一对应，改这里要同时改那边。
HISTORY_COLUMNS = (
    "local_id, event_at, username, type, key, old_value, new_value, "
    "old_value_text, value_text, op_type, delta_value, source, sync_from, "
    "instance_id, op_id, remote_id, created_at"
)

#: 聚合候选的维度表达式。``key`` 是「模型:key」，与 UI 的「键」列同一口径。
FACET_COLUMNS = {
    "username": "username",
    "key": "type || ':' || key",
    "op_type": "op_type",
    "source": "COALESCE(source, '')",
    "sync_from": "COALESCE(sync_from, '')",
    "device": "COALESCE(instance_id, '')",
}

#: 变动量：优先用记账时写下的 ``delta_value``；没有就按新旧值补算；两者都
#: 缺（旧值本身未知，例如某键的第一条记录）时为 NULL，表示"无法确认"。
#: 不写成 ``COALESCE(delta_value, new - COALESCE(old, 0))``——那会把"不清楚
#: 起点"伪造成"从 0 增加了这么多"，与「变动量」列显示的「—」自相矛盾。
DELTA_AMOUNT_SQL = (
    "CASE WHEN delta_value IS NOT NULL THEN delta_value "
    "WHEN old_value IS NOT NULL THEN new_value - old_value END"
)

DELTA_COMPARATORS = {"pos": " > 0", "neg": " < 0", "zero": " = 0"}

#: 允许的排序：白名单，绝不拼接调用方传入的字符串。
HISTORY_ORDERS = {
    "id_desc": "local_id DESC",
    "id_asc": "local_id ASC",
    "ts_desc": "event_at DESC, local_id DESC",
    "ts_asc": "event_at ASC, local_id ASC",
}

ESCAPE = "ESCAPE '\\'"


def marks(count: int) -> str:
    return ", ".join("?" for _ in range(count))


def like_pattern(text: str) -> str:
    """把用户输入变成"包含"匹配的模式串。

    ``%`` / ``_`` 在 LIKE 里是通配符，用户输入的字面量必须转义，否则
    搜一个下划线会把整张表搜出来。
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def value_text_clause(column: str) -> str:
    """文本值与数值都按"包含"匹配：数值列 CAST 成文本再比。

    ``new`` 的文本列统一叫 ``value_text``（与云端同构），旧值文本仍是
    ``old_value_text``。
    """
    text_col = "value_text" if column == "new" else "old_value_text"
    number_col = "new_value" if column == "new" else "old_value"
    return (
        f"(COALESCE({text_col}, '') LIKE ? {ESCAPE} "
        f"OR CAST({number_col} AS TEXT) LIKE ? {ESCAPE})"
    )


def history_where(query: HistoryQuery) -> tuple[str, list[object]]:
    """把条件对象翻成 WHERE 子句与参数。"""
    clauses: list[str] = []
    params: list[object] = []

    if query.usernames:
        clauses.append(f"username IN ({marks(len(query.usernames))})")
        params.extend(query.usernames)
    if query.keys:
        clauses.append(
            "(" + " OR ".join(["(type = ? AND key = ?)"] * len(query.keys)) + ")")
        for model_type, key in query.keys:
            params.extend((model_type, key))
    if query.model_types:
        clauses.append(f"type IN ({marks(len(query.model_types))})")
        params.extend(query.model_types)
    if query.key_names:
        clauses.append(f"key IN ({marks(len(query.key_names))})")
        params.extend(query.key_names)
    if query.change_types:
        clauses.append(f"op_type IN ({marks(len(query.change_types))})")
        params.extend(query.change_types)
    if query.sources:
        clauses.append(f"COALESCE(source, '') IN ({marks(len(query.sources))})")
        params.extend(query.sources)
    if query.sync_froms:
        clauses.append(
            f"COALESCE(sync_from, '') IN ({marks(len(query.sync_froms))})")
        params.extend(query.sync_froms)
    if query.devices:
        clauses.append(
            f"COALESCE(instance_id, '') IN ({marks(len(query.devices))})")
        params.extend(query.devices)
    if query.ts_from:
        clauses.append("event_at >= ?")
        params.append(query.ts_from)
    if query.ts_until:
        clauses.append("event_at < ?")
        params.append(query.ts_until)
    if query.delta_scope != "all":
        clauses.append(DELTA_AMOUNT_SQL + DELTA_COMPARATORS[query.delta_scope])
    if query.old_text:
        clauses.append(value_text_clause("old"))
        params.extend((like_pattern(query.old_text),) * 2)
    if query.new_text:
        clauses.append(value_text_clause("new"))
        params.extend((like_pattern(query.new_text),) * 2)
    if query.keyword or query.key_matches:
        parts: list[str] = []
        if query.keyword:
            pattern = like_pattern(query.keyword)
            parts.extend([
                f"COALESCE(source, '') LIKE ? {ESCAPE}",
                f"username LIKE ? {ESCAPE}",
                f"type LIKE ? {ESCAPE}",
                f"key LIKE ? {ESCAPE}",
                f"COALESCE(old_value_text, '') LIKE ? {ESCAPE}",
                f"COALESCE(value_text, '') LIKE ? {ESCAPE}",
            ])
            params.extend([pattern] * len(parts))
        for model_type, key in query.key_matches:
            parts.append("(type = ? AND key = ?)")
            params.extend((model_type, key))
        clauses.append("(" + " OR ".join(parts) + ")")

    return (" AND ".join(clauses) if clauses else "1 = 1"), params


def single_history_query(
    username: str | None, type_: str | None, key: str | None,
) -> HistoryQuery:
    """把旧的「单用户 / 单键」参数翻成条件对象（便捷入口专用）。

    ``type_`` 与 ``key`` 可以只给一个，语义是"该模型下的全部键"或
    "所有模型下的同名键"，与旧的等值筛选完全一致。
    """
    return HistoryQuery(
        usernames=(username,) if username is not None else (),
        model_types=(type_,) if type_ is not None else (),
        key_names=(key,) if key is not None else (),
    )
