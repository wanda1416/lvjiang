"""账号服务客户端 —— 账密登录、会话刷新、账号信息与自助改密。

登录态拆两半存放：refresh 令牌只进系统凭据库（明文文件不放），账号名与设备
标识进 ``config/session/account.json``（运行数据，机器级归属，不是凭据）。
access 令牌只在内存，30 天过期由 refresh 轮换，用户不感知。
"""
from __future__ import annotations

import json
import socket
import sys
import time
from http.client import HTTPException
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

from loguru import logger

from ...constants import SESSION_CONFIG_DIR
from ..json_files import formatted_json
from . import identity

try:
    import keyring  # 桌面端系统凭据库；APK 未打包（Android 走沙箱文件存储）
except ImportError:                    # pragma: no cover - 仅 Android 环境出现
    keyring = None                     # type: ignore[assignment]


def _on_android() -> bool:
    return hasattr(sys, "getandroidapilevel")


def _android_token_path() -> Path:
    """Android 的 refresh 令牌落点：应用私有 session 目录（沙箱隔离，
    其他应用不可读——这是 Android 的安全边界，等价于桌面系统凭据库）。"""
    return SESSION_CONFIG_DIR / "account_token"


class _AndroidTokenStore:
    """极简 get/set/delete 接口，与 keyring 后端同形。"""

    def get_password(self, service: str, username: str) -> str | None:
        try:
            data = json.loads(_android_token_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(data, dict) and data.get("user") == username:
            value = data.get("token")
            return value if isinstance(value, str) else None
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        _android_token_path().parent.mkdir(parents=True, exist_ok=True)
        _android_token_path().write_text(
            json.dumps({"user": username, "token": password}, ensure_ascii=False),
            encoding="utf-8")

    def delete_password(self, service: str, username: str) -> None:
        try:
            _android_token_path().unlink()
        except OSError:
            pass

    def stored_users(self) -> set[str]:
        """文件里已经存了哪些实例键（诊断"登录态被实例变更孤立"用）。"""
        try:
            data = json.loads(_android_token_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        user = data.get("user") if isinstance(data, dict) else None
        return {user} if isinstance(user, str) and user else set()

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
    "device_revoked": "设备已被移出，请联系管理员",
    "token_expired": "登录已过期，请重新登录",
    "invalid_request": "请求无效",
    "unauthorized": "未登录或会话已失效",
    "not_found": "服务接口不存在",
    "service_unavailable": "账号服务暂时不可用，请稍后再试",
}

# 服务端**明确**判定"这个凭据/会话不行"时才允许清理本地登录态。网络失败、
# 5xx、网关拦截都不在此列——它们只说明"现在问不到"，不能据此把用户踢下线。
AUTH_REJECTED_CODES = frozenset({
    "invalid_credentials", "token_expired", "account_disabled", "device_revoked",
})


class AccountError(Exception):
    """账号服务操作失败；message 是可直接展示的中文。"""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def account_config_path() -> Path:
    """账号运行态落 config/session（运行数据语义）。"""
    return SESSION_CONFIG_DIR / "account.json"


def load_profile() -> dict:
    path = account_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_profile(profile: dict) -> None:
    path = account_config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(formatted_json(profile), encoding="utf-8")
    except OSError as error:
        logger.warning("账号信息保存失败：{}（登录态仍保留在凭据库）", error)


def current_instance_id() -> str:
    """本实例标识（出生标识）；回声护栏比对 op 的写入方时使用。"""
    return identity.stable_instance_id()


def profile_sync_state() -> dict:
    """profile 账本同步状态（落 account.json）：

    - ``remote_history_id``：已应用的**远端**水位（远端自增 id，与本地
      ``profile_history.id`` 是两套编号，绝不互认）
    - ``push_local_id``：已上传的**本地** history.id 水位
    - ``profile_snapshot_id``：``{username: 远端快照 id}``，本设备落地/刷新时绑定
    """
    data = load_profile().get("profile_sync")
    return dict(data) if isinstance(data, dict) else {}


def save_profile_sync_state(state: dict) -> None:
    profile = load_profile()
    profile["profile_sync"] = state
    _save_profile(profile)


def _credentials():
    """凭据后端。桌面端用系统凭据库（不接受明文文件后端）；Android 用
    应用私有沙箱文件——进程隔离由 OS 保证，等价于桌面的凭据库边界。"""
    if _on_android() or keyring is None:
        return _AndroidTokenStore()
    backend = keyring.get_keyring()
    allowed = {
        "keyring.backends.Windows", "keyring.backends.macOS",
        "keyring.backends.SecretService", "keyring.backends.kwallet",
        "keyring.backends.libsecret",
    }
    if type(backend).__module__ == "keyring.backends.chainer":
        backend = next((candidate for candidate in backend.backends
                        if type(candidate).__module__ in allowed), None)
    if backend is None or type(backend).__module__ not in allowed:
        raise AccountError("系统凭据库不可用，无法保存登录态")
    return backend


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
    user = _keyring_user()
    if not user:
        return ""
    try:
        return _credentials().get_password(_KEYRING_SERVICE, user) or ""
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(f"无法读取系统凭据库：{error}") from None


def _store_refresh_token(token: str) -> None:
    user = _keyring_user()
    if not user:
        if token:
            raise AccountError("缺少实例标识，无法保存登录态")
        return
    try:
        backend = _credentials()
        if token:
            backend.set_password(_KEYRING_SERVICE, user, token)
        elif backend.get_password(_KEYRING_SERVICE, user):
            backend.delete_password(_KEYRING_SERVICE, user)
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(f"无法写入系统凭据库：{error}") from None


def _clear_stored_session() -> None:
    _store_refresh_token("")


def stored_token_owner() -> str:
    """凭据库里现存令牌归属的实例键；没有令牌返回空串。

    用途：区分「从没登录」与「登录态还在，但当前实例标识变了、匹配不上」——
    后者要明确告诉用户重新登录，而不是静默显示成未登录。
    """
    try:
        backend = _credentials()
    except AccountError:
        return ""
    owners = getattr(backend, "stored_users", None)
    if not callable(owners):
        return ""
    try:
        return next(iter(owners()), "")
    except Exception:                      # noqa: BLE001 — 诊断信息，取不到就算了
        return ""


def current_keyring_user() -> str:
    """当前实例在凭据库里的键（``session:<实例标识>``），供日志与诊断使用。"""
    return _keyring_user()


def _network_hint(error: BaseException) -> str:
    """把底层网络异常翻成用户能照着做的中文（原文只进日志）。"""
    text = str(error)
    if "handshake" in text.lower():
        return "和账号服务握手超时"
    if "Network is unreachable" in text or "Errno 101" in text:
        return "手机当前网络连不到账号服务"
    if "timed out" in text.lower() or isinstance(error, TimeoutError):
        return "连接账号服务超时"
    if "Name or service not known" in text or "getaddrinfo" in text:
        return "域名解析失败"
    return "连不上账号服务"


def _open_once(request: urlrequest.Request, timeout: float | None = None) -> bytes:
    """发一次请求；网络类异常原样抛出，交给调用方决定是否重试。"""
    with urlrequest.build_opener().open(
            request, timeout=timeout or _REQUEST_TIMEOUT_S) as response:
        return response.read(1_000_001)


def _http_error(exc: urlerror.HTTPError) -> AccountError:
    """把 HTTP 错误分成三类，**只有第一类允许清本地登录态**。

    1. 认证被拒：响应体里带我们 Worker 的显式错误码（``invalid_credentials`` /
       ``token_expired`` / ``account_disabled`` / ``device_revoked``）；
    2. 服务不可用：5xx（Cloudflare 502 之类）、非 JSON 响应；
    3. 其它请求错误：网关/防火墙拦截（典型：Cloudflare Bot 防护返回 403 HTML）。

    第 2、3 类都是"现在拿不到答案"，绝不能当成"你密码错了"——否则一次网关
    抖动就会把用户踢下线。
    """
    code = ""
    try:
        details = json.loads(exc.read(65_536).decode("utf-8"))
        if isinstance(details, dict) and isinstance(details.get("error"), str):
            code = details["error"]
    except (ValueError, UnicodeDecodeError, OSError):
        pass
    if exc.code >= 500:
        return AccountError(ERROR_MESSAGES["service_unavailable"],
                            code=code or "service_unavailable")
    if code in AUTH_REJECTED_CODES:
        return AccountError(ERROR_MESSAGES.get(code, f"账号服务请求失败（HTTP {exc.code}）"),
                            code=code)
    if code:
        return AccountError(ERROR_MESSAGES.get(code, f"账号服务请求失败（HTTP {exc.code}）"),
                            code=code)
    return AccountError(f"账号服务拒绝了请求（HTTP {exc.code}），请稍后重试",
                        code="rejected")


def _request(method: str, route: str, data: dict | None = None,
             token: str = "", timeout: float | None = None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               # 自定义 UA：urllib 默认的 Python-urllib 会被 Cloudflare Bot 防护
               # 在边缘直接 403（请求到不了 Worker），网页与内容服务客户端同理。
               "User-Agent": "lvjiang-account-client"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urlrequest.Request(
        ACCOUNT_SERVICE + route,
        data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers=headers, method=method)
    # 网络类失败重试一次：手机（尤其 IPv6-only 的 5G）握手超时、短暂不可达很常见，
    # 一次失败就报错会让用户看到"什么都没发生"。服务端返回的错误不重试。
    errors: list[Exception] = []
    for attempt in range(2):
        try:
            raw = _open_once(request, timeout)
            break
        except urlerror.HTTPError as exc:
            raise _http_error(exc) from None
        except (urlerror.URLError, TimeoutError, OSError, HTTPException) as exc:
            errors.append(exc)
            if attempt == 0:
                logger.warning("账号服务连接失败（重试一次）：{}", exc)
                time.sleep(0.8)
    else:                                        # 两次都是网络类失败
        logger.warning("账号服务连接失败（重试后仍失败）：{}", errors[-1])
        raise AccountError(f"{_network_hint(errors[-1])}，请检查网络后重试",
                           code="network") from None
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AccountError("账号服务响应格式无效", code="service_unavailable") from None
    if not isinstance(result, dict):
        raise AccountError("账号服务响应格式无效", code="service_unavailable")
    return result


def _android_device_name() -> str:
    """Android 上的机型名（``Build.MODEL``，例如 V2415A）。

    ``socket.gethostname()`` 在 Android 恒为 "localhost"，设备列表里全是
    localhost 没法分辨是哪台手机，所以这里读系统机型；品牌名已经在机型里
    （如 "vivo V2415A"）就不重复拼。取不到时返回空串，由调用方兜底。
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
        _save_profile({
            "login": result["login"], "account_id": result["account_id"],
            "display_name": result["display_name"], "instance_id": result["instance_id"],
            "device_name": device_name, "instance_uuid": instance_uuid,
        })
        self.access_token = result["access_token"]
        self.access_expires_at = result["access_expires_at"]
        if not fetch_summary:
            return {"account": {"login": result["login"],
                               "display_name": result["display_name"] or ""},
                    "account_id": result["account_id"],
                    "instance_id": result["instance_id"]}
        return self.summary()

    def has_stored_session(self) -> bool:
        """凭据库里是否存在登录态（只查本地，不联网）。"""
        return bool(_stored_refresh_token())

    def restore(self, *, fetch_summary: bool = True) -> dict | None:
        """用凭据库里的 refresh 令牌恢复登录态；没有或**服务端明确拒绝**返回 None。

        网络失败 / 5xx / 网关拦截会原样抛出，绝不清本地令牌——那些只说明
        "这次问不到"，不代表登录态失效。``fetch_summary=False`` 时只换令牌、
        不取账号详情（同步路径用得上，少一次 TLS 握手）。
        """
        token = _stored_refresh_token()
        if not token:
            return None
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
        # 不取详情时也要返回一个真值，调用方靠"返回 None"判断失败
        return self.summary() if fetch_summary else {"ok": True}

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

    def revoke_device(self, device_id: str) -> dict:
        """让某台设备下线（会话鉴权；服务端只允许操作自己账号下的设备）。"""
        if not device_id:
            raise AccountError("缺少设备标识")
        return self._authorized("POST", "/api/devices/revoke",
                                {"instance_id": device_id})

    def _authorized(self, method: str, route: str, data: dict | None = None) -> dict:
        if not self.access_token:
            raise AccountError("尚未登录")
        try:
            return _request(method, route, data, token=self.access_token)
        except AccountError as error:
            # ``unauthorized`` 和 ``token_expired`` 都是「这个 access 令牌不再
            # 被会话认」，必须一样滑动重试：refresh 会轮换 access，同一会话在
            # 别的进程/实例里刷新过之后，本进程手上的旧令牌就查不到行。
            if error.code not in ("token_expired", "unauthorized"):
                raise
        # 滑动一次再试；refresh 也失效则要求重新登录。
        if self.restore() is None:
            raise AccountError("登录已过期，请重新登录", code="token_expired")
        return _request(method, route, data, token=self.access_token)
