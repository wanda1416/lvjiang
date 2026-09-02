from __future__ import annotations

from lvjiang.apps.yysls.config.models import LevelConfig
from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
from lvjiang.apps.yysls.core.loadout import (
    LoadoutRepository,
    find_chengyin_merge_candidates,
)


def _levels() -> list[LevelConfig]:
    return [
        LevelConfig(level=91, allow_chengyin=True),
        LevelConfig(level=100, allow_chengyin=True),
        LevelConfig(
            level=105, allow_chengyin=True, allow_retransfer=True),
        LevelConfig(
            level=110, allow_chengyin=True, allow_retransfer=True),
    ]


def _equip(
    *,
    level: int = 100,
    values: tuple[float, ...] = (10, 20, 30, 40, 50),
    names: tuple[str, ...] = ("词一", "词二", "词三", "词四", "词五"),
    transferred: int | None = None,
    dingyin: bool = True,
    name: str = "套装甲",
) -> dict:
    equip: dict = {
        "type": "剑",
        "name": name,
        "level": level,
        "quality": "gold",
        "is_chengyin": level > 100,
        "dingyin": {"name": "定音甲", "value": 1} if dingyin else None,
    }
    for index, (affix_name, value) in enumerate(
        zip(names, values, strict=True), 1
    ):
        equip[f"affix_{index}"] = {
            "name": affix_name,
            "value": value,
            "is_transferred": transferred == index,
        }
    equip["_fp"] = make_fingerprint(equip)
    return equip


def _find(*equips: dict):
    return find_chengyin_merge_candidates(
        {equip["_fp"]: equip for equip in equips}, _levels())


def test_first_transfer_does_not_require_old_level_to_allow_retransfer():
    old = _equip(level=100)
    names = ("词一", "词二", "新词三", "词四", "词五")
    new = _equip(level=105, values=(11, 21, 8, 41, 51), names=names,
                 transferred=3)

    candidates = _find(old, new)

    assert len(candidates) == 1
    assert candidates[0].old_fp == old["_fp"]
    assert candidates[0].new_fp == new["_fp"]


def test_retransfer_name_change_requires_both_levels_to_allow_it():
    old = _equip(level=100, transferred=3)
    new = _equip(
        level=105,
        names=("词一", "词二", "新词三", "词四", "词五"),
        transferred=3,
    )

    assert _find(old, new) == []


def test_retransfer_at_fixed_position_is_allowed_for_infinite_levels():
    old = _equip(level=105, transferred=3)
    new = _equip(
        level=110,
        values=(11, 21, 8, 41, 51),
        names=("词一", "词二", "新词三", "词四", "词五"),
        transferred=3,
    )

    assert len(_find(old, new)) == 1


def test_crossed_affix_values_are_not_same_item():
    left = _equip(values=(10, 21, 30, 40, 50))
    right = _equip(values=(11, 20, 30, 40, 50), name="另一套装")

    assert _find(left, right) == []


def test_name_and_dingyin_contents_are_ignored():
    old = _equip(name="套装甲")
    new = _equip(values=(11, 21, 31, 41, 51), name="套装乙")
    new["dingyin"] = {"name": "完全不同的定音", "value": 999}

    assert len(_find(old, new)) == 1


def test_requires_five_affixes_and_any_dingyin():
    complete = _equip()
    incomplete = _equip(values=(11, 21, 31, 41, 51))
    incomplete.pop("affix_5")
    no_dingyin = _equip(values=(12, 22, 32, 42, 52), dingyin=False)

    assert _find(complete, incomplete, no_dingyin) == []


def test_more_than_five_affixes_is_not_a_full_valid_item():
    old = _equip()
    old["affix_6"] = {"name": "异常词条", "value": 1}
    new = _equip(values=(11, 21, 31, 41, 51))

    assert _find(old, new) == []


def test_transfer_slot_cannot_move():
    old = _equip(level=105, transferred=2)
    new = _equip(level=110, values=(11, 21, 31, 41, 51), transferred=3)

    assert _find(old, new) == []


def test_first_affix_cannot_become_transferred():
    old = _equip(level=100)
    new = _equip(level=105, values=(11, 21, 31, 41, 51), transferred=1)

    assert _find(old, new) == []


def test_both_levels_must_allow_chengyin():
    old = _equip(level=91)
    new = _equip(level=90, values=(11, 21, 31, 41, 51))

    assert _find(old, new) == []


def test_multiple_transfer_markers_are_rejected():
    old = _equip(level=105)
    old["affix_2"]["is_transferred"] = True
    old["affix_3"]["is_transferred"] = True
    new = _equip(level=110, values=(11, 21, 31, 41, 51), transferred=2)

    assert _find(old, new) == []


def test_zhige_dingyin_is_eligible():
    old = _equip(dingyin=False)
    new = _equip(values=(11, 21, 31, 41, 51), dingyin=False)
    old["_extra"] = {"is_zhige_dingyin": True}
    new["_extra"] = {"is_zhige_dingyin": True}

    assert len(_find(old, new)) == 1


def test_unknown_quality_is_rejected():
    old = _equip()
    new = _equip(values=(11, 21, 31, 41, 51))
    old["quality"] = "unknown"
    new["quality"] = "unknown"

    assert _find(old, new) == []


def test_merge_repository_migrates_all_plan_references(tmp_path):
    repo = LoadoutRepository("tester", users_dir=tmp_path)
    old = _equip()
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    old_fp = repo.upsert_item(old)
    new_fp = repo.upsert_item(new)
    plan_id = repo.load().active_plan_id
    repo.assign_equipment(plan_id, "main_weapon", old)

    repo.merge_items({old_fp: new_fp})

    state = repo.load()
    assert old_fp not in state.equipment_items
    assert state.plans[plan_id].equipment["main_weapon"] == new_fp


def test_merge_inherits_earliest_creation_and_latest_update(tmp_path):
    repo = LoadoutRepository("tester", users_dir=tmp_path)
    old = _equip()
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    old_fp = repo.upsert_item(old)
    new_fp = repo.upsert_item(new)

    def set_times(state):
        state.equipment_items[old_fp].update({
            "created_at": "2026-08-01T01:00:00+00:00",
            "updated_at": "2026-08-03T01:00:00+00:00",
        })
        state.equipment_items[new_fp].update({
            "created_at": "2026-08-02T01:00:00+00:00",
            "updated_at": "2026-08-04T01:00:00+00:00",
        })
    repo.update(set_times)

    repo.merge_items({old_fp: new_fp})

    merged = repo.load().equipment_items[new_fp]
    assert merged["created_at"] == "2026-08-01T01:00:00+00:00"
    assert merged["updated_at"] == "2026-08-04T01:00:00+00:00"


def test_merge_with_no_time_data_keeps_empty_values(tmp_path):
    repo = LoadoutRepository("tester", users_dir=tmp_path)
    old = _equip()
    new = _equip(level=105, values=(11, 21, 31, 41, 51))
    old_fp = repo.upsert_item(old)
    new_fp = repo.upsert_item(new)

    def clear_times(state):
        for fp in (old_fp, new_fp):
            state.equipment_items[fp]["created_at"] = ""
            state.equipment_items[fp]["updated_at"] = ""
    repo.update(clear_times)

    repo.merge_items({old_fp: new_fp})

    merged = repo.load().equipment_items[new_fp]
    assert merged["created_at"] == ""
    assert merged["updated_at"] == ""
