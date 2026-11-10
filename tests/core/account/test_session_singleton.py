"""进程内唯一会话：令牌刷新单飞，多持有者不再互相顶掉。

事故（2026-11-11）：服务端 ``/api/refresh`` 是「单行会话 + 轮换 access/refresh
两枚令牌」。页面结构上传下载与账号同步各自 new 了一个 AccountSession，谁先刷新
谁就把对方的 access 令牌变成孤儿，同步侧随即成批报「未登录或会话已失效」，直到
十分钟后的心跳把它刷回来。
"""
from __future__ import annotations

import pytest

from lvjiang.core.account import service


@pytest.fixture(autouse=True)
def _clean_singleton():
    service.reset_session()
    yield
    service.reset_session()


def test_get_session_is_process_wide():
    session = service.get_session()
    assert session is service.get_session()

    service.reset_session()
    assert service.get_session() is not session


def test_android_account_bridge_shares_the_same_session():
    """手机端账户桥接不得自己 new 会话。"""
    from lvjiang.core.ondevice import account_api

    assert account_api._session() is service.get_session()


def test_refresh_token_for_reuses_token_refreshed_elsewhere(monkeypatch):
    """别处刚刷新过（内存令牌已换）：直接复用，不再打 /api/refresh。"""
    session = service.AccountSession()
    session.access_token = "fresh"
    requests = []
    monkeypatch.setattr(service, "_request", lambda *a, **k: requests.append(a) or {})

    assert session.refresh_token_for("stale") == "fresh"
    assert requests == []


def test_refresh_token_for_refreshes_once_per_stale_token(monkeypatch):
    session = service.AccountSession()
    routes = []

    def fake_request(method, route, data, timeout=None):
        routes.append(route)
        return {"access_token": "fresh", "refresh_token": "r2",
                "access_expires_at": "later"}

    monkeypatch.setattr(service, "_request", fake_request)
    monkeypatch.setattr(service, "_stored_refresh_token", lambda: "r1")
    monkeypatch.setattr(service, "_store_refresh_token", lambda token: None)
    monkeypatch.setattr("lvjiang.core.account.e2e.load_stored_key", lambda: None)
    monkeypatch.setattr("lvjiang.core.account.e2e.set_key", lambda key: None)

    assert session.refresh_token_for("stale") == "fresh"
    assert routes == ["/api/refresh"]
    assert session.access_token == "fresh"

    # 同一枚 stale 再来：内存里已是 fresh，直接复用，不重复刷新（否则又把别人顶掉）
    assert session.refresh_token_for("stale") == "fresh"
    assert routes == ["/api/refresh"]


def test_refresh_token_for_returns_empty_when_server_rejects(monkeypatch):
    """服务端明确拒绝＝需要重新登录：返回空串，并清掉本地令牌。"""
    session = service.AccountSession()
    cleared = []

    def reject(*args, **kwargs):
        raise service.AccountError("登录已过期，请重新登录", code="token_expired")

    monkeypatch.setattr(service, "_request", reject)
    monkeypatch.setattr(service, "_stored_refresh_token", lambda: "r1")
    monkeypatch.setattr(service, "_clear_stored_session", lambda: cleared.append(True))

    assert session.refresh_token_for("stale") == ""
    assert cleared == [True]
