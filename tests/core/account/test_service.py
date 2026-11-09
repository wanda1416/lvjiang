"""账号客户端服务层：凭据只进凭据库、profile 不含令牌、过期自动滑动。"""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from lvjiang.core.account import identity as account_identity
from lvjiang.core.account import service as account_service
from lvjiang.core.account.service import AccountError, AccountSession


class FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


def _stored_refresh(keyring: "FakeKeyring"):
    """凭据库里的 refresh 令牌；键带实例维度，值唯一即当前实例那条。"""
    values = [value for (service, _user), value in keyring.store.items()
              if service == "lvjiang.account"]
    return values[0] if values else None


@pytest.fixture()
def fake_env(tmp_path, monkeypatch):
    keyring = FakeKeyring()
    monkeypatch.setattr(account_service, "_credentials", lambda: keyring)
    monkeypatch.setattr(account_service, "account_config_path", lambda: tmp_path / "account.json")
    # 机器指纹在测试里固定，避免依赖真实硬件；实例维度仍由 account.json 决定。
    monkeypatch.setattr(account_identity, "_machine_component", lambda: "test:fixed")
    requests: list[tuple[str, str, dict | None]] = []

    def fake_request(method, route, data=None, token="", timeout=None):
        requests.append((method, route, data))
        if route == "/api/login":
            return {"account_id": "a" * 32, "login": data["login"], "display_name": "王某",
                    "instance_id": data["instance_id"], "access_token": "access-1",
                    "access_expires_at": "later", "refresh_token": "refresh-1"}
        if route == "/api/refresh":
            if data["refresh_token"] != _stored_refresh(keyring):
                raise AccountError("登录已过期，请重新登录", code="token_expired")
            return {"access_token": "access-2", "access_expires_at": "later",
                    "refresh_token": "refresh-2"}
        if route == "/api/summary":
            if token != "access-1":
                if token == "access-2":
                    return {"account": {"login": "wang", "display_name": "王某"}, "devices": [],
                            "active_sessions": 1}
                raise AccountError("登录已过期，请重新登录", code="token_expired")
            return {"account": {"login": "wang", "display_name": "王某"}, "devices": [],
                    "active_sessions": 1}
        if route == "/api/change_password":
            if data["new_password"] == "short":
                raise AccountError("请求无效", code="invalid_request")
            return {"changed": True}
        raise AssertionError(route)

    monkeypatch.setattr(account_service, "_request", fake_request)
    return {"keyring": keyring, "requests": requests}


def test_login_stores_refresh_in_keyring_profile_without_tokens(fake_env) -> None:
    session = AccountSession()
    summary = session.login("wang", "pw")
    assert summary["account"]["login"] == "wang"
    assert _stored_refresh(fake_env["keyring"]) == "refresh-1"
    profile = json.loads(account_service.account_config_path().read_text(encoding="utf-8"))
    assert profile["login"] == "wang"
    assert profile["instance_id"]  # 由实例标识 + 机器指纹派生
    assert profile["instance_uuid"]
    assert "refresh" not in json.dumps(profile) and "token" not in json.dumps(profile)


def test_relogin_same_account_preserves_sync_state(fake_env) -> None:
    """事故回归：掉线/登出后重新登录不得抹掉同步水位。

    2026-11-04 事故根源：login() 整写 account.json，profile_sync 被清 →
    下一轮同步退回 bootstrap，云端旧快照静默盖掉本地未推送的更新。
    同账号重登必须保留；换账号才换干净的运行态。
    """
    session = AccountSession()
    session.login("wang", "pw")
    path = account_service.account_config_path()
    account_service.save_profile_sync_state({
        "remote_history_id": 199, "local_history_id": 31120,
        "profile_snapshot_id": 2})
    # 掉线后重新登录同一账号
    session.login("wang", "pw")
    profile = json.loads(path.read_text(encoding="utf-8"))
    assert profile["profile_sync"]["remote_history_id"] == 199
    assert profile["profile_sync"]["local_history_id"] == 31120
    assert profile["profile_sync"]["profile_snapshot_id"] == 2
    # 换账号 → 运行态重置（水位属于旧账号，不能带到新账号）：
    # 把 account.json 的 account_id 改成别的账号再登录即可模拟
    profile["account_id"] = "b" * 32
    path.write_text(json.dumps(profile, ensure_ascii=False), encoding="utf-8")
    session.login("wang", "pw")
    profile = json.loads(path.read_text(encoding="utf-8"))
    assert "profile_sync" not in profile


def test_restore_slides_tokens_and_expired_access_refreshes_once(fake_env) -> None:
    session = AccountSession()
    session.login("wang", "pw")
    restored_session = AccountSession()
    assert restored_session.restore() is not None
    assert restored_session.access_token == "access-2"
    assert _stored_refresh(fake_env["keyring"]) == "refresh-2"
    # access 令牌过期时 _authorized 先滑动再重试
    fresh = AccountSession()
    fresh.login("wang", "pw")
    fake_env["requests"].clear()
    fresh.access_token = "stale"
    assert fresh.summary()["account"]["login"] == "wang"
    assert any(route == "/api/refresh" for _, route, _ in fake_env["requests"])


def test_logout_clears_keyring_even_if_service_unreachable(fake_env, monkeypatch) -> None:
    session = AccountSession()
    session.login("wang", "pw")

    def unreachable(method, route, data=None, token=""):
        raise AccountError("账号服务暂时不可用，请稍后再试")

    monkeypatch.setattr(account_service, "_request", unreachable)
    session.logout()
    assert _stored_refresh(fake_env["keyring"]) in (None, "")
    assert session.access_token == ""


def test_change_password_rejects_short_new_password(fake_env) -> None:
    session = AccountSession()
    session.login("wang", "pw")
    with pytest.raises(AccountError):
        session.change_password("pw", "short")


def test_no_keyring_falls_back_to_android_store(monkeypatch, tmp_path) -> None:
    """APK 环境没有 keyring 包：凭据落到应用私有沙箱文件（等价安全边界）。"""
    monkeypatch.setattr(account_service, "keyring", None)
    monkeypatch.setattr(account_service, "_on_android", lambda: True)
    token_file = tmp_path / "account_token"
    monkeypatch.setattr(account_service, "_android_token_path", lambda: token_file)

    backend = account_service._credentials()
    assert type(backend).__name__ == "_AndroidTokenStore"
    backend.set_password("lvjiang.account", "session:x", "tok-1")
    assert backend.get_password("lvjiang.account", "session:x") == "tok-1"
    assert backend.get_password("lvjiang.account", "session:other") is None
    backend.delete_password("lvjiang.account", "session:x")
    assert backend.get_password("lvjiang.account", "session:x") is None


def test_desktop_without_keyring_refuses_plaintext_fallback(monkeypatch) -> None:
    """桌面端缺 keyring 必须报错，绝不把 refresh 令牌明文写进 config/session。"""
    monkeypatch.setattr(account_service, "keyring", None)
    monkeypatch.setattr(account_service, "_on_android", lambda: False)

    with pytest.raises(AccountError):
        account_service._credentials()

    session = account_service.AccountSession()
    # 启动期不能因为凭据库不可用就抛异常：按未登录处理
    assert session.has_stored_session() is False


def test_stale_access_token_self_heals_via_refresh(fake_env, monkeypatch) -> None:
    """access 被别的实例/进程轮换掉时收到 unauthorized，也应滑动重试而不是直接报错。"""
    session = AccountSession()
    session.login("wang", "pw")
    session.access_token = "rotated-elsewhere"      # 服务端已不认这个令牌

    def fake_request(method, route, data=None, token="", timeout=None):
        if route == "/api/refresh":
            return {"access_token": "access-3", "access_expires_at": "later",
                    "refresh_token": "refresh-3"}
        if token == "access-3":
            return {"account": {"login": "wang", "display_name": ""},
                    "devices": [], "active_sessions": 1}
        raise AccountError("未登录或会话已失效", code="unauthorized")

    monkeypatch.setattr(account_service, "_request", fake_request)
    assert session.summary()["account"]["login"] == "wang"


def test_distinct_instances_get_distinct_device_ids_and_tokens(tmp_path, monkeypatch) -> None:
    """同机多实例（源码版 / 安装版）必须各算各的设备身份，登录态互不顶替。"""
    keyring = FakeKeyring()
    monkeypatch.setattr(account_service, "_credentials", lambda: keyring)
    monkeypatch.setattr(account_identity, "_machine_component", lambda: "test:fixed")

    def login_with(config_name: str) -> tuple[str, str]:
        path = tmp_path / config_name / "account.json"
        marker = tmp_path / config_name / "instance.id"
        monkeypatch.setattr(account_service, "account_config_path", lambda p=path: p)
        # 实例身份 = 出生文件；两个实例各有各的出生文件
        monkeypatch.setattr(account_identity, "_instance_id_path", lambda m=marker: m)
        account_identity._stable_instance_id_cache = None
        monkeypatch.setattr(account_service, "_request",
                            lambda method, route, data=None, token="", timeout=None: {
            "account_id": "a" * 32, "login": data["login"], "display_name": "",
            "instance_id": data["instance_id"], "access_token": "access",
            "access_expires_at": "later", "refresh_token": f"refresh-{config_name}"}
        if route == "/api/login" else {"account": {"login": "wang", "display_name": ""},
                                       "devices": [], "active_sessions": 1})
        AccountSession().login("wang", "pw")
        profile = json.loads(path.read_text(encoding="utf-8"))
        return profile["instance_id"], marker.read_text(encoding="utf-8").strip()

    source_id, source_uuid = login_with("source")
    install_id, install_uuid = login_with("install")
    assert source_id != install_id
    assert source_uuid != install_uuid
    assert len(keyring.store) == 2          # 两条登录态并存，互不覆盖

def test_android_reports_model_name_and_android_platform(monkeypatch) -> None:
    """Android 上 sys.platform 是 linux、gethostname 是 localhost：
    上报必须换成机型 + android，否则设备列表里全是认不出的条目。"""
    monkeypatch.setattr(account_service, "_on_android", lambda: True)
    monkeypatch.setattr(account_service, "_android_device_name", lambda: "Pixel 7")
    monkeypatch.setattr(account_identity, "stable_instance_id", lambda: "inst-1")

    instance, device_name, instance_uuid = account_service._device_identity()

    assert (instance, device_name, instance_uuid) == ("inst-1", "Pixel 7", "inst-1")
    assert account_service._platform_tag() == "android"


def test_desktop_keeps_hostname_and_sys_platform(monkeypatch) -> None:
    monkeypatch.setattr(account_service, "_on_android", lambda: False)
    monkeypatch.setattr(account_service.socket, "gethostname", lambda: "PC-01")
    monkeypatch.setattr(account_service.sys, "platform", "win32")

    assert account_service._device_identity()[1] == "PC-01"
    assert account_service._platform_tag() == "win32"


def test_request_retries_once_after_network_error(monkeypatch) -> None:
    """弱网下一次握手超时不该直接判失败：重试一次成功就正常返回。"""
    calls: list[int] = []

    def flaky(_request, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise URLError(OSError(101, "Network is unreachable"))
        return json.dumps({"ok": True}).encode("utf-8")

    monkeypatch.setattr(account_service, "_open_once", flaky)
    monkeypatch.setattr(account_service.time, "sleep", lambda _seconds: None)

    assert account_service._request("POST", "/api/login", {}) == {"ok": True}
    assert len(calls) == 2


def test_request_network_failure_is_actionable(monkeypatch) -> None:
    """两次都失败时给可行动的中文原因，而不是笼统的"服务暂时不可用"。"""

    def always_fail(_request, timeout=None):
        raise URLError(TimeoutError("The handshake operation timed out"))

    monkeypatch.setattr(account_service, "_open_once", always_fail)
    monkeypatch.setattr(account_service.time, "sleep", lambda _seconds: None)

    with pytest.raises(AccountError) as info:
        account_service._request("POST", "/api/login", {})
    assert info.value.code == "network"
    assert "握手超时" in str(info.value)

def test_network_error_never_clears_stored_token(monkeypatch) -> None:
    """网络失败只说明"现在问不到"，绝不能把本地登录态清掉。"""
    cleared: list[int] = []
    monkeypatch.setattr(account_service, "_stored_refresh_token", lambda: "refresh-token")
    monkeypatch.setattr(account_service, "_clear_stored_session",
                        lambda: cleared.append(1))

    def offline(method, route, data=None, token="", timeout=None):
        raise AccountError("和账号服务握手超时，请检查网络后重试", code="network")

    monkeypatch.setattr(account_service, "_request", offline)
    session = AccountSession()

    with pytest.raises(AccountError):
        session.restore()
    assert cleared == []


def test_auth_rejection_clears_stored_token(monkeypatch) -> None:
    """服务端明确说令牌不行时，才清本地登录态。"""
    cleared: list[int] = []
    monkeypatch.setattr(account_service, "_stored_refresh_token", lambda: "refresh-token")
    monkeypatch.setattr(account_service, "_clear_stored_session",
                        lambda: cleared.append(1))

    def rejected(method, route, data=None, token="", timeout=None):
        raise AccountError("登录已过期，请重新登录", code="token_expired")

    monkeypatch.setattr(account_service, "_request", rejected)

    assert AccountSession().restore() is None
    assert cleared == [1]


def _http_error(status: int, body: bytes) -> AccountError:
    """把 (状态码, 响应体) 走一遍 ``_request`` 的错误映射，返回映射后的异常。"""
    import io
    from urllib import error as urlerror

    try:
        raise urlerror.HTTPError("https://x/api", status, "err", {}, io.BytesIO(body))
    except urlerror.HTTPError as exc:
        return account_service._http_error(exc)


def test_http_5xx_is_service_unavailable_not_auth() -> None:
    """网关 502 / 非 JSON 响应：服务不可用，不能当成认证失败。"""
    error = _http_error(502, b"<html>bad gateway</html>")
    assert error.code == "service_unavailable"
    assert error.code not in account_service.AUTH_REJECTED_CODES


def test_http_403_html_is_rejected_but_not_auth() -> None:
    """Cloudflare Bot 防护那种 403 HTML 不能把用户踢下线。"""
    error = _http_error(403, b"<html>blocked</html>")
    assert error.code == "rejected"
    assert error.code not in account_service.AUTH_REJECTED_CODES


def test_http_401_with_explicit_code_is_auth() -> None:
    error = _http_error(401, b'{"error": "token_expired"}')
    assert error.code == "token_expired"
    assert error.code in account_service.AUTH_REJECTED_CODES

def test_login_fast_path_costs_one_request(monkeypatch) -> None:
    """手机端登录只发一次 POST /api/login：每少一跳就少一次 TLS 握手。"""
    calls: list[str] = []

    def fake_request(method, route, data=None, token="", timeout=None):
        calls.append(route)
        if route == "/api/login":
            return {"login": "wang", "display_name": "王", "account_id": "a" * 32,
                    "instance_id": "inst", "access_token": "at",
                    "access_expires_at": "later", "refresh_token": "rt"}
        return {"account": {"login": "wang", "display_name": "王"}}    # /api/summary

    monkeypatch.setattr(account_service, "_request", fake_request)
    monkeypatch.setattr(account_service, "_store_refresh_token", lambda token: None)
    monkeypatch.setattr(account_service, "_save_profile", lambda profile: None)
    monkeypatch.setattr(account_identity, "stable_instance_id", lambda: "inst")

    summary = AccountSession().login("wang", "pw", fetch_summary=False)

    assert calls == ["/api/login"]
    assert summary["account"]["login"] == "wang"

    calls.clear()
    AccountSession().login("wang", "pw")          # 桌面端仍要设备列表 → 多取一次
    assert calls == ["/api/login", "/api/summary"]


def test_restore_fast_path_returns_truthy_without_summary(monkeypatch) -> None:
    """同步路径只要令牌：restore(fetch_summary=False) 不能再打 summary，
    同时必须返回真值，调用方才不会当成"未登录"。"""
    calls: list[str] = []
    monkeypatch.setattr(account_service, "_stored_refresh_token", lambda: "rt")

    def fake_request(method, route, data=None, token="", timeout=None):
        calls.append(route)
        return {"refresh_token": "rt2", "access_token": "at", "access_expires_at": "later"}

    monkeypatch.setattr(account_service, "_request", fake_request)
    monkeypatch.setattr(account_service, "_store_refresh_token", lambda token: None)

    session = AccountSession()
    assert session.restore(fetch_summary=False)
    assert calls == ["/api/refresh"]
    assert session.access_token == "at"


def test_stored_session_state_distinguishes_unknown(monkeypatch) -> None:
    """凭据库读不到时返回 None，让周期恢复闸门按 fail-closed 处理。"""
    monkeypatch.setattr(account_service, "_stored_refresh_token", lambda: "")
    assert account_service.stored_session_state() is False

    monkeypatch.setattr(account_service, "_stored_refresh_token",
                        lambda: "refresh-x")
    assert account_service.stored_session_state() is True

    def broken() -> str:
        raise AccountError("keyring down")

    monkeypatch.setattr(account_service, "_stored_refresh_token", broken)
    assert account_service.stored_session_state() is None
