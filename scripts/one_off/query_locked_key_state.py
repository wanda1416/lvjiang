"""一次性诊断：核对云端快照与本地 profile 库中指定 key 的状态。只读。"""
import json
import sqlite3
import sys

from lvjiang.core.account.service import AccountSession
from lvjiang.core.profile import repository

KEY = "baiye_raoliang_shengwang_of_month"
USERS = ["宛元芷", "寒铁岚"]


def main() -> int:
    session = AccountSession()
    if session.restore(fetch_summary=False) is None:
        print("本地无可用登录态")
        return 1
    token = session.access_token

    # 云端快照 #2 中该 key 的 entries
    response = post_snapshot(token)
    entries = json.loads(response.get("snapshot", {}).get("entries") or "[]")
    print("── 云端快照 entries 中该 key ──")
    for entry in entries:
        if entry.get("key") == KEY:
            print(json.dumps(
                {k: entry.get(k) for k in
                 ("username", "type", "key", "value", "value_text", "updated_at", "locked")},
                ensure_ascii=False))

    # 本地库 entry 状态
    db = repository.get_profile_db()
    print("── 本地 profile_entries ──")
    conn = db._borrow_connection()  # noqa: SLF001 诊断脚本直接用连接
    try:
        for username in USERS:
            row = conn.execute(
                "SELECT username, value, value_text, updated_at, updated_time, locked "
                "FROM profile_entries WHERE username=? AND type='quota' AND key=?",
                (username, KEY)).fetchone()
            print(row)
        print("── 本地 profile_history 该 key 最近 10 条 ──")
        for row in conn.execute(
                "SELECT ts, username, change_type, delta_value, source, sync_from, device_id "
                "FROM profile_history WHERE type='quota' AND key=? "
                "ORDER BY id DESC LIMIT 10", (KEY,)):
            print(row)
    finally:
        db._release_connection(conn)  # noqa: SLF001
    return 0


def post_snapshot(token: str) -> dict:
    from lvjiang.core.sync.client import post
    return post("/api/sync/profile/snapshot", {"id": 2}, token)


if __name__ == "__main__":
    raise SystemExit(main())
