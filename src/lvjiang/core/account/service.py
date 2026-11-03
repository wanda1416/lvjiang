"""账号服务客户端 —— 账密登录、会话刷新、账号信息与自助改密。

登录态拆两半存放：refresh 令牌只进系统凭据库（明文文件不放），账号名与设备
标识进 ``config/session/account.json``（运行数据，机器级归属，不是凭据）。
access 令牌只在内存，30 天过期由 refresh 轮换，用户不感知。
"""
from __future__ import annotations

import json
import socket
import sys
import uuid
from http.client import HTTPException
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

import keyring
from loguru import logger

from ...constants import SESSION_CONFIG_DIR
from ..json_files import formatted_json

ACCOUNT_SERVICE = "https://lvjiang.wyxj.net"
_KEYRING_SERVICE = "lvjiang.account"
_KEYRING_USER = "session"

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


def _credentials():
    """系统凭据库后端；不接受明文文件后端，令牌落明文等于没做。"""
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


def _stored_refresh_token() -> str:
    try:
        return _credentials().get_password(_KEYRING_SERVICE, _KEYRING_USER) or ""
    except AccountError:
        raise
    except Exception as error:
        raise AccountError(f"无法读取系统凭据库：{error}") from None


def _store_refresh_token(token: str) -> None:
    try:
        backend = _credentials()
        if token:
            backend.set_password(_KEYRING_SERVICE, _KEYRING_USER, token)
        elif backend.get_password(_KEYRING_SERVICE, _KEYRING_USER):
            backend.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
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


def _device_identity() -> tuple[str, str]:
    """本机稳定设备标识与展示名；设备标识生成一次后随 account.json 固定。"""
    profile = _load_profile()
    device_id = str(profile.get("device_id") or "")
    if not device_id:
        device_id = uuid.uuid4().hex
    device_name = socket.gethostname() or "此电脑"
    return device_id, device_name


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
        device_id, device_name = _device_identity()
        result = _request("POST", "/api/login", {
            "login": login_name.strip(), "password": password,
            "device_id": device_id, "device_name": device_name,
            "platform": sys.platform,
        })
        _store_refresh_token(result["refresh_token"])
        _save_profile({
            "login": result["login"], "account_id": result["account_id"],
            "display_name": result["display_name"], "device_id": result["device_id"],
            "device_name": device_name,
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

    def _authorized(self, method: str, route: str, data: dict | None = None) -> dict:
        if not self.access_token:
            raise AccountError("尚未登录")
        try:
            return _request(method, route, data, token=self.access_token)
        except AccountError as error:
            if error.code != "token_expired":
                raise
        # access 令牌到期：滑动一次再试；refresh 也失效则要求重新登录。
        if self.restore() is None:
            raise AccountError("登录已过期，请重新登录", code="token_expired")
        return _request(method, route, data, token=self.access_token)
