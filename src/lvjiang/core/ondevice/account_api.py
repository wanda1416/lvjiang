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
import threading

from loguru import logger

from ..account.service import AccountError, AccountSession


def _session() -> AccountSession:
    return AccountSession()


def _sync(token: str) -> dict:
    """拉（快照回放 + 增量）+ 推（本机新 op）。不建快照。"""
    from ..profile.snapshot_sync import pull_and_replay, push_local

    pulled = pull_and_replay(token)
    # 远端没快照且本机未初始化：明确「无需同步」，不算错误
    from ..profile.snapshot_sync import NO_SNAPSHOT_HINT
    if any(error == NO_SNAPSHOT_HINT for error in pulled.errors):
        return {"synced": False, "reason": "no_snapshot",
                "message": "云端还没有快照；先在 PC 上刷新快照"}
    pushed = push_local(token)
    errors = [*pulled.errors, *pushed.errors]
    return {"synced": not errors,
            "pulled": pulled.pulled, "pushed": pushed.pushed,
            "message": "；".join(errors[:3]) if errors else ""}


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


def sync_after_task() -> None:
    """任务收尾钩子：后台推送本机新 op，失败只记日志（任务结果不受影响）。

    由 task_runner 在任务终态调用；网络慢/失败不阻塞、不报错到任务状态。
    """
    def run() -> None:
        try:
            result = json.loads(account_sync())
            if result.get("ok") and result.get("synced"):
                logger.info("[account] 任务后同步：上行 {} 条",
                            result.get("pushed", 0))
        except Exception as error:               # noqa: BLE001 — 钩子绝不外抛
            logger.warning("[account] 任务后同步未完成：{}", error)

    threading.Thread(target=run, daemon=True, name="account-sync").start()
