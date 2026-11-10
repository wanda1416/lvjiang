"""同步通道遇 401：换一枚令牌重试一次，仍失败才交给界面报错。

事故（2026-11-11）：账号同步直接用内存里的 access 令牌发请求，不像账户层那样
在 401 时刷新重试。令牌一旦被别处刷新轮换掉，每分钟自动同步和手动「立即同步」
就会一直报「未登录或会话已失效」，直到心跳把它刷回来。
"""
from __future__ import annotations

import io
import json
from urllib import error as urlerror

import pytest

from lvjiang.core.sync import client


class _Response:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self, _n: int) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http_error(status: int, body: dict) -> urlerror.HTTPError:
    return urlerror.HTTPError(
        "https://account.invalid", status, "err", {}, io.BytesIO(json.dumps(body).encode()))


def _install(monkeypatch, script: list) -> None:
    """让下一次（及后续）HTTP 调用按 script 顺序返回或抛错。"""
    pending = list(script)

    class _Opener:
        def open(self, _request, timeout=30):
            action = pending.pop(0)
            if isinstance(action, Exception):
                raise action
            return _Response(action)

    monkeypatch.setattr(client.urlrequest, "build_opener", lambda: _Opener())


def test_retries_once_after_refreshing_the_token(monkeypatch):
    stale_seen = []
    monkeypatch.setattr(client, "_fresh_token",
                        lambda stale: stale_seen.append(stale) or "fresh")
    _install(monkeypatch, [_http_error(401, {"error": "unauthorized"}), {"users": []}])

    assert client.post("/api/sync/users/pull", {}, "stale") == {"users": []}
    assert stale_seen == ["stale"]


def test_gives_up_after_one_retry(monkeypatch):
    fresh_calls = []
    monkeypatch.setattr(client, "_fresh_token",
                        lambda stale: fresh_calls.append(stale) or "fresh")
    _install(monkeypatch, [
        _http_error(401, {"error": "unauthorized"}),
        _http_error(401, {"error": "unauthorized"}),
    ])

    with pytest.raises(client.SyncError) as info:
        client.post("/api/sync/users/pull", {}, "stale")
    assert info.value.code == "unauthorized"
    assert str(info.value) == "未登录或会话已失效"
    assert len(fresh_calls) == 1          # 只换一次，不无限重试


def test_no_retry_when_refresh_returns_nothing(monkeypatch):
    """刷新不出来（服务端拒绝 / 凭据库异常）：直接把原错误报上去。"""
    monkeypatch.setattr(client, "_fresh_token", lambda stale: "")
    _install(monkeypatch, [_http_error(401, {"error": "token_expired"})])

    with pytest.raises(client.SyncError) as info:
        client.post("/api/sync/users/pull", {}, "stale")
    assert info.value.code == "token_expired"


def test_non_auth_errors_are_not_retried(monkeypatch):
    fresh_calls = []
    monkeypatch.setattr(client, "_fresh_token",
                        lambda stale: fresh_calls.append(stale) or "fresh")
    _install(monkeypatch, [_http_error(503, {"error": "service_unavailable"})])

    with pytest.raises(client.SyncError) as info:
        client.post("/api/sync/users/pull", {}, "stale")
    assert info.value.code == "service_unavailable"
    assert fresh_calls == []
