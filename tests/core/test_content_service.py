"""传输层标识应用，并区分服务授权拒绝与边缘层 403。"""
import io
import json
from urllib.error import HTTPError

import pytest

from lvjiang.core.config import content_service


def test_identifies_application_and_distinguishes_edge_from_authorization_error(monkeypatch):
    response = b'{"accepted":true}'
    class Opener:
        def open(self, request, **kwargs):
            assert request.get_header("User-agent")
            assert not request.get_header("User-agent").startswith("Python-urllib")
            if response.startswith(b'{"accepted"'):
                return io.BytesIO(response)
            raise HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(response))
    monkeypatch.setattr(content_service, "build_opener", lambda *args: Opener())
    assert content_service.request_json("https://service.example.invalid", "/v1/key", {}) == {"accepted": True}
    response = b'error code: 1010'
    with pytest.raises(content_service.ServiceError) as edge:
        content_service.request_json("https://service.example.invalid", "/v1/key", {})
    assert edge.value.status == 403 and edge.value.code == ""
    response = json.dumps({"error": "forbidden"}).encode("utf-8")
    with pytest.raises(content_service.ServiceError) as denial:
        content_service.request_json("https://service.example.invalid", "/v1/key", {})
    assert denial.value.code == "forbidden"
