"""Android 账户桥接 —— 登录/登出/状态/同步四个 JSON 入口，供 PyBridge 调用。

与 PC 端的差异（按定案）：
- **不建快照**：Android 没有 profile 编辑能力，只是被动接受远端快照与历史
  回放；快照永远由 PC 刷新。
- **任务结束后自动推送**：task_runner 在任务收尾时调 ``sync_after_task``，
  把本机新产生的 set/delta op 上行（后台线程，不阻塞任务收尾）。
- 远端没有快照时不做同步（无基线可回放，也无需上行——没有别的设备在等）。
"""
from __future__ import annotations

import json

from ..account.service import AccountError, AccountSession


def _session() -> AccountSession:
    return AccountSession()


def _sync(token: str) -> dict:
    """只拉不推，且服从「总览数据来源」模式：pc 模式下整体跳过 profile。

    手机 db 在 pc 模式下由离线包整库重建（PC 的镜像），在线通道碰它就会
    两条线打架（推出去的是 PC 旧账；自己写的行又被下一次包冲掉）。因此
    在线通道对手机是只读的，且 pc 模式下连读都不读。上行只走离线回传。
    """
    from . import profile_source

    if profile_source.mode() == profile_source.PC:
        return {"synced": True, "skipped": "profile",
                "message": "总览数据处于 PC 同步模式，已跳过在线同步"}
    from ..profile.snapshot_sync import NO_SNAPSHOT_HINT, pull_and_replay

    pulled = pull_and_replay(token)
    if any(error == NO_SNAPSHOT_HINT for error in pulled.errors):
        return {"synced": False, "reason": "no_snapshot",
                "message": "云端还没有快照；先在 PC 上刷新快照"}
    return {"synced": not pulled.errors,
            "pulled": pulled.pulled, "pushed": 0,
            "message": "；".join(pulled.errors[:3]) if pulled.errors else ""}


def _reset_sync_state() -> None:
    """清除 profile 同步水位（profile_source.clear_local_profile 调用）。"""
    from ..account.service import save_profile_sync_state

    save_profile_sync_state({})


def account_profile_mode() -> str:
    """总览数据来源模式：`{ok, mode}`（remote / pc）。"""
    from . import profile_source

    return json.dumps({"ok": True, "mode": profile_source.mode()},
                      ensure_ascii=False)


def account_set_profile_mode(mode_value: str) -> str:
    """切换模式。pc 随时可切；切回 remote 必须先清除本地总览数据。"""
    from . import profile_source

    if mode_value == profile_source.PC:
        profile_source.set_mode(profile_source.PC)
        return json.dumps({"ok": True, "mode": profile_source.PC,
                           "message": "总览数据改为 PC 同步；离线包将携带总览数据"},
                          ensure_ascii=False)
    if mode_value == profile_source.REMOTE:
        if profile_source.mode() == profile_source.PC:
            return json.dumps(
                {"ok": False, "need_clear": True,
                 "message": "请先点「清除本地总览数据」，再切换为远端同步"},
                ensure_ascii=False)
        profile_source.set_mode(profile_source.REMOTE)
        return json.dumps({"ok": True, "mode": profile_source.REMOTE},
                          ensure_ascii=False)
    return json.dumps({"ok": False, "message": "未知模式"}, ensure_ascii=False)


def account_clear_profile() -> str:
    """清除本地总览数据并切回远端同步模式。"""
    from . import profile_source

    try:
        message = profile_source.clear_local_profile()
    except Exception as error:                 # noqa: BLE001 — 入口不外抛
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    return json.dumps({"ok": True, "mode": profile_source.REMOTE,
                       "message": message}, ensure_ascii=False)


def account_status() -> str:
    """是否已登录（凭据库有令牌且能用它恢复会话）。"""
    session = _session()
    if not session.has_stored_session():
        return json.dumps({"ok": True, "logged_in": False}, ensure_ascii=False)
    try:
        summary = session.restore()
    except AccountError as error:
        return json.dumps({"ok": True, "logged_in": False,
                           "message": str(error)}, ensure_ascii=False)
    if summary is None:
        return json.dumps({"ok": True, "logged_in": False,
                           "message": "登录已过期，请重新登录"}, ensure_ascii=False)
    return json.dumps({"ok": True, "logged_in": True,
                       "login": summary["account"]["login"],
                       "display_name": summary["account"].get("display_name") or "",
                       "active_sessions": summary.get("active_sessions")},
                      ensure_ascii=False)


def account_login(login: str, password: str) -> str:
    session = _session()
    try:
        summary = session.login(login, password)
    except AccountError as error:
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    result = {"ok": True, "login": summary["account"]["login"],
              "display_name": summary["account"].get("display_name") or ""}
    # 登录成功即拉一轮：新设备落地快照 + 回放（远端没快照就等 PC 建）
    try:
        result["sync"] = _sync(session.access_token)
    except AccountError as error:
        result["sync"] = {"synced": False, "message": str(error)}
    return json.dumps(result, ensure_ascii=False)


def account_logout() -> str:
    session = _session()
    try:
        session.logout()
    except AccountError as error:
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    return json.dumps({"ok": True}, ensure_ascii=False)


def account_sync() -> str:
    session = _session()
    if not session.access_token:
        try:
            if session.restore() is None:
                return json.dumps({"ok": False, "message": "尚未登录"}, ensure_ascii=False)
        except AccountError as error:
            return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    try:
        result = _sync(session.access_token)
    except AccountError as error:
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    return json.dumps({"ok": True, **result}, ensure_ascii=False)
