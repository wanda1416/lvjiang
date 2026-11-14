"""Android 账户桥接 —— 登录/登出/状态/同步四个 JSON 入口，供 PyBridge 调用。

profile 行为与 PC 一致：pull + push 全量、云端无快照时自动建首份基线；
本地数据可用「清除数据」重置，之后同步自动重建。
"""
from __future__ import annotations

import json
import threading

from loguru import logger

from ..account.service import (
    AccountError,
    AccountSession,
    current_keyring_user,
    get_session,
    load_profile,
    stored_token_owner,
)

# 进程内唯一会话由 core.account.service.get_session() 提供：新建第二个持有者会与
# 服务端的「单行会话 + 轮换令牌」打架，两边各自 refresh 就互相顶掉令牌。
# 登录态快照。首次询问后常驻内存，之后读它即可；登录/登出/令牌失效时刷新。
_STATUS_CACHE: dict | None = None
# 只是"现在连不上服务"，与登录态无关，别混进 logged_in。
_OFFLINE_CODES = frozenset({"network", "service_unavailable", "rejected"})


def _session() -> AccountSession:
    return get_session()


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
    """一轮镜像端同步；一轮只取一次云端版本行，没变化的通道不发请求。"""
    from ..sync.round_state import sync_round

    with sync_round():
        return _sync_round(token)


def _sync_round(token: str) -> dict:
    """镜像端同步：定义只拉不推、总览只拉快照+历史、绝不建快照。

    顺序是硬约束：**profile.yaml 先提交（拉取并重载 schema），再回放账本**。
    改名场景里新 key 的基线 set 会先于定义到达就会被当成未知项，值在却
    不显示、引擎不计算。

    **云端模式**：手机跑任务会改 profile.db，这些行按 author 过滤上行
    （push_local 只推本机产生的行，离线包带来的 PC 行被天然排除）。
    **PC端模式**：整体跳过——手机 db 是 PC 的镜像，推了必污染。

    定义与快照都是 **PC 独占的写权限**：手机上的 profile.yaml 是离线包副本，
    上行会把云端定义回退成旧版；建快照等于把手机 db 当成全网基线。两条都
    在底层做了硬门禁（``allow_push=False`` / ``refresh_snapshot`` 拒绝）。
    """
    from ..profile.snapshot_sync import (
        _ensure_baseline,
        profile_protocol_block,
        pull_and_replay,
        push_local,
    )
    from ..sync.definition_sync import sync_definition

    blocked = profile_protocol_block(token)
    if blocked:
        return {"synced": False, "reason": "protocol", "message": blocked}
    definition = sync_definition(token, allow_push=False)
    pulled = pull_and_replay(token)
    errors = [*definition.errors, *pulled.errors]
    # 先拉后推，与 PC 的 sync_all 同序；云端没有基线且本机有数据时自动建
    # （_ensure_baseline 内部经 refresh_snapshot，手机门禁已随模式一起删除）。
    pushed = push_local(token)
    errors.extend(pushed.errors)
    seeded = _ensure_baseline(token)
    if seeded is not None:
        errors.extend(seeded.errors)
    if pushed.rejected and not any("校准" in item for item in errors):
        errors.append(f"有 {pushed.rejected} 条记录因时间异常未上传，请校准系统时间")
    return {"synced": not errors,
            "pulled": pulled.pulled, "pushed": pushed.pushed,
            "snapshots": pulled.snapshots + (seeded.snapshots if seeded else 0),
            "rejected": pushed.rejected,
            "message": "；".join(errors[:3]) if errors else ""}


def _reset_sync_state() -> None:
    """清除 profile 同步水位（account_clear_profile 调用）。"""
    from ..account.service import save_profile_sync_state

    save_profile_sync_state({})


def account_profile_status() -> str:
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
    }, ensure_ascii=False)


def _author_stamp() -> str:
    from ..profile.repository import _author_stamp

    return _author_stamp()


def account_clear_profile() -> str:
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
                      ensure_ascii=False)


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


def _ensure_access_token() -> str:
    """心跳使用的 access 令牌。进程重启后内存是空的，先用 refresh 换一张。"""
    session = _session()
    if session.access_token:
        return session.access_token
    if not session.has_stored_session():
        return ""
    try:
        if session.restore(fetch_summary=False) is None:
            return ""
    except AccountError:
        return ""
    return session.access_token or ""


def _heartbeat_tick(ticks: int) -> int:
    """跑一轮前台同步。没有令牌时计数不变，调用方据此跳过这一分钟。"""
    session = _session()
    token = _ensure_access_token()
    if not token:
        return ticks
    ticks += 1
    if ticks % HEARTBEAT_EVERY_N_SYNC == 0:
        try:
            session.heartbeat()
        except AccountError:
            pass                          # 弱网抖动静默，下轮再试
    try:
        _sync(token)
    except AccountError:
        pass
    return ticks


def _heartbeat_loop() -> None:
    ticks = 0
    while not _heartbeat_stop.wait(SYNC_INTERVAL_S):
        ticks = _heartbeat_tick(ticks)


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
