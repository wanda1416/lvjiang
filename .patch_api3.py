import io

p = "src/lvjiang/core/ondevice/account_api.py"
raw = io.open(p, encoding="utf-8", newline="").read()
crlf = "\r\n" in raw
s = raw.replace("\r\n", "\n")

# account_profile_mode → account_profile_status（不再有 mode）
old = '''def account_profile_mode() -> str:
    """总览数据来源模式：`{ok, mode, snapshot_id, snapshot_remote_id, pending_push}`。

    snapshot_id/remote_id 是手机当前跟随的云端位置（哪份快照、回放到账本
    哪个水位）；pending_push 是水位之上本机产生、尚未上云的变更行数——
    切到 PC端前必须让用户知道这些数据会留在本地不再上云。未落地快照为 0。
    """
    from ..account.service import profile_sync_state
    from ..profile.repository import db_count_pending_push
    from . import profile_source

    state = profile_sync_state()
    watermark = int(state.get("remote_history_id") or 0)
    return json.dumps({
        "ok": True, "mode": profile_source.mode(),
        "snapshot_id": int(state.get("profile_snapshot_id") or 0),
        "snapshot_remote_id": watermark,
        "pending_push": db_count_pending_push(
            int(state.get("local_history_id") or 0), _author_stamp()),
    }, ensure_ascii=False)'''
new = '''def account_profile_status() -> str:
    """profile 同步状态：`{ok, snapshot_id, snapshot_remote_id, pending_push}`。

    snapshot_id/remote_id 是手机当前跟随的云端位置（哪份快照、回放到账本
    哪个水位）；pending_push 是水位之上本机产生、尚未上云的变更行数。
    """
    from ..account.service import profile_sync_state
    from ..profile.repository import db_count_pending_push

    state = profile_sync_state()
    watermark = int(state.get("remote_history_id") or 0)
    return json.dumps({
        "ok": True,
        "snapshot_id": int(state.get("profile_snapshot_id") or 0),
        "snapshot_remote_id": watermark,
        "pending_push": db_count_pending_push(
            int(state.get("local_history_id") or 0), _author_stamp()),
    }, ensure_ascii=False)'''
assert old in s, "profile_mode"
s = s.replace(old, new)

# account_set_profile_mode 整个删除
start = s.index("def account_set_profile_mode")
end = s.index("def account_clear_profile")
s = s[:start] + s[end:]

# account_clear_profile 简化
old2 = '''def account_clear_profile(keep_mode: bool = False) -> str:
    """清除本地总览数据。``keep_mode`` 为真时保持当前模式（云端=下次同步从
    云端重拉；PC端=等下一次离线包）；为假时顺带切换为云端（PC端→云门的
    前置清除）。"""
    from . import profile_source

    try:
        message = profile_source.clear_local_profile(switch_to_remote=not keep_mode)
    except Exception as error:                 # noqa: BLE001 — 入口不外抛
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    return json.dumps({"ok": True, "mode": profile_source.mode(),
                       "message": message}, ensure_ascii=False)'''
new2 = '''def account_clear_profile() -> str:
    """清除本地总览数据并复位同步水位；下次同步自动重建（快照引导或首份基线）。"""
    from ..account.service import save_profile_sync_state
    from ..profile.repository import wipe_profile_data

    try:
        wipe_profile_data()
        save_profile_sync_state({})
    except Exception as error:                 # noqa: BLE001 — 入口不外抛
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    return json.dumps({"ok": True,
                       "message": "已清除本地总览数据；下次同步将自动重建。"},
                      ensure_ascii=False)'''
assert old2 in s, "clear"
s = s.replace(old2, new2)

# _reset_sync_state 与 docstring 更新
old3 = '''def _reset_sync_state() -> None:
    """清除 profile 同步水位（profile_source.clear_local_profile 调用）。"""
    from ..account.service import save_profile_sync_state

    save_profile_sync_state({})'''
new3 = '''def _reset_sync_state() -> None:
    """清除 profile 同步水位（account_clear_profile 调用）。"""
    from ..account.service import save_profile_sync_state

    save_profile_sync_state({})'''
assert old3 in s, "reset"
s = s.replace(old3, new3)

io.open(p, "w", encoding="utf-8", newline="").write(s.replace("\n", "\r\n") if crlf else s)
print("mode APIs removed")
