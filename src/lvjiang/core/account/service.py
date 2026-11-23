"""账号服务客户端 —— 账密登录、会话刷新、账号信息与自助改密。

登录态拆两半存放：refresh 令牌只进系统凭据库（明文文件不放），账号名与设备
标识进 ``config/session/account.json``（运行数据，机器级归属，不是凭据）。
access 令牌只在内存，30 天过期由 refresh 轮换，用户不感知。
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time  # noqa: F401 — 测试按 account_service.time 替换 sleep
from collections.abc import Callable
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

from loguru import logger

from ..fs_util import InterprocessLockTimeout, atomic_write_text, interprocess_lock
from ..json_files import formatted_json
from . import credentials, identity, transport
from .errors import AccountError

try:
    import keyring  # 桌面端系统凭据库；APK 未打包（Android 走沙箱文件存储）
except ImportError:                    # pragma: no cover - 仅 Android 环境出现
    keyring = None                     # type: ignore[assignment]


def _on_android() -> bool:
    return hasattr(sys, "getandroidapilevel")


def _session_dir() -> Path:
    """会话目录：**每次动态取** ``constants``。

    account.json 的所有读写者（登录态、profile 水位、document_sync、users_sync）
    必须解析出同一个路径。模块顶层绑定的那份常量会绕过测试对
    ``constants.SESSION_CONFIG_DIR`` 的 monkeypatch，导致「写一个文件、读另一个
    文件」——``sync/dirty.py`` 早就按动态口径取，这里对齐。
    """
    from ... import constants

    return constants.SESSION_CONFIG_DIR


def _android_token_path() -> Path:
    """Android 的 refresh 令牌落点：应用私有 session 目录（沙箱隔离，
    其他应用不可读——这是 Android 的安全边界，等价于桌面系统凭据库）。"""
    return _session_dir() / "account_token"


ACCOUNT_SERVICE = "https://lvjiang.wyxj.net"
_KEYRING_SERVICE = "lvjiang.account"
# 单次请求超时。手机在弱网/移动网络下握手明显比桌面慢，普通请求放宽到 20s
# （失败后会再重试一次，最坏 40s 给出结论）。
_REQUEST_TIMEOUT_S = 20
# 登录/刷新这类"用户正盯着界面等"的请求用更短的超时：与其让用户干等 40s，
# 不如 8s 内失败给出可行动的原因（重试一次，最坏 ~17s）。
_AUTH_TIMEOUT_S = 8

ERROR_MESSAGES = {
    "invalid_credentials": "账户名或密码不正确",
    "account_disabled": "账号已被禁用，请联系管理员",
    # 服务端已不再返回此码（revoked 设备凭密码登录会就地复活），留一行
    # 旧服务端兜底文案；不在 AUTH_REJECTED_CODES 里。
    "device_revoked": "设备登录已失效，请重新登录",
    "token_expired": "登录已过期，请重新登录",
    "invalid_request": "请求无效",
    "unauthorized": "未登录或会话已失效",
    "not_found": "服务接口不存在",
    "service_unavailable": "账号服务暂时不可用，请稍后再试",
    "pre_snapshot": "有早于云端快照的本机变更未上传，水位未前进。请在本机创建快照，把当前总览值收进新基线",
    "snapshot_required": "云端还没有总览快照，请先在电脑上完成初始同步",
    "stale_base": "本地同步进度落后于云端，请先拉取再同步",
    "snapshot_exists": "云端已有快照，请再点一次「初始同步」",
    # 页面结构只允许桌面端写入：手机与网页上的结构是下载来的副本，反推云端
    # 会把 PC 的排版回退成旧版。
    "readonly_platform": "页面结构以 PC 为准，手机与网页端只支持下载",
    "client_upgrade_required": "云端已启用全文档端到端加密，请升级客户端后再同步",
    "e2e_required": "云端已启用全文档端到端加密，拒绝明文写入",
}

# 服务端**明确**判定"这个凭据/会话不行"时才允许清理本地登录态。网络失败、
# 5xx、网关拦截都不在此列——它们只说明"现在问不到"，不能据此把用户踢下线。
AUTH_REJECTED_CODES = frozenset({
    "invalid_credentials", "token_expired", "account_disabled",
})


def account_config_path() -> Path:
    """账号运行态落 config/session（运行数据语义）。"""
    return _session_dir() / "account.json"


def load_profile() -> dict:
    path = account_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# account.json 是**多写入者**的共享运行态：登录/登出、profile 水位、document_sync、
# users_sync、sync_established 各写各的键。此前每个写入者各自「读整份 → 改一个键 →
# 整份写回」，三条写路径里还有两条是非原子的 ``write_text``：并发时就地覆盖别人的
# 键，甚至另一个线程读到半截 JSON → 解析失败返回 ``{}`` → 下一次写回把 login /
# account_id / e2e_key 整份抹掉。这里收敛成唯一写入点：进程内线程锁 + 跨进程文件锁
# （与 session.json 同一套约定，见 core/config/session.py 的 _mutate_disk）+ 原子写。
_PROFILE_LOCK = threading.RLock()
#: 等锁上限（秒）。account.json 的写入都是毫秒级，等这么久只能说明有实例卡住了；
#: 宁可放弃这次状态写（水位可从账本恢复），也不能把登录/心跳线程永久挂住。
_PROFILE_LOCK_TIMEOUT_S = 10.0


def _write_profile(profile: dict) -> None:
    """原子写回（调用方已持锁）：临时文件 + ``os.replace``，读端永不见半截文件。"""
    path = account_config_path()
    try:
        atomic_write_text(path, formatted_json(profile), prefix=".account-")
    except OSError as error:
        logger.warning("账号信息保存失败：{}（登录态仍保留在凭据库）", error)


def mutate_profile(mutate: Callable[[dict], None], *,
                   require_existing: bool = False) -> bool:
    """合并最新磁盘状态后原子写回；返回是否真的写成功。

    **唯一**的 account.json 写入口：``mutate`` 只拿得到最新读出的字典，改完由本函数
    写回，因此它只能动自己的键，不会覆盖别人的。``mutate`` 里**不得做任何 I/O**
    （不得写 session.json / profile.db）——否则会与那些文件的锁形成反向嵌套。

    ``require_existing=True``：文件不存在或读不出来时整体放弃（脏标记这类「丢了
    也无所谓、但绝不能凭空造一份空账号态出来」的写入用）。
    """
    path = account_config_path()
    try:
        with _PROFILE_LOCK, interprocess_lock(
                path, suffix="state", timeout=_PROFILE_LOCK_TIMEOUT_S):
            profile = load_profile()
            if require_existing and not profile:
                return False
            mutate(profile)
            _write_profile(profile)
            return True
    except InterprocessLockTimeout as error:
        logger.warning("账号信息暂未写入（等锁超时，下次同步会重试）：{}", error)
        return False


def current_instance_id() -> str:
    """本实例标识（出生标识）；回声护栏比对 op 的写入方时使用。"""
    return identity.stable_instance_id()


def sync_paused() -> bool:
    """是否已暂停自动同步（落 account.json，重启后保持）。"""
    return bool(load_profile().get("sync_paused"))


def set_sync_paused(paused: bool) -> None:
    def _set(profile: dict) -> None:
        profile["sync_paused"] = bool(paused)

    mutate_profile(_set)


def profile_sync_state() -> dict:
    """profile 账本同步状态（落 account.json）：

    - ``remote_history_id``：已应用的**远端**水位（云端 ``remote_id``，与本地
      ``profile_history.local_id`` 是两套编号，绝不互认）
    - ``local_history_id``：上行基线——快照上传/落地那一刻推进的本地最大
      ``local_id``，之下的行由快照整体覆盖，永不上传
    - ``snapshot_remote_id``：当前快照绑定的云端水位
    - ``profile_snapshot_id``：本设备跟随的**账户级**快照行 id（单一整数，
      不是按用户映射）
    """
    data = load_profile().get("profile_sync")
    return dict(data) if isinstance(data, dict) else {}


def save_profile_sync_state(state: dict) -> None:
    def _set(profile: dict) -> None:
        profile["profile_sync"] = state

    mutate_profile(_set)


def _credentials():
    """凭据后端：桌面端系统凭据库，Android 应用私有沙箱文件。

    具体选型在 :func:`credentials.select_backend`；这里只把「当前平台、keyring
    模块、令牌落点」三个平台事实交给它——测试替换本函数即可换掉整个后端。
    """
    return credentials.select_backend(
        on_android=_on_android(),
        keyring_module=keyring,
        token_path=_android_token_path,
    )


def _instance_uuid() -> str:
    """本实例标识：**出生标识**（独立文件、永不变化），与登录态无关。

    历史实现从 account.json 读 instance_uuid——删 account.json 就换身份，
    自己写的行自己不认；出生文件修复了这一点。
    """
    return identity.stable_instance_id()


def _keyring_user() -> str:
    """凭据库键带实例维度：同机多实例各存各的令牌，互不顶替。"""
    return identity.keyring_user(_instance_uuid())


def _stored_refresh_token() -> str:
    """读本实例在凭据库里的 refresh 令牌；没有或不可用按空串处理。"""
    return credentials.read_token(_credentials, _keyring_user())


def _store_refresh_token(token: str) -> None:
    """写入或清除本实例的 refresh 令牌（``token`` 为空即清除）。"""
    credentials.write_token(_credentials, _keyring_user(), token)


def _clear_stored_session() -> None:
    _store_refresh_token("")


def stored_token_owner() -> str:
    """凭据库里现存令牌归属的实例键；没有令牌返回空串。

    用途：区分「从没登录」与「登录态还在，但当前实例标识变了、匹配不上」——
    后者要明确告诉用户重新登录，而不是静默显示成未登录。
    """
    return credentials.token_owner(_credentials)


def current_keyring_user() -> str:
    """当前实例在凭据库里的键（``session:<实例标识>``），供日志与诊断使用。"""
    return _keyring_user()


def _network_hint(error: BaseException) -> str:
    """把底层网络异常翻成用户能照着做的中文（原文只进日志）。"""
    return transport.network_hint(error)


def _open_once(request: urlrequest.Request, timeout: float | None = None) -> bytes:
    """发一次请求；网络类异常原样抛出，交给调用方决定是否重试。

    只做转发：测试替换本函数即可让整条请求链（含重试与错误分类）走假实现。
    """
    return transport.open_once(request, timeout, default_timeout=_REQUEST_TIMEOUT_S)


def _http_error(exc: urlerror.HTTPError) -> AccountError:
    """把 HTTP 错误分成三类，**只有第一类允许清本地登录态**（分类规则见
    :func:`transport.http_error`；文案表与「认证被拒」集合由本模块给出）。"""
    return transport.http_error(
        exc, messages=ERROR_MESSAGES, rejected=AUTH_REJECTED_CODES)


def _request(method: str, route: str, data: dict | None = None,
             token: str = "", timeout: float | None = None) -> dict:
    """对账号服务发一次业务请求（组装、重试与错误分类见 :mod:`.transport`）。"""
    return transport.request(
        method, ACCOUNT_SERVICE + route, data,
        token=token, timeout=timeout, default_timeout=_REQUEST_TIMEOUT_S,
        open_once=_open_once, http_error=_http_error,
    )


def _android_device_name() -> str:
    """Android 上的机型名（``Build.MODEL``，例如 V2415A）。

    ``socket.gethostname()`` 在 Android 恒为 "localhost"，设备列表里全是
    localhost 没法分辨是哪台手机，所以这里读系统机型；品牌名已经在机型里
    （如 "Pixel 7"）就不重复拼。取不到时返回空串，由调用方兜底。
    """
    try:
        from java import jclass  # Chaquopy 的 Java 桥，桌面端不存在
        build = jclass("android.os.Build")
        model = str(build.MODEL or "").strip()
        brand = str(build.BRAND or "").strip()
    except Exception:  # noqa: BLE001 — 机型只是展示信息，拿不到不能影响登录
        return ""
    if not model:
        return ""
    if brand and brand.lower() not in model.lower():
        return f"{brand} {model}"
    return model


def _platform_tag() -> str:
    """上报给账号服务的平台：Android 上 ``sys.platform`` 是 "linux"，会被当成
    Linux 机器，必须显式区分（桌面 Windows/macOS 仍用原值）。"""
    return "android" if _on_android() else sys.platform


def _device_identity() -> tuple[str, str, str]:
    """本实例的登录身份 ``(instance_id, device_name, instance_uuid)``。

    账号侧一律用**实例级**标识（见 ``identity``）：同机多实例各算各的，源码版与
    安装版不会合成一条设备记录。机器级 ``machine_device_id`` 只服务激活码绑定，
    这里不碰。实例标识 = **出生标识**（首次生成随机 uuid 落独立文件，此后
    永不变化，删 account.json/重建登录态都不换身份）。
    """
    if _on_android():
        device_name = _android_device_name() or "Android 设备"
    else:
        device_name = socket.gethostname() or "此电脑"
    stable = identity.stable_instance_id()
    return stable, device_name, stable


class AccountSession:
    """一次客户端登录态：内存持有 access 令牌，凭据库持有 refresh 令牌。"""

    def __init__(self) -> None:
        self.access_token = ""
        self.access_expires_at = ""
        # E2E 密钥：登录派生 / 恢复时从 account.json 加载（见 login/restore）
        self.e2e_key: bytes | None = None
        # 刷新单飞：服务端 refresh 会轮换 access/refresh 两枚令牌，同一进程里两个
        # 线程同时刷新会互相把对方的令牌顶掉（2026-11-11 事件）。同一会话的刷新
        # 必须串行，见 refresh_token_for。
        self._refresh_lock = threading.RLock()

    # ─── 登录态生命周期 ─────────────────────────────────

    def login(self, login_name: str, password: str, *, fetch_summary: bool = True) -> dict:
        """账密登录；成功后落凭据库与 account.json。

        ``fetch_summary=False`` 时**不再多打一次 GET /api/summary**——登录响应
        本身就带 login / display_name / account_id / instance_id，够界面显示
        "已登录 X"。手机端登录用的是这条路：每省一跳就少一次 TLS 握手，而握手
        恰恰是弱网下最贵的一步。设备列表这类只能由 summary 提供的字段，等用户
        真正打开账户详情时再取。
        """
        if not login_name.strip():
            raise AccountError("请输入账户名")
        if not password:
            raise AccountError("请输入密码")
        instance, device_name, instance_uuid = _device_identity()
        result = _request("POST", "/api/login", {
            "login": login_name.strip(), "password": password,
            "instance_id": instance, "device_name": device_name,
            "platform": _platform_tag(),
        }, timeout=_AUTH_TIMEOUT_S)
        _store_refresh_token(result["refresh_token"])
        # E2E 密钥在「握有明文口令的瞬间」派生并常驻内存；hex 缓存进
        # account.json 供 restore 免重派生（本地盘本就是明文，不降安全性）
        from . import e2e
        # 局部别名给闭包用：闭包里读 ``self.e2e_key`` 的类型是 ``bytes | None``
        # （赋值处的收窄不跨闭包），派生结果本身一定是 bytes。
        key = e2e.derive_key(password, result["account_id"])
        self.e2e_key = key
        e2e.set_key(key)
        # 同账号重新登录（掉线/登出后再登）必须**保留**同步水位等运行态——
        # 整写 account.json 会抹掉 profile_sync/users_sync，下一轮同步退回
        # bootstrap，把云端旧快照静默盖到本地新数据上（2026-11-04 事故根源）。
        # 只有切换到不同账号时才换一份干净的运行态。
        def _merge_login(profile: dict) -> None:
            if profile.get("account_id") != result["account_id"]:
                profile.clear()
            profile.update({
                "login": result["login"], "account_id": result["account_id"],
                "display_name": result["display_name"],
                "instance_id": result["instance_id"],
                "device_name": device_name, "instance_uuid": instance_uuid,
            })
            e2e.persist_key_into(key, profile)

        mutate_profile(_merge_login)
        self.access_token = result["access_token"]
        self.access_expires_at = result["access_expires_at"]
        if not fetch_summary:
            return {"account": {"login": result["login"],
                               "display_name": result["display_name"] or ""},
                    "account_id": result["account_id"],
                    "instance_id": result["instance_id"]}
        return self.summary()

    def has_stored_session(self) -> bool:
        """凭据库里是否存在登录态（只查本地，不联网）。

        凭据库不可用时按「没有登录态」处理，避免启动期在 UI 线程抛出；
        真正的错误会在用户点登录时由 ``_store_refresh_token`` 明确报出。
        """
        try:
            return bool(_stored_refresh_token())
        except AccountError as error:
            logger.warning("凭据库不可用，按未登录处理：{}", error)
            return False

    def restore(self, *, fetch_summary: bool = True) -> dict | None:
        """用凭据库里的 refresh 令牌恢复登录态；没有或**服务端明确拒绝**返回 None。

        网络失败 / 5xx / 网关拦截会原样抛出，绝不清本地令牌——那些只说明
        "这次问不到"，不代表登录态失效。``fetch_summary=False`` 时只换令牌、
        不取账号详情（同步路径用得上，少一次 TLS 握手）。
        """
        token = _stored_refresh_token()
        if not token:
            return None
        with self._refresh_lock:
            try:
                result = _request("POST", "/api/refresh", {"refresh_token": token},
                                  timeout=_AUTH_TIMEOUT_S)
            except AccountError as error:
                if error.code in AUTH_REJECTED_CODES:
                    logger.warning("登录态已被服务端拒绝（{}），清除本地令牌", error.code)
                    _clear_stored_session()
                    return None
                raise
            _store_refresh_token(result["refresh_token"])
            self.access_token = result["access_token"]
            self.access_expires_at = result["access_expires_at"]
        # restore 不经口令：E2E 密钥从 account.json 缓存加载
        from . import e2e
        if self.e2e_key is None:
            self.e2e_key = e2e.load_stored_key()
            e2e.set_key(self.e2e_key)
        # 不取详情时也要返回一个真值，调用方靠"返回 None"判断失败
        return self.summary() if fetch_summary else {"ok": True}

    def refresh_token_for(self, stale: str) -> str:
        """把调用方手上的 ``stale`` 换成一枚当前有效的 access 令牌。

        供同步通道用（它们只拿到一个字符串令牌，不是本会话的引用）：

        - 内存里的令牌已经不是 ``stale`` → 说明别处刚刷新过，直接用，不再打
          ``/api/refresh``（refresh 会轮换两枚令牌，多刷一次就把别人的顶掉）；
        - 否则单飞刷新一次；被服务端明确拒绝（``restore`` 返回 None）→ 返回空串，
          由调用方给出「需要重新登录」。
        """
        with self._refresh_lock:
            if self.access_token and self.access_token != stale:
                return self.access_token
            if self.restore(fetch_summary=False) is None:
                return ""
            return self.access_token

    def logout(self) -> None:
        if _stored_refresh_token():
            try:
                _request("POST", "/api/logout", {"refresh_token": _stored_refresh_token()})
            except AccountError as error:
                logger.info("登出请求未完成（{}）；本地登录态照常清除", error)
        _clear_stored_session()
        self.access_token = ""

    def logout_all(self) -> dict:
        result = self._authorized("POST", "/api/logout_all", {})
        _clear_stored_session()
        self.access_token = ""
        return result

    # ─── 已登录后的操作 ─────────────────────────────────

    def summary(self) -> dict:
        return self._authorized("GET", "/api/summary")

    def change_password(self, old_password: str, new_password: str) -> None:
        if len(new_password) < 8:
            raise AccountError("新密码至少 8 位")
        self._authorized("POST", "/api/change_password",
                         {"old_password": old_password, "new_password": new_password})
        # E2E 密钥由口令派生：换口令即换钥。旧密文由下一次同步自动重推——
        # 名册内容签名基于密文（必变）触发上传；notes 文档 sha 同理。
        from . import e2e
        account_id = load_profile().get("account_id") or ""
        self.e2e_key = e2e.derive_key(new_password, str(account_id))
        e2e.set_key(self.e2e_key)
        e2e.persist_key(self.e2e_key)

    def revoke_device(self, device_id: str) -> dict:
        """让某台设备下线（会话鉴权；服务端只允许操作自己账号下的设备）。"""
        if not device_id:
            raise AccountError("缺少设备标识")
        return self._authorized("POST", "/api/devices/revoke",
                                {"instance_id": device_id})

    def sync_state(self) -> dict:
        """云端同步数据概况（各通道计数与是否为空），初始同步前判定用。"""
        return self._authorized("POST", "/api/sync/state", {})

    def clear_sync(self, password: str) -> dict:
        """清空云端同步数据（验密码）；本地水位由调用方重置。"""
        if not password:
            raise AccountError("请输入密码")
        return self._authorized("POST", "/api/sync/clear", {"password": password})

    def _authorized(self, method: str, route: str, data: dict | None = None) -> dict:
        stale = self.access_token
        if not stale:
            raise AccountError("尚未登录")
        try:
            return _request(method, route, data, token=stale)
        except AccountError as error:
            # ``unauthorized`` 和 ``token_expired`` 都是「这个 access 令牌不再
            # 被会话认」，必须一样滑动重试：refresh 会轮换 access，同一会话在
            # 别的进程/实例里刷新过之后，本进程手上的旧令牌就查不到行。
            if error.code not in ("token_expired", "unauthorized"):
                raise
        # 滑动一次再试；刷新走单飞（refresh_token_for），与同步通道共用同一把锁，
        # 避免两个调用方同时 refresh 把对方的令牌顶掉。
        fresh = self.refresh_token_for(stale)
        if not fresh:
            raise AccountError("登录已过期，请重新登录", code="token_expired")
        return _request(method, route, data, token=fresh)

    def heartbeat(self) -> dict:
        """在线心跳：刷新设备与会话活跃时间（设备列表「在线」的数据源）。

        设备被踢或登录态彻底失效时抛 AccountError——调用方据此感知会话
        健康状况，无需等下一次业务请求才发现。
        """
        return self._authorized("POST", "/api/heartbeat", {})

# ─── 进程内唯一会话 ────────────────────────────────────────
#
# 服务端 ``/api/refresh`` 是「单行会话 + 轮换令牌」：每刷新一次就改写
# access_hash 与 refresh_hash，别人手里的旧 access 令牌立刻变成孤儿。
# 所以一个进程里**只能有一个** AccountSession 持有者；各 UI 模块各自 new
# 一个就等于互相顶掉令牌（2026-11-11：页面结构上传下载与账号同步同时持有，
# 同步侧开始成批报「未登录或会话已失效」）。

_session_lock = threading.RLock()
_session: "AccountSession | None" = None


def get_session() -> AccountSession:
    """取进程内唯一的登录态对象。

    所有需要账号能力的地方（账号对话框、页面结构上传下载、手机端账户桥接）
    都必须经这里拿会话，不要再各自 ``AccountSession()``。
    """
    global _session
    with _session_lock:
        if _session is None:
            _session = AccountSession()
        return _session


def reset_session() -> None:
    """丢弃进程内会话（测试用：换账号或需要干净令牌时）。"""
    global _session
    with _session_lock:
        _session = None
