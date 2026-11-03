"""账号服务客户端 —— 账密登录、会话刷新、账号信息与自助改密。

登录态拆两半存放：refresh 令牌只进系统凭据库（明文文件不放），账号名与设备
标识进 ``config/session/account.json``（运行数据，机器级归属，不是凭据）。
access 令牌只在内存，30 天过期由 refresh 轮换，用户不感知。
"""
from __future__ import annotations

import json
import socket
import sys
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

ACCOUNT_SERVICE = "https://lvjiang.wyxj.net"
_KEYRING_SERVICE = "lvjiang.account"

_ERROR_MESSAGES = {
    "invalid_credentials": "账户名或密码不正确",
    "account_disabled": "账号已被禁用，请联系管理员",
    "device_revoked": "设备已被移出，请联系管理员",
    "token_expired": "登录已过期，请重新登录",
    "invalid_request": "请求无效",
    "unauthorized": "未登录或会话已失效",
    "not_found": "服务接口不存在",
    "service_unavailable": "账号服务暂时不可用，请稍后再试",
}


class AccountError(Exception):
    """账号服务操作失败；message 是可直接展示的中文。"""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def account_config_path() -> Path:
    """账号运行态落 config/session（运行数据语义）。"""
    return SESSION_CONFIG_DIR / "account.json"


def _load_profile() -> dict:
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
    """本实例标识；未登录过（无实例 uuid）时为空串。"""
    instance_uuid = _instance_uuid()
    return identity.instance_id(instance_uuid) if instance_uuid else ""


def profile_sync_state() -> dict:
    """profile 账本同步状态（落 account.json）：

    - ``remote_history_id``：已应用的**远端**水位（远端自增 id，与本地
      ``profile_history.id`` 是两套编号，绝不互认）
    - ``push_local_id``：已上传的**本地** history.id 水位
    - ``profile_snapshot_id``：``{username: 远端快照 id}``，本设备落地/刷新时绑定
    """
    data = _load_profile().get("profile_sync")
    return dict(data) if isinstance(data, dict) else {}


def save_profile_sync_state(state: dict) -> None:
    profile = _load_profile()
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
    """本实例标识；未登录过时为空（此时也不可能有登录态）。"""
    return str(_load_profile().get("instance_uuid") or "")


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


def _request(method: str, route: str, data: dict | None = None,
             token: str = "") -> dict:
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
    try:
        with urlrequest.build_opener().open(request, timeout=15) as response:
            raw = response.read(1_000_001)
    except urlerror.HTTPError as exc:
        code = ""
        try:
            details = json.loads(exc.read(65_536).decode("utf-8"))
            if isinstance(details, dict) and isinstance(details.get("error"), str):
                code = details["error"]
        except (ValueError, UnicodeDecodeError, OSError):
            pass
        message = _ERROR_MESSAGES.get(code, f"账号服务请求失败（HTTP {exc.code}）")
        raise AccountError(message, code=code) from None
    except (urlerror.URLError, TimeoutError, OSError, HTTPException) as error:
        logger.warning("账号服务连接失败：{}", error)
        raise AccountError(_ERROR_MESSAGES["service_unavailable"]) from None
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise AccountError("账号服务响应格式无效") from None
    if not isinstance(result, dict):
        raise AccountError("账号服务响应格式无效")
    return result


def _device_identity() -> tuple[str, str, str]:
    """本实例的登录身份 ``(instance_id, device_name, instance_uuid)``。

    账号侧一律用**实例级**标识（见 ``identity``）：同机多实例各算各的，源码版与
    安装版不会合成一条设备记录。机器级 ``machine_device_id`` 只服务激活码绑定，
    这里不碰。实例 uuid 缺失时立即生成并落盘，保证随后的凭据库写入有实例维度。
    """
    profile = _load_profile()
    instance_uuid = str(profile.get("instance_uuid") or "")
    if not instance_uuid:
        instance_uuid = identity.new_instance_uuid()
        profile["instance_uuid"] = instance_uuid
        _save_profile(profile)
    device_name = socket.gethostname() or "此电脑"
    return identity.instance_id(instance_uuid), device_name, instance_uuid


class AccountSession:
    """一次客户端登录态：内存持有 access 令牌，凭据库持有 refresh 令牌。"""

    def __init__(self) -> None:
        self.access_token = ""
        self.access_expires_at = ""

    # ─── 登录态生命周期 ─────────────────────────────────

    def login(self, login_name: str, password: str) -> dict:
        """账密登录；成功后刷新本地 profile 与凭据库，返回 summary 数据。"""
        if not login_name.strip():
            raise AccountError("请输入账户名")
        if not password:
            raise AccountError("请输入密码")
        instance, device_name, instance_uuid = _device_identity()
        result = _request("POST", "/api/login", {
            "login": login_name.strip(), "password": password,
            "instance_id": instance, "device_name": device_name,
            "platform": sys.platform,
        })
        _store_refresh_token(result["refresh_token"])
        _save_profile({
            "login": result["login"], "account_id": result["account_id"],
            "display_name": result["display_name"], "instance_id": result["instance_id"],
            "device_name": device_name, "instance_uuid": instance_uuid,
        })
        self.access_token = result["access_token"]
        self.access_expires_at = result["access_expires_at"]
        return self.summary()

    def has_stored_session(self) -> bool:
        """凭据库里是否存在登录态（只查本地，不联网）。"""
        return bool(_stored_refresh_token())

    def restore(self) -> dict | None:
        """用凭据库里的 refresh 令牌恢复登录态；没有或已失效返回 None。"""
        token = _stored_refresh_token()
        if not token:
            return None
        try:
            result = _request("POST", "/api/refresh", {"refresh_token": token})
        except AccountError as error:
            if error.code in ("invalid_credentials", "token_expired", "account_disabled",
                              "device_revoked"):
                _clear_stored_session()
                return None
            raise
        _store_refresh_token(result["refresh_token"])
        self.access_token = result["access_token"]
        self.access_expires_at = result["access_expires_at"]
        return self.summary()

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
