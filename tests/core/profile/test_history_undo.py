"""Profile 历史撤销的业务安全边界。"""

import pytest

from lvjiang.core.profile import service
from lvjiang.core.profile.models import StockKeyDef, SyncTargetDef
from lvjiang.core.profile.schema import ProfileSchema


def test_undo_rejects_key_with_sync_targets(monkeypatch):
    record = {
        "id": 3,
        "username": "user",
        "type": "stock",
        "key": "source",
        "old_value": 1.0,
        "new_value": 2.0,
        "old_value_text": "",
        "new_value_text": "",
        "change_type": "action",
        "sync_from": None,
    }
    config = ProfileSchema(keys_by_model={
        "stock": [StockKeyDef(
            key="source",
            sync_targets=[SyncTargetDef(key="stock:target")],
        )],
    })
    monkeypatch.setattr(service, "get_profile_config", lambda: config)
    monkeypatch.setattr(service, "db_get_history_record", lambda history_id: record)
    monkeypatch.setattr(service, "db_get_history", lambda *args, **kwargs: [record])
    undo_calls = []
    monkeypatch.setattr(
        service, "db_undo_history", lambda history_id: undo_calls.append(history_id),
    )

    with pytest.raises(ValueError, match="同步其他数据"):
        service.undo_profile_history(3)
    assert undo_calls == []
