"""AI 测试只能使用草稿，不能隐式保存，取消后释放后台请求。"""
import asyncio

from lvjiang.core.ai import AIReply, AIService, AIStore
from lvjiang.ui.ai_settings import AISettingsPage


def test_test_connection_does_not_save_draft(qtbot, tmp_path, monkeypatch):
    requests = []

    async def reply(service):
        requests.append((service.settings.model, service._api_key))
        return AIReply("OK", service.settings.model, 0.1, None)

    monkeypatch.setattr(AIService, "test_connection", reply)
    store = AIStore(tmp_path / "session.json")
    page = AISettingsPage(store=store)
    qtbot.addWidget(page)
    page.url.setText("https://example.invalid/v1")
    page.model.setEditText("draft-model")
    page.key.setText("test-only-key")
    page.test()
    qtbot.waitUntil(lambda: page._worker is None)
    assert requests == [("draft-model", "test-only-key")]
    assert "连接可用" in page.status.text()
    assert not store.path.exists()
    page.url.setText("https://other.invalid/v1")
    assert page.key.text() == ""
    assert not page.status.text()


def test_cancel_aborts_request_and_reenables_form(qtbot, tmp_path, monkeypatch):
    stopped = []

    async def slow(_):
        try:
            await asyncio.sleep(10)
        finally:
            stopped.append(True)

    monkeypatch.setattr(AIService, "test_connection", slow)
    page = AISettingsPage(store=AIStore(tmp_path / "session.json"))
    qtbot.addWidget(page)
    page.url.setText("https://example.invalid/v1")
    page.model.setEditText("test-model")
    page.test()
    assert not page.test_button.isEnabled()
    page.cancel_test()
    qtbot.waitUntil(lambda: page._worker is None)
    assert stopped == [True]
    assert page.test_button.isEnabled()
    assert "取消" in page.status.text()


def test_auto_models_preserves_draft_and_does_not_repeat_or_save(qtbot, tmp_path, monkeypatch):
    from lvjiang.core.ai import AIModelList

    requests = []

    async def models(service):
        requests.append(service.settings.base_url)
        return AIModelList(("model-a", "model-b"), 0.1)

    monkeypatch.setattr(AIService, "list_models", models)
    page = AISettingsPage(store=AIStore(tmp_path / "session.json"))
    qtbot.addWidget(page)
    page.url.setText("https://example.invalid/v1")
    page.model.setEditText("custom-model")
    page.show()
    qtbot.waitUntil(lambda: page.model.count() == 2 and page._worker is None)
    assert page.model.currentText() == "custom-model"
    assert not page.store.path.exists()
    assert "测试连接" in page.status.text()
    page._auto_fetch()
    assert page._worker is None
    assert len(requests) == 1
    page.key.setText("test-only-key")
    assert page.model.count() == 0
    page.key.editingFinished.emit()
    qtbot.waitUntil(lambda: len(requests) == 2 and page._worker is None)
    assert page.model.currentText() == "custom-model"
