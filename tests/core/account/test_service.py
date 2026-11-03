"""账号客户端服务层：凭据只进凭据库、profile 不含令牌、过期自动滑动。"""

from __future__ import annotations

import json

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

    def fake_request(method, route, data=None, token=""):
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


def test_distinct_instances_get_distinct_device_ids_and_tokens(tmp_path, monkeypatch) -> None:
    """同机多实例（源码版 / 安装版）必须各算各的设备身份，登录态互不顶替。"""
    keyring = FakeKeyring()
    monkeypatch.setattr(account_service, "_credentials", lambda: keyring)
    monkeypatch.setattr(account_identity, "_machine_component", lambda: "test:fixed")

    def login_with(config_name: str) -> tuple[str, str]:
        path = tmp_path / config_name / "account.json"
        monkeypatch.setattr(account_service, "account_config_path", lambda p=path: p)
        monkeypatch.setattr(account_service, "_request", lambda method, route, data=None, token="": {
            "account_id": "a" * 32, "login": data["login"], "display_name": "",
            "instance_id": data["instance_id"], "access_token": "access",
            "access_expires_at": "later", "refresh_token": f"refresh-{config_name}"}
        if route == "/api/login" else {"account": {"login": "wang", "display_name": ""},
                                       "devices": [], "active_sessions": 1})
        AccountSession().login("wang", "pw")
        profile = json.loads(path.read_text(encoding="utf-8"))
        return profile["instance_id"], profile["instance_uuid"]

    source_id, source_uuid = login_with("source")
    install_id, install_uuid = login_with("install")
    assert source_id != install_id
    assert source_uuid != install_uuid
    assert len(keyring.store) == 2          # 两条登录态并存，互不覆盖
