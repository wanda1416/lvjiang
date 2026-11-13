"""Android 账户桥接：登录态走进程内内存快照。

回归点：早期每次调用都新建 ``AccountSession`` 并 ``restore()``，即
「进一次设置页 → refresh + summary 两个网络请求」。这里锁住三件事：
重复取状态只探测一次、登录后缓存直接就是已登录、登出后缓存翻成未登录。
"""

from __future__ import annotations

import json

import pytest

from lvjiang.core.account.service import AccountError
from lvjiang.core.ondevice import account_api


@pytest.fixture(autouse=True)
def reset_module_state(monkeypatch):
    # 会话已收敛到进程内单例（core.account.service.get_session），这里改成重置它；
    # 手机端桥接不再自己持有 _SESSION。
    from lvjiang.core.account import service

    service.reset_session()
    monkeypatch.setattr(account_api, "_STATUS_CACHE", None)
    yield
    service.reset_session()


def test_status_probes_only_once_per_process(monkeypatch) -> None:
    calls: list[int] = []

    def probe() -> dict:
        calls.append(1)
        return {"ok": True, "logged_in": True, "login": "wang", "display_name": "王"}

    monkeypatch.setattr(account_api, "_probe_status", probe)

    first = json.loads(account_api.account_status())
    second = json.loads(account_api.account_status())

    assert first == second == {"ok": True, "logged_in": True,
                               "login": "wang", "display_name": "王"}
    assert len(calls) == 1


def test_probe_prefers_memory_session_over_network(monkeypatch) -> None:
    """本进程已登录时，状态来自 account.json，不再读凭据库、更不联网。"""
    session = account_api._session()
    session.access_token = "access"
    monkeypatch.setattr(account_api, "load_profile",
                        lambda: {"login": "wang", "display_name": "王"})
    monkeypatch.setattr(session, "has_stored_session",
                        lambda: pytest.fail("已登录时不应再读凭据库"))

    status = account_api._probe_status()

    assert status["logged_in"] is True
    assert status["login"] == "wang"


def test_profile_answers_without_network(monkeypatch) -> None:
    """有 refresh 令牌 + account.json 里的账号名 → 直接已登录，不打网络。"""
    session = account_api._session()
    monkeypatch.setattr(session, "has_stored_session", lambda: True)
    monkeypatch.setattr(account_api, "load_profile",
                        lambda: {"login": "wang", "display_name": "王"})
    monkeypatch.setattr(session, "restore",
                        lambda: pytest.fail("有账号名时不该联网"))

    assert account_api._probe_status() == {
        "ok": True, "logged_in": True, "login": "wang", "display_name": "王"}


def test_stored_token_stays_logged_in_when_network_fails(monkeypatch) -> None:
    """网络失败不能把已登录翻成未登录——这正是设置页一会儿「未登录」
    一会儿「已登录」来回跳的原因。"""
    session = account_api._session()
    session.access_token = ""
    monkeypatch.setattr(session, "has_stored_session", lambda: True)
    monkeypatch.setattr(account_api, "load_profile", lambda: {})

    def offline():
        raise AccountError("和账号服务握手超时，请检查网络后重试", code="network")

    monkeypatch.setattr(session, "restore", offline)

    status = account_api._probe_status()

    assert status["logged_in"] is True
    assert status["login"] == ""


def test_login_warms_cache_without_extra_probe(monkeypatch) -> None:
    monkeypatch.setattr(account_api, "_probe_status",
                        lambda: pytest.fail("登录后不该再探测"))
    session = account_api._session()
    monkeypatch.setattr(session, "login", lambda login, password, **_kwargs: {
        "account": {"login": login, "display_name": "王"},
        "access_token": "access", "access_expires_at": "later"})

    result = json.loads(account_api.account_login("wang", "pw"))
    status = json.loads(account_api.account_status())

    assert result["ok"] is True
    assert status == {"ok": True, "logged_in": True, "online": True,
                      "login": "wang", "display_name": "王"}


def test_logout_clears_cached_status(monkeypatch) -> None:
    monkeypatch.setattr(account_api, "_STATUS_CACHE", {
        "ok": True, "logged_in": True, "login": "wang", "display_name": ""})
    session = account_api._session()
    monkeypatch.setattr(session, "logout", lambda: None)

    account_api.account_logout()

    assert json.loads(account_api.account_status())["logged_in"] is False
