"""Android 账户桥接 —— 登录/登出/状态/同步四个 JSON 入口，供 PyBridge 调用。

与 PC 端的差异（按定案）：
- **只收不发**：手机 profile 只从云端快照 + 历史回放获取，永不上行——
  手机的 db 可能被离线包整库重建，推上去的是 PC 旧账，自己写的行又被
  下一次包冲掉；上行数据走既有的离线回传通道（手机 → PC → 云）。
- **不建快照**：Android 没有 profile 编辑能力，快照永远由 PC 刷新。
- **服从总览数据来源模式**：``profile_source`` 为 pc 模式时整体跳过
  profile 同步（离线包独占这份 db）。
- 云端没有快照时不做同步（无基线可回放）。
"""
from __future__ import annotations

import json
import threading

from loguru import logger

from ..account.service import (
    AccountError,
    AccountSession,
    current_keyring_user,
    load_profile,
    stored_token_owner,
)

# 进程内唯一会话：access 令牌只存在内存里，每次调用都新建一个 AccountSession
# 就等于每次都要重新 refresh（进一次设置页白打两个网络请求）。
_SESSION: AccountSession | None = None
# 登录态快照。首次询问后常驻内存，之后读它即可；登录/登出/令牌失效时刷新。
_STATUS_CACHE: dict | None = None
# 只是"现在连不上服务"，与登录态无关，别混进 logged_in。
_OFFLINE_CODES = frozenset({"network", "service_unavailable", "rejected"})


def _session() -> AccountSession:
    global _SESSION
    if _SESSION is None:
        _SESSION = AccountSession()
    return _SESSION


def _remember_status(value: dict) -> None:
    global _STATUS_CACHE
    _STATUS_CACHE = dict(value)


def _set_online(value: bool) -> None:
    """记录"服务此刻是否可达"。它只影响提示，不参与登录态判断。"""
    if _STATUS_CACHE is not None:
        _STATUS_CACHE["online"] = value


def _note_network_error(error: AccountError) -> None:
    if error.code in _OFFLINE_CODES:
        _set_online(False)


def _probe_status() -> dict:
    """登录态：**以本地凭据为准，绝不因为网络失败改口**。

    三层事实严格分开：

    - 有没有会话 = 本地凭据（refresh 令牌）在不在，只由用户登录/登出或服务端
      **明确**拒绝（``AUTH_REJECTED_CODES``）改变；
    - 能不能连上服务 = ``online``，只用于提示"暂时同步不了"；
    - 登录态被实例变更孤立（令牌在、但当前实例键匹配不上）= 单独一条 message，
      让用户知道该重新登录，而不是被当成"从没登录"。
    """
    session = _session()
    profile = load_profile()
    if session.access_token or session.has_stored_session():
        status = {"ok": True, "logged_in": True,
                  "login": str(profile.get("login") or ""),
                  "display_name": str(profile.get("display_name") or "")}
        if not status["login"]:
            try:
                summary = session.restore()
            except AccountError as error:
                # 拿不到账号名只是少个显示名，登录态照旧
                _note_network_error(error)
                return status
            if summary is None:
                return {"ok": True, "logged_in": False,
                        "message": "登录已过期，请重新登录"}
            status["login"] = summary["account"]["login"]
            status["display_name"] = summary["account"].get("display_name") or ""
        return status
    owner = stored_token_owner()
    if owner:
        logger.warning("登录态与当前实例不匹配：令牌属于 {}，当前实例 {}",
                       owner, current_keyring_user())
        return {"ok": True, "logged_in": False, "orphaned": True,
                "message": "本机登录记录与当前实例不匹配（实例标识变了），请重新登录"}
    return {"ok": True, "logged_in": False}


def _sync(token: str) -> dict:
    """定义先行、账本双向（云端模式）；PC端模式只收不发。

    顺序是硬约束：**profile.yaml 先提交（拉取并重载 schema），再回放账本**。
    改名场景里新 key 的基线 set 会先于定义到达就会被当成未知项，值在却
    不显示、引擎不计算——手机此前根本不同步定义（只靠离线包），这是改名
    能否无感传播的前提缺口。

    **云端模式下手机是对等写入端**：手机上跑任务直接改 profile.db，这些
    变更必须上行云端（push_local 只推本机产生的行，author 过滤天然排除
    离线包带来的 PC 行——离线包重建后误推 PC 旧账的旧顾虑已被该机制堵死）。
    PC端模式维持只收不发：db 是 PC 的镜像，推了必污染。

    pc 模式下整体跳过 profile：手机 db 由离线包整库重建（PC 的镜像），
    在线通道碰它就会两条线打架（推出去的是 PC 旧账；自己写的行又被下一
    次包冲掉）。上行只走离线回传。
    """
    from . import profile_source

    if profile_source.mode() == profile_source.PC:
        return {"synced": True, "skipped": "profile",
                "message": "总览数据处于 PC端模式，已跳过在线同步"}
    from ..profile.snapshot_sync import NO_SNAPSHOT_HINT, pull_and_replay, push_local
    from ..sync.definition_sync import sync_definition

    definition = sync_definition(token)
    pulled = pull_and_replay(token)
    errors = [*definition.errors, *pulled.errors]
    if any(error == NO_SNAPSHOT_HINT for error in pulled.errors):
        return {"synced": False, "reason": "no_snapshot",
                "message": "云端还没有快照；先在 PC 端刷新快照"}
    if getattr(pulled, "pending", None):
        # 本地有数据但从未落地过快照（典型：离线包重建后重新登录）。绝不
        # 静默跳过——必须让用户知道 profile 没同步、该怎么处理。
        return {"synced": False, "reason": "pending_overwrite",
                "message": "检测到本地已有总览数据；请先在「设置 → 总览数据」"
                           "点「清除数据」，再执行同步"}
    # 先拉后推：拿到远端状态再补自己的新变更，与 PC 端 sync_all 同序
    pushed = push_local(token)
    errors.extend(pushed.errors)
    return {"synced": not errors,
            "pulled": pulled.pulled, "pushed": pushed.pushed,
            "message": "；".join(errors[:3]) if errors else ""}


def _reset_sync_state() -> None:
    """清除 profile 同步水位（profile_source.clear_local_profile 调用）。"""
    from ..account.service import save_profile_sync_state

    save_profile_sync_state({})


def account_profile_mode() -> str:
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
    }, ensure_ascii=False)


def _author_stamp() -> str:
    from ..profile.repository import _author_stamp

    return _author_stamp()


def account_set_profile_mode(mode_value: str, force: bool = False) -> str:
    """切换模式。切 PC端带未同步风险评估（pending_push > 0 且未 force 时
    返回 need_confirm 要求二次确认）；切回云端必须先清除本地总览数据。"""
    from ..account.service import profile_sync_state
    from ..profile.repository import db_count_pending_push
    from . import profile_source

    if mode_value == profile_source.PC:
        if profile_source.mode() != profile_source.PC and not force:
            state = profile_sync_state()
            pending = db_count_pending_push(
                int(state.get("local_history_id") or 0), _author_stamp())
            if pending > 0:
                return json.dumps(
                    {"ok": False, "need_confirm": True, "pending_push": pending,
                     "message": f"还有 {pending} 条本机变更未同步到云端。"
                                "切到 PC端后这些变更会留在本机、不再上云，"
                                "可能与云端数据分叉。确认切换？"},
                    ensure_ascii=False)
        profile_source.set_mode(profile_source.PC)
        return json.dumps({"ok": True, "mode": profile_source.PC,
                           "message": "总览数据改为 PC端；离线包将携带总览数据"},
                          ensure_ascii=False)
    if mode_value == profile_source.REMOTE:
        if profile_source.mode() == profile_source.PC:
            return json.dumps(
                {"ok": False, "need_clear": True,
                 "message": "请先点「清除数据」，再切换为云端"},
                ensure_ascii=False)
        profile_source.set_mode(profile_source.REMOTE)
        return json.dumps({"ok": True, "mode": profile_source.REMOTE},
                          ensure_ascii=False)
    return json.dumps({"ok": False, "message": "未知模式"}, ensure_ascii=False)


def account_clear_profile(keep_mode: bool = False) -> str:
    """清除本地总览数据。``keep_mode`` 为真时保持当前模式（云端=下次同步从
    云端重拉；PC端=等下一次离线包）；为假时顺带切换为云端（PC端→云门的
    前置清除）。"""
    from . import profile_source

    try:
        message = profile_source.clear_local_profile(switch_to_remote=not keep_mode)
    except Exception as error:                 # noqa: BLE001 — 入口不外抛
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    return json.dumps({"ok": True, "mode": profile_source.mode(),
                       "message": message}, ensure_ascii=False)


def account_status() -> str:
    """登录态。**内存快照优先**：进程内第一次才真的问一次（凭据库 + 必要时
    一次网络校验），之后进设置页只是读缓存；登录/登出/令牌失效时才刷新。
    之前每次调用都新建会话并 refresh+summary，等于进一次设置页打两个网络请求。"""
    global _STATUS_CACHE
    if _STATUS_CACHE is None:
        _STATUS_CACHE = _probe_status()
    return json.dumps(_STATUS_CACHE, ensure_ascii=False)


def account_login(login: str, password: str) -> str:
    """账密登录。**只做登录本身**，不在这里串行拉快照：

    一次登录要走的网络请求越少越好——手机弱网下每一次都是一轮 TLS 握手。
    这里只发 ``POST /api/login``（响应已带账号名），成功立刻返回；profile
    同步由界面在后台另起一次 ``account_sync``，成功与否都不影响"已登录"。
    """
    session = _session()
    try:
        summary = session.login(login, password, fetch_summary=False)
    except AccountError as error:
        # 登录失败只在"网络/服务不可用"时标记在线状态，登录态本身不动
        _note_network_error(error)
        return json.dumps({"ok": False, "message": str(error),
                           "code": error.code or "", "offline": error.code in _OFFLINE_CODES},
                          ensure_ascii=False)
    result = {"ok": True, "login": summary["account"]["login"],
              "display_name": summary["account"].get("display_name") or "",
              "pending_sync": True}
    _remember_status({"ok": True, "logged_in": True, "online": True,
                      "login": result["login"], "display_name": result["display_name"]})
    return json.dumps(result, ensure_ascii=False)


def account_logout() -> str:
    session = _session()
    try:
        session.logout()
    except AccountError as error:
        return json.dumps({"ok": False, "message": str(error)}, ensure_ascii=False)
    _remember_status({"ok": True, "logged_in": False})
    return json.dumps({"ok": True}, ensure_ascii=False)


def account_sync() -> str:
    """同步。网络失败只影响 `online`，**绝不改登录态**；只有服务端明确拒绝
    凭据（``AUTH_REJECTED_CODES``）才清本地登录。"""
    session = _session()
    if not session.access_token:
        try:
            if session.restore(fetch_summary=False) is None:
                _remember_status({"ok": True, "logged_in": False})
                return json.dumps({"ok": False, "message": "尚未登录"}, ensure_ascii=False)
        except AccountError as error:
            _note_network_error(error)
            return json.dumps({"ok": False, "message": str(error),
                               "offline": error.code in _OFFLINE_CODES},
                              ensure_ascii=False)
    try:
        result = _sync(session.access_token)
    except AccountError as error:
        _note_network_error(error)
        return json.dumps({"ok": False, "message": str(error),
                           "offline": error.code in _OFFLINE_CODES}, ensure_ascii=False)
    _set_online(True)
    return json.dumps({"ok": True, **result}, ensure_ascii=False)


# ─── 在线节拍：前台期间每分钟自动同步、每 10 分钟心跳一次 ────────────────
# 前后台由 Kotlin Activity 的 onResume/onPause 驱动（account_heartbeat_start/stop），
# 后台全停：省电，且"在线=App 在前台"与用户直觉一致。在线判定窗口 20 分钟
# （服务端设备列表口径），心跳 10 分钟一跳即容错两跳。

SYNC_INTERVAL_S = 60
HEARTBEAT_EVERY_N_SYNC = 10            # 60s × 10 = 10 分钟一跳
_heartbeat_stop = threading.Event()
_heartbeat_thread: threading.Thread | None = None


def _heartbeat_loop() -> None:
    ticks = 0
    while not _heartbeat_stop.wait(SYNC_INTERVAL_S):
        session = _session()
        if not session.access_token and not session.has_stored_session():
            continue                          # 未登录不跳不同步
        ticks += 1
        if ticks % HEARTBEAT_EVERY_N_SYNC == 0:
            try:
                session.heartbeat()
            except AccountError:
                pass                          # 弱网抖动静默，下轮再试
        try:
            _sync(session.access_token)       # 每分钟刷新同步状态
        except AccountError:
            pass


def account_heartbeat_start() -> str:
    """启动心跳循环（幂等）。"""
    global _heartbeat_thread
    if _heartbeat_thread is not None and _heartbeat_thread.is_alive():
        return json.dumps({"ok": True, "running": True})
    _heartbeat_stop.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, name="account-heartbeat", daemon=True)
    _heartbeat_thread.start()
    return json.dumps({"ok": True, "running": True})


def account_heartbeat_stop() -> str:
    """停止心跳循环（幂等）。"""
    _heartbeat_stop.set()
    return json.dumps({"ok": True, "running": False})
