"""无 Lv1 不能通过传输入口绕过 UI 下发配置；有 Lv1 保持正常传输。"""
from unittest.mock import Mock

import pytest

from lvjiang.core.android import offline


def test_unlicensed_sync_is_rejected_before_reading_or_sending_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(offline, "has_feature", lambda level: False)
    agent = Mock()
    with pytest.raises(PermissionError, match="Lv1"):
        offline.sync_offline_bundle(agent, tmp_path / "not-created.zip")
    agent.call.assert_not_called()


def test_lv1_sync_keeps_existing_upload_protocol(monkeypatch, tmp_path):
    monkeypatch.setattr(offline, "has_feature", lambda level: level == "lv1")
    archive = tmp_path / "snapshot.zip"
    archive.write_bytes(b"configuration")
    agent = Mock()
    agent.status = {"offline_protocol": 1}
    agent.call.return_value = ({"ok": True}, None)
    assert offline.sync_offline_bundle(agent, archive) == {"ok": True}
    assert [call.args[0] for call in agent.call.call_args_list] == [
        "offline_sync_begin", "offline_sync_chunk", "offline_sync_commit",
    ]
