import json
from pathlib import Path

import pytest

import lvjiang.apps.yysls.core.loadout.repository as repository_module
from lvjiang.apps.yysls.core.loadout import LoadoutRepository, resolve_school


def equip(fp="real-fp", type_="环"):
    return {"_fp": fp, "type": type_, "level": 110}


def test_default_plan_and_shared_pool(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    state = repo.load()
    assert len(state.plans) == 1
    repo.assign_equipment(state.active_plan_id, "ring", equip())
    state = repo.load()
    assert state.active_plan.equipment["ring"] == "real-fp"
    assert state.resolved_equipment()["ring"]["type"] == "环"


def test_upsert_is_idempotent(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    repo.upsert_item(equip())
    repo.upsert_item({**equip(), "level": 111})
    state = repo.load()
    assert list(state.equipment_items) == ["real-fp"]
    assert state.equipment_items["real-fp"]["level"] == 111


def test_same_fingerprint_rescan_updates_cooldown_expiry(tmp_path: Path):
    """冷却不参与指纹，但同一装备重扫时必须覆盖其动态到期时间。"""
    repo = LoadoutRepository("alice", tmp_path)
    repo.upsert_item({
        **equip(),
        "cooldown_expires_at": "2026-09-07T04:00:00.000+00:00",
    })
    repo.upsert_item({
        **equip(),
        "cooldown_expires_at": "2026-09-08T20:00:00.000+00:00",
    })

    stored = repo.load().equipment_items["real-fp"]
    assert stored["cooldown_expires_at"] == (
        "2026-09-08T20:00:00.000+00:00")


def test_new_fp_records_both_times_and_existing_fp_refreshes_update(
    tmp_path: Path, monkeypatch,
):
    timestamps = iter([
        "2026-09-01T01:00:00.000+00:00",
        "2026-09-01T02:00:00.000+00:00",
    ])
    monkeypatch.setattr(repository_module, "_now_iso", lambda: next(timestamps))
    repo = LoadoutRepository("alice", tmp_path)

    repo.upsert_item(equip())
    created = repo.load().equipment_items["real-fp"]
    assert created["created_at"] == "2026-09-01T01:00:00.000+00:00"
    assert created["updated_at"] == "2026-09-01T01:00:00.000+00:00"

    repo.upsert_item({**equip(), "level": 111})
    updated = repo.load().equipment_items["real-fp"]
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] == "2026-09-01T02:00:00.000+00:00"


def test_legacy_equipment_times_are_empty_and_creation_is_not_fabricated(
    tmp_path: Path, monkeypatch,
):
    repo = LoadoutRepository("alice", tmp_path)
    state = repo.load()
    payload = state.to_dict()
    payload["equipment_items"] = {"real-fp": equip()}
    repo.path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = repo.load().equipment_items["real-fp"]
    assert legacy["created_at"] == ""
    assert legacy["updated_at"] == ""

    monkeypatch.setattr(
        repository_module, "_now_iso",
        lambda: "2026-09-01T03:00:00.000+00:00")
    repo.upsert_item(equip())
    refreshed = repo.load().equipment_items["real-fp"]
    assert refreshed["created_at"] == ""
    assert refreshed["updated_at"] == "2026-09-01T03:00:00.000+00:00"


def test_combo_application_uses_the_same_timestamp_write_path(
    tmp_path: Path, monkeypatch,
):
    import lvjiang.constants
    from lvjiang.apps.yysls.core.combat.equipment import EquipmentInventory

    monkeypatch.setattr(lvjiang.constants, "USERS_DIR", tmp_path)
    monkeypatch.setattr(
        repository_module, "_now_iso",
        lambda: "2026-09-01T04:00:00.000+00:00")
    inventory = EquipmentInventory("alice")

    inventory.apply_combos({"ring": equip()})

    item = LoadoutRepository("alice", tmp_path).load().equipment_items["real-fp"]
    assert item["created_at"] == "2026-09-01T04:00:00.000+00:00"
    assert item["updated_at"] == "2026-09-01T04:00:00.000+00:00"


def test_delete_clears_all_plan_references(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    first = repo.load().active_plan_id
    repo.assign_equipment(first, "ring", equip())
    second = repo.create_plan("second", "主功法", "副功法").id
    repo.assign_equipment(second, "ring", equip())
    repo.delete_items({"real-fp"})
    state = repo.load()
    assert all(plan.equipment["ring"] is None for plan in state.plans.values())


def test_delete_all_real_preserves_mock(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    repo.upsert_item(equip())
    repo.upsert_item(equip("mock_x", "剑"))
    repo.delete_all_real()
    assert set(repo.load().equipment_items) == {"mock_x"}


def test_update_equipped_mock_removes_orphan_old_fp(tmp_path: Path):
    """编辑已装备模拟装备：新数据写入后，无引用的旧指纹被清理。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_id = repo.load().active_plan_id
    old = {"type": "剑", "name": "模拟剑 2", "level": 110,
           "_extra": {"is_mock": True}}
    old_fp = repo.assign_equipment(plan_id, "main_weapon", old)
    assert old_fp.startswith("mock_")

    edited = {**old, "level": 100}  # 词条外字段变化 → 新指纹
    new_fp = repo.update_equipped_mock(plan_id, "main_weapon", old_fp, edited)
    state = repo.load()
    assert new_fp != old_fp
    assert state.active_plan.equipment["main_weapon"] == new_fp
    assert new_fp in state.equipment_items
    assert old_fp not in state.equipment_items  # 旧孤儿被清理


def test_update_equipped_mock_keeps_shared_old_fp(tmp_path: Path):
    """旧指纹仍被其他方案引用时不清理，宁脏勿丢。"""
    repo = LoadoutRepository("alice", tmp_path)
    first = repo.load().active_plan_id
    old = {"type": "剑", "name": "模拟剑 2", "level": 110,
           "_extra": {"is_mock": True}}
    old_fp = repo.assign_equipment(first, "main_weapon", old)
    second = repo.create_plan("second", "主功法", "副功法").id
    repo.assign_equipment(second, "main_weapon", {**old})

    new_fp = repo.update_equipped_mock(
        first, "main_weapon", old_fp, {**old, "level": 100})
    state = repo.load()
    assert state.plans[second].equipment["main_weapon"] == old_fp
    assert old_fp in state.equipment_items  # 被共用，不清理
    assert state.plans[first].equipment["main_weapon"] == new_fp


def test_update_equipped_mock_noop_edit_keeps_single_item(tmp_path: Path):
    """编辑未改变指纹时，不应误删自身。"""
    repo = LoadoutRepository("alice", tmp_path)
    plan_id = repo.load().active_plan_id
    old = {"type": "剑", "name": "模拟剑 2", "level": 110,
           "_extra": {"is_mock": True}}
    old_fp = repo.assign_equipment(plan_id, "main_weapon", old)

    new_fp = repo.update_equipped_mock(
        plan_id, "main_weapon", old_fp, {**old, "name": "改名不影响指纹"})
    state = repo.load()
    assert new_fp == old_fp
    assert old_fp in state.equipment_items
    assert state.active_plan.equipment["main_weapon"] == new_fp


def test_noop_mock_edit_preserves_creation_and_refreshes_update(
    tmp_path: Path, monkeypatch,
):
    timestamps = iter([
        "2026-09-01T01:00:00.000+00:00",
        "2026-09-01T02:00:00.000+00:00",
    ])
    monkeypatch.setattr(repository_module, "_now_iso", lambda: next(timestamps))
    repo = LoadoutRepository("alice", tmp_path)
    plan_id = repo.load().active_plan_id
    old = {"type": "剑", "name": "模拟剑", "level": 110,
           "_extra": {"is_mock": True}}
    fp = repo.assign_equipment(plan_id, "main_weapon", old)

    repo.update_equipped_mock(plan_id, "main_weapon", fp, {**old})

    item = repo.load().equipment_items[fp]
    assert item["created_at"] == "2026-09-01T01:00:00.000+00:00"
    assert item["updated_at"] == "2026-09-01T02:00:00.000+00:00"


def test_exact_school_resolution():
    schools = {"流派": {
        "main": {"martial_art": "主功法"},
        "sub": {"martial_art": "副功法"},
    }}
    assert resolve_school("主功法", "副功法", schools) == "流派"
    assert resolve_school("副功法", "主功法", schools) == "流派"


def test_create_plan_requires_both_martial_arts(tmp_path: Path):
    """新建方案必须同时绑定主副武学，不允许无武学方案。"""
    repo = LoadoutRepository("alice", tmp_path)
    with pytest.raises(ValueError):
        repo.create_plan("p", "", "副功法")
    with pytest.raises(ValueError):
        repo.create_plan("p", "主功法", "")
    plan = repo.create_plan("p", "主功法", "副功法")
    assert (plan.main_martial_art, plan.sub_martial_art) == ("主功法", "副功法")


def test_combat_selections_are_independent_per_plan(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    first = repo.load().active_plan_id
    second = repo.create_plan("second", "主功法", "副功法").id

    repo.configure_plan(
        first,
        base_attribute="属性甲",
        gongjue="会意",
        graduation_scheme="方案甲",
    )
    repo.configure_plan(
        second,
        base_attribute="属性乙",
        gongjue="精准",
        graduation_scheme="方案乙",
    )

    reloaded = repo.load()
    assert (
        reloaded.plans[first].base_attribute,
        reloaded.plans[first].gongjue,
        reloaded.plans[first].graduation_scheme,
    ) == ("属性甲", "会意", "方案甲")
    assert (
        reloaded.plans[second].base_attribute,
        reloaded.plans[second].gongjue,
        reloaded.plans[second].graduation_scheme,
    ) == ("属性乙", "精准", "方案乙")


def test_legacy_plan_defaults_new_combat_selections_to_empty(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    state = repo.load()
    payload = state.to_dict()
    plan = payload["plans"][state.active_plan_id]
    plan.pop("base_attribute")
    plan.pop("gongjue")
    plan.pop("graduation_scheme")
    repo.path.write_text(json.dumps(payload), encoding="utf-8")

    legacy = repo.load().active_plan

    assert (
        legacy.base_attribute,
        legacy.gongjue,
        legacy.graduation_scheme,
    ) == ("", "", "")

    # 旧方案下一次正常写入时，直接按新结构落盘，不迁移任何用户级旧值。
    repo.configure_plan(legacy.id, name="旧方案改名")
    saved = json.loads(repo.path.read_text(encoding="utf-8"))
    saved_plan = saved["plans"][legacy.id]
    assert saved_plan["base_attribute"] == ""
    assert saved_plan["gongjue"] == ""
    assert saved_plan["graduation_scheme"] == ""


def test_cannot_delete_last_plan(tmp_path: Path):
    repo = LoadoutRepository("alice", tmp_path)
    with pytest.raises(ValueError):
        repo.delete_plan(repo.load().active_plan_id)
