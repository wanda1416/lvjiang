"""一次性诊断：查询云端 profile 账本中指定 key 的全部 op。

用法：python scripts/one_off/query_remote_profile_ops.py [key]
默认 key 为 baiye_raoliang_shengwang_of_month。只读查询，不修改任何数据。
"""
import json
import sys

from lvjiang.core.account.service import AccountSession
from lvjiang.core.profile.snapshot_sync import current_instance_id
from lvjiang.core.sync.client import post

KEY = sys.argv[1] if len(sys.argv) > 1 else "baiye_raoliang_shengwang_of_month"


def main() -> int:
    session = AccountSession()
    if session.restore(fetch_summary=False) is None:
        print("本地无可用登录态，无法查询云端")
        return 1
    token = session.access_token
    mine = current_instance_id()

    head = post("/api/sync/profile/snapshot", {}, token).get("head_remote_id")
    print(f"云端 head_remote_id={head}")

    since = 0
    total = 0
    matches = []
    while True:
        response = post("/api/sync/profile/pull",
                        {"since_remote_id": since, "limit": 500}, token)
        ops = response.get("ops") or []
        for op in ops:
            total += 1
            if op.get("key") == KEY:
                matches.append(op)
        cursor = int(response.get("last_remote_id") or since)
        if cursor <= since or not ops:
            break
        since = cursor

    print(f"云端账本共 {total} 条 op，匹配 key={KEY} 的有 {len(matches)} 条")
    for op in matches:
        tag = "本机写入" if op.get("instance_id") == mine else "其他设备写入"
        print(json.dumps(op, ensure_ascii=False, indent=2), f"[{tag}]")
    if not matches:
        print("云端账本中没有该 key 的 op")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
