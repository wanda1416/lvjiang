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

    with pytest.raises(service.ProfileOperationRefused, match="同步其他数据"):
        service.undo_profile_history(3)
    assert undo_calls == []


def test_directional_sync_allows_only_unsynchronized_source_changes(tmp_path, monkeypatch):
    """增加未触发负向目标时可撤销，消耗及同步来源不能拆开撤销。"""
    from lvjiang.core.profile import repository, schema
    from lvjiang.core.profile.models import DIR_NEG

    config = ProfileSchema(keys_by_model={
        "stock": [
            StockKeyDef(key="source", sync_targets=[
                SyncTargetDef(key="stock:target", direction=DIR_NEG),
            ]),
            StockKeyDef(key="target"),
        ],
    })
    db = repository.ProfileDB(tmp_path / "profile.db")
    monkeypatch.setattr(repository, "_db", db)
    monkeypatch.setattr(schema, "_config", config)
    db.upsert("tester", "stock", "source", 10)
    db.upsert("tester", "stock", "target", 20)
    target_history = db.get_history("tester", "stock", "target")

    service.profile_action("tester", "source", delta=3)
    increase = db.get_history("tester", "stock", "source", limit=1)[0]
    assert service.profile_history_undo_unavailable_reason(increase) is None
    service.undo_profile_history(increase["id"])
    assert db.get_entry("tester", "stock", "source")["value"] == 10
    assert db.get_entry("tester", "stock", "target")["value"] == 20
    assert db.get_history("tester", "stock", "target") == target_history
    assert db.get_history("tester", "stock", "source", limit=1)[0]["change_type"] == "undo"

    service.profile_action("tester", "source", delta=-2)
    decrease = db.get_history("tester", "stock", "source", limit=1)[0]
    assert db.get_entry("tester", "stock", "target")["value"] == 18
    with pytest.raises(service.ProfileOperationRefused, match="同步其他数据"):
        service.undo_profile_history(decrease["id"])
    assert db.get_entry("tester", "stock", "source")["value"] == 8

    db.upsert("tester", "stock", "source", 11, change_type="action", sync_from="quota:grant")
    incoming = db.get_history("tester", "stock", "source", limit=1)[0]
    with pytest.raises(service.ProfileOperationRefused, match="同步产生的记录"):
        service.undo_profile_history(incoming["id"])
