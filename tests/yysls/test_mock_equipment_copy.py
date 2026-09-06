from __future__ import annotations

from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
from lvjiang.apps.yysls.core.loadout import (
    LoadoutRepository,
    copy_mock_items_to_users,
)


def _mock(name: str, value: float) -> dict:
    equip = {
        "type": "环",
        "name": name,
        "level": 110,
        "quality": "gold",
        "affix_1": {"name": "劲", "value": value},
        "_extra": {"is_mock": True, "affix_count": 1},
    }
    equip["_fp"] = make_fingerprint(equip, is_mock=True)
    return equip


def test_batch_copy_multiple_mocks_to_multiple_users(tmp_path):
    source = LoadoutRepository("source", tmp_path)
    first = _mock("模拟环一", 10)
    second = _mock("模拟环二", 20)
    for equip in (first, second):
        source.upsert_item(equip)

    results = copy_mock_items_to_users(
        "source", ["alice", "bob"],
        {first["_fp"], second["_fp"]}, tmp_path)

    assert [(result.target_username, result.copied) for result in results] == [
        ("alice", 2), ("bob", 2),
    ]
    assert set(LoadoutRepository("alice", tmp_path).load().equipment_items) == {
        first["_fp"], second["_fp"],
    }
    assert set(LoadoutRepository("bob", tmp_path).load().equipment_items) == {
        first["_fp"], second["_fp"],
    }


def test_copy_does_not_copy_plan_references_or_storage_metadata(tmp_path):
    source = LoadoutRepository("source", tmp_path)
    equip = _mock("模拟环", 10)
    source_plan = source.load().active_plan_id
    source.assign_equipment(source_plan, "ring", equip)
    target = LoadoutRepository("target", tmp_path)
    target.load()

    copy_mock_items_to_users(
        "source", ["target"], {equip["_fp"]}, tmp_path)

    copied = target.load()
    assert copied.active_plan.equipment["ring"] is None
    assert copied.equipment_items[equip["_fp"]].get("cooldown_expires_at", "") == ""
    assert copied.equipment_items[equip["_fp"]]["created_at"]
    assert copied.equipment_items[equip["_fp"]]["updated_at"]


def test_existing_and_conflicting_targets_are_not_overwritten(tmp_path):
    source = LoadoutRepository("source", tmp_path)
    equip = _mock("来源名称", 10)
    source.upsert_item(equip)

    same = LoadoutRepository("same", tmp_path)
    same.upsert_item(equip)
    conflict = LoadoutRepository("conflict", tmp_path)
    conflicting = {**equip, "name": "目标名称"}
    conflict.upsert_item(conflicting)

    results = copy_mock_items_to_users(
        "source", ["same", "conflict"], {equip["_fp"]}, tmp_path)

    assert (results[0].existing, results[0].conflicts) == (1, 0)
    assert (results[1].existing, results[1].conflicts) == (0, 1)
    assert conflict.load().equipment_items[equip["_fp"]]["name"] == "目标名称"
