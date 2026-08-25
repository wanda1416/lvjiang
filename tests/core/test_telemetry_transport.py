"""传输层：https 强制、响应体完全忽略、异常收敛为 False（绝不外抛）。"""
from __future__ import annotations

from io import BytesIO

import pytest

from lvjiang.core.telemetry import transport


class _FakeResponse:
    def __init__(self, status=204, body=b""):
        self.status = status
        self._body = BytesIO(body)

    def read(self):
        return self._body.read()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestSchemeEnforcement:
    def test_rejects_http_to_remote_host(self, monkeypatch):
        called = []
        monkeypatch.setattr(transport, "urlopen", lambda *a, **k: called.append(1))
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "http://example.com/v1/report")
        assert transport.post_report({}) is False
        assert not called  # 拒绝的地址根本不该发起连接

    def test_allows_https(self, monkeypatch):
        monkeypatch.setattr(transport, "urlopen", lambda *a, **k: _FakeResponse(204))
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "https://example.com/v1/report")
        assert transport.post_report({"a": 1}) is True

    def test_allows_http_localhost_for_local_dev(self, monkeypatch):
        monkeypatch.setattr(transport, "urlopen", lambda *a, **k: _FakeResponse(204))
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "http://127.0.0.1:8787/v1/report")
        assert transport.post_report({}) is True


class TestResponseBodyIgnored:
    """服务端返回任何合法 JSON 指令都不能改变客户端状态。"""

    def test_body_content_never_parsed_or_acted_on(self, monkeypatch):
        body = b'{"command": "delete_all_local_data", "disable": true}'
        monkeypatch.setattr(transport, "urlopen", lambda *a, **k: _FakeResponse(204, body))
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "https://example.com/v1/report")
        # 唯一的观测面：返回值只反映 HTTP 状态码，不反映 body 内容
        assert transport.post_report({}) is True


class TestStatusCodes:
    @pytest.mark.parametrize("status,expected", [(200, True), (204, True), (299, True),
                                                 (300, False), (404, False), (500, False)])
    def test_status_range(self, monkeypatch, status, expected):
        monkeypatch.setattr(transport, "urlopen", lambda *a, **k: _FakeResponse(status))
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "https://example.com/v1/report")
        assert transport.post_report({}) is expected


class TestNeverRaises:
    def test_network_exception_returns_false_not_raise(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("network down")
        monkeypatch.setattr(transport, "urlopen", _boom)
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "https://example.com/v1/report")
        assert transport.post_report({}) is False

    def test_timeout_returns_false(self, monkeypatch):
        import socket
        monkeypatch.setattr(transport, "urlopen",
                            lambda *a, **k: (_ for _ in ()).throw(socket.timeout()))
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "https://example.com/v1/report")
        assert transport.post_report({}) is False


class TestUrlOverride:
    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("LVJIANG_TELEMETRY_URL", "https://custom.example/v1/report")
        assert transport.telemetry_url() == "https://custom.example/v1/report"

    def test_default_is_https(self, monkeypatch):
        monkeypatch.delenv("LVJIANG_TELEMETRY_URL", raising=False)
        assert transport.telemetry_url().startswith("https://")
