from pathlib import Path

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.equipment import EquipmentInventory
from lvjiang.apps.yysls.core.combat.equipment_sets import equipment_set_bonus
from lvjiang.apps.yysls.core.equip_parser.parser import EquipmentParser
from lvjiang.apps.yysls.core.graduation.scoring import equipment_attrs
from lvjiang.apps.yysls.core.loadout import LoadoutRepository

LEFT_SLOTS = ("main_weapon", "sub_weapon", "ring", "pendant")


def _left_sets(keys: tuple[str, str, str, str], levels=(110, 110, 110, 110)):
    return {
        slot: {"type": "剑" if "weapon" in slot else "环", "level": level,
               "equipment_set": key}
        for slot, key, level in zip(LEFT_SLOTS, keys, levels, strict=True)
    }


def test_set_registry_keeps_left_and_right_identity_separate():
    config = get_game_config()
    assert config.equipment_set_name("yudou") == "玉斗"
    assert config.equipment_set_name("yixiang") == "易相"
    assert config.get_equipment_sets("left")["yudou"][
        "recommended_right"] == "yixiang"
    assert config.recommended_equipment_set("鸣金·虹") == "yudou"


def test_two_piece_bonus_uses_exact_registered_level():
    bonus = equipment_set_bonus(
        _left_sets(("yudou", "yudou", "feisun", "feisun")),
        get_game_config(),
    )
    assert bonus.max_outer == pytest.approx(121.4)
    assert bonus.intent_rate == pytest.approx(0.07)


def test_average_between_tiers_uses_midpoint_not_linear_fraction():
    # 平均 107.5，严格位于 105/110 之间，固定取两档数值中点。
    equipped = _left_sets(
        ("yudou", "yudou", "yudou", "yudou"),
        (105, 105, 105, 115),
    )
    bonus = equipment_set_bonus(equipped, get_game_config())
    assert bonus.max_outer == pytest.approx((105.6 + 121.4) / 2)
    # 正常评分入口必须复用同一套组合级规则。
    assert equipment_attrs(equipped, get_game_config()).max_outer == pytest.approx(
        bonus.max_outer)


def test_incomplete_historical_set_data_adds_no_bonus():
    equipped = _left_sets(("yudou", "yudou", "", ""))
    assert equipment_set_bonus(equipped, get_game_config()).max_outer == 0


def test_parser_reads_only_known_set_from_full_detail():
    parsed = EquipmentParser().parse({
        "equip_type": "流星含光 | 武器·剑",
        "equip_level": "115阶",
        "equip_detail": "流星含光 | 玉斗套装 | 2/4 | 最大外功攻击",
    })
    assert parsed.equipment_set == "yudou"

    wrong_side = EquipmentParser().parse({
        "equip_type": "雁南飞冠 | 冠胄",
        "equip_level": "115阶",
        "equip_detail": "玉斗套装 | 易相套装",
    })
    assert wrong_side.equipment_set == "yixiang"
    assert get_game_config().resolve_equipment_set("推荐玉斗", "left") == ""


def test_plan_scan_updates_plan_set_without_polluting_existing_item(tmp_path: Path):
    repo = LoadoutRepository("tester", tmp_path)
    first = repo.load().active_plan_id
    second = repo.create_plan("PVP", "无名剑法", "无名枪法").id
    original = {"_fp": "same", "type": "环", "level": 110,
                "equipment_set": "yudou"}
    repo.assign_equipment(first, "ring", original, scanned=True)
    rescanned = {**original, "equipment_set": "feisun"}
    repo.assign_equipment(second, "ring", rescanned, scanned=True)

    state = repo.load()
    assert state.equipment_items["same"]["equipment_set"] == "yudou"
    assert state.plans[first].equipment_sets["ring"] == "yudou"
    assert state.plans[second].equipment_sets["ring"] == "feisun"
    assert state.resolved_equipment(first)["ring"]["equipment_set"] == "yudou"
    assert state.resolved_equipment(second)["ring"]["equipment_set"] == "feisun"


def test_apply_combo_keeps_item_set_and_writes_selected_plan_set(tmp_path: Path):
    repo = LoadoutRepository("tester", tmp_path)
    plan_id = repo.load().active_plan_id
    item = {"_fp": "same", "type": "环", "level": 110,
            "equipment_set": "yudou"}
    repo.assign_equipment(plan_id, "ring", item, scanned=True)
    repo.set_plan_equipment_set(plan_id, "ring", "feisun")
    inventory = EquipmentInventory.__new__(EquipmentInventory)
    inventory._repo = repo
    inventory._state = repo.load()

    inventory.apply_combos(
        {"ring": inventory.state.resolved_equipment()["ring"]},
        equipment_set="huanhua",
    )

    state = repo.load()
    assert state.equipment_items["same"]["equipment_set"] == "yudou"
    assert state.active_plan.equipment_sets["ring"] == "huanhua"


def test_merge_items_carries_missing_original_set(tmp_path: Path):
    repo = LoadoutRepository("tester", tmp_path)
    repo.upsert_item({"_fp": "kept", "type": "环", "level": 110})
    repo.upsert_item({
        "_fp": "removed", "type": "环", "level": 110,
        "equipment_set": "yudou",
    })

    repo.merge_items({"removed": "kept"})

    assert repo.load().equipment_items["kept"]["equipment_set"] == "yudou"
