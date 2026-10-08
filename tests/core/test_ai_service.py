"""保护真实模型调用、错误隔离和总超时契约；不访问外网。"""
import asyncio
import json

import httpx
import pytest

from lvjiang.core.ai import AIError, AIService, AISettings, AIStore


def test_connection_uses_selected_model_and_requires_text():
    def respond(request):
        assert request.url == "https://example.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-only-key"
        assert json.loads(request.content)["model"] == "test-model"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "OK"}}],
            "usage": {"total_tokens": 5},
        })

    service = AIService(AISettings("https://example.invalid/v1", "test-model"),
                        "test-only-key", transport=httpx.MockTransport(respond))
    result = asyncio.run(service.test_connection())
    assert result.text == "OK"
    assert result.usage == {"total_tokens": 5}


@pytest.mark.parametrize(("status", "body", "code"), [
    (401, {"error": "private response"}, "auth"),
    (429, {"error": "private response"}, "rate_limit"),
    (200, {"choices": []}, "response"),
])
def test_failure_is_actionable_and_does_not_expose_response(status, body, code):
    service = AIService(AISettings("https://example.invalid/v1", "test-model"),
                        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)))
    with pytest.raises(AIError) as caught:
        asyncio.run(service.test_connection())
    assert caught.value.code == code
    assert "private response" not in str(caught.value)


def test_total_timeout_cancels_request():
    cancelled = []

    async def slow(_):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)

    service = AIService(AISettings("https://example.invalid/v1", "test-model", 1),
                        transport=httpx.MockTransport(slow))
    with pytest.raises(AIError) as caught:
        asyncio.run(service.test_connection())
    assert caught.value.code == "timeout"
    assert cancelled == [True]


def test_settings_persist_without_key_and_credentials_are_scoped(tmp_path, monkeypatch):
    secrets = {}

    class Credentials:
        def get_password(self, service, account):
            return secrets.get((service, account))

        def set_password(self, service, account, key):
            secrets[service, account] = key

        def delete_password(self, service, account):
            secrets.pop((service, account))

    monkeypatch.setattr(AIStore, "_credentials", staticmethod(lambda: Credentials()))
    store = AIStore(tmp_path / "session" / "session.json")
    store.path.parent.mkdir(parents=True)
    store.path.write_text(json.dumps({"version": 2, "settings": {"theme": "dark"}}), encoding="utf-8")
    store._store.reload()
    settings = AISettings("https://example.invalid/v1", "test-model")
    store.save(settings, "test-only-key")
    assert AIStore(store.path).settings() == settings
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert data["settings"]["theme"] == "dark"
    assert data["settings"]["ai"]["model"] == "test-model"
    assert "test-only-key" not in store.path.read_text(encoding="utf-8")
    assert store.get_key(settings.base_url) == "test-only-key"
    assert store.get_key("https://other.invalid/v1") == ""
    assert not store.path.with_name("ai.json").exists()
    store.save(settings, "")
    assert store.get_key(settings.base_url) == ""


def test_models_can_be_fetched_without_selecting_a_model():
    def respond(request):
        assert request.method == "GET"
        assert request.url == "https://example.invalid/v1/models"
        assert request.headers["authorization"] == "Bearer test-only-key"
        return httpx.Response(200, json={"data": [
            {"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"},
        ]})

    service = AIService(AISettings("https://example.invalid/v1"), "test-only-key",
                        transport=httpx.MockTransport(respond))
    result = asyncio.run(service.list_models())
    assert result.models == ("model-a", "model-b")
    with pytest.raises(AIError, match="请填写模型名称"):
        asyncio.run(service.test_connection())


def test_unsupported_model_listing_keeps_manual_call_available():
    def respond(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": "invalid"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    service = AIService(AISettings("https://example.invalid/v1", "manual-model"),
                        transport=httpx.MockTransport(respond))
    with pytest.raises(AIError) as caught:
        asyncio.run(service.list_models())
    assert caught.value.code == "response"
    assert asyncio.run(service.test_connection()).text == "OK"
