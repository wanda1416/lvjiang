"""「假设同级承音」计算设置：投影、写入合并、属性行与应用落标。

这是用户显式给单件装备设的开关：装备事实仍是未承音，但满承音投影按承音
上限取值，不再需要为同等级装备生成承音/不承音两个平行候选分支。
"""

from __future__ import annotations

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import apply_hypothetical_caps
from lvjiang.apps.yysls.core.equip_parser.constants import ASSUME_CHENGYIN_KEY
from lvjiang.apps.yysls.core.loadout.equipment_write import (
    WriteSource,
    merge_equipment_write,
)


def _season_equipment(pct: float = 0.83) -> dict:
    """一件本赛季等级、未承音、词条达到给定比例的装备。"""
    gc = get_game_config()
    level = gc.current_equip_level()
    caps = gc.get_affix_caps(level, "最大外功攻击")
    assert caps is not None
    return {
        "type": "枪",
        "name": "测试枪",
        "level": level,
        "quality": "gold",
        "is_chengyin": False,
        "affix_1": {
            "name": "最大外功攻击",
            "value": round(float(caps["cap"]) * pct, 1),
        },
    }


def test_assume_chengyin_makes_full_chengyin_use_the_chengyin_cap() -> None:
    """勾选后满承音直接按承音上限计算，不再需要平行分支。"""
    gc = get_game_config()
    level = gc.current_equip_level()
    caps = gc.get_affix_caps(level, "最大外功攻击")
    equip = _season_equipment(0.83) | {ASSUME_CHENGYIN_KEY: True}

    projected = apply_hypothetical_caps({"weapon": equip}, full_chengyin=True)

    assert projected["weapon"]["affix_1"]["value"] == caps["chengyin"]
    assert projected["weapon"]["is_chengyin"] is True
    # 投影只作用在副本上，原装备仍是未承音的 83%。
    assert equip["affix_1"]["value"] != caps["chengyin"]
    assert equip["is_chengyin"] is False


def test_without_the_flag_season_native_equipment_stays_native() -> None:
    """未勾选时维持既有口径：满承音不动本赛季原生装备。"""
    equip = _season_equipment(0.83)

    projected = apply_hypothetical_caps({"weapon": equip}, full_chengyin=True)

    assert projected["weapon"]["affix_1"]["value"] == equip["affix_1"]["value"]
    assert projected["weapon"]["is_chengyin"] is False


def test_assume_chengyin_truncates_values_above_the_cap() -> None:
    """假设承音与真实承音一致：高于承音上限的词条会被截断到上限。"""
    gc = get_game_config()
    level = gc.current_equip_level()
    caps = gc.get_affix_caps(level, "最大外功攻击")
    equip = _season_equipment(0.96) | {ASSUME_CHENGYIN_KEY: True}
    assert equip["affix_1"]["value"] > caps["chengyin"]

    projected = apply_hypothetical_caps({"weapon": equip}, full_chengyin=True)

    assert projected["weapon"]["affix_1"]["value"] == caps["chengyin"]


def test_flag_is_ignored_while_full_chengyin_is_off() -> None:
    """开关只改变满承音的行为，不单独触发承音投影。"""
    equip = _season_equipment(0.83) | {ASSUME_CHENGYIN_KEY: True}

    projected = apply_hypothetical_caps({"weapon": equip})

    assert projected["weapon"]["affix_1"]["value"] == equip["affix_1"]["value"]
    assert projected["weapon"]["is_chengyin"] is False


def test_scan_write_keeps_the_assumption_flag() -> None:
    """扫描数据永远不带这个字段，写入时必须从旧记录搬回来。"""
    existing = _season_equipment(0.83) | {ASSUME_CHENGYIN_KEY: True}
    scanned = {
        key: value for key, value in existing.items()
        if key != ASSUME_CHENGYIN_KEY
    }

    merged = merge_equipment_write(
        scanned, existing, source=WriteSource.BAG_SCAN)

    assert merged[ASSUME_CHENGYIN_KEY] is True


def test_flagged_equipment_does_not_spawn_a_parallel_chengyin_branch() -> None:
    """勾选后满承音已按承音取值，再派生平行分支只会得到重复候选。"""
    from lvjiang.apps.yysls.core.graduation.optimal_combo import (
        build_candidate_variants,
    )

    level = get_game_config().current_equip_level()
    flagged = _season_equipment(0.83) | {ASSUME_CHENGYIN_KEY: True}

    variants = build_candidate_variants(
        {"main_weapon": [flagged]},
        season_chengyin=True, season_level=level,
    )["main_weapon"]
    assert len(variants) == 1

    plain = build_candidate_variants(
        {"main_weapon": [_season_equipment(0.83)]},
        season_chengyin=True, season_level=level,
    )["main_weapon"]
    assert len(plain) == 2


def test_property_rows_offer_the_flag_only_for_season_native_equipment() -> None:
    """开关只对本赛季等级、且尚未承音的装备开放。"""
    from lvjiang.apps.yysls.ui.loadout.equip.cards import (
        _equipment_property_rows,
    )

    level = get_game_config().current_equip_level()
    names = [name for name, _ in _equipment_property_rows(_season_equipment())]
    assert names.index("状态") < names.index("计算设置") < names.index("引用方案")

    older = _season_equipment() | {"level": level - 10}
    assert "计算设置" not in [
        name for name, _ in _equipment_property_rows(older)]

    already = _season_equipment() | {"is_chengyin": True}
    assert "计算设置" not in [
        name for name, _ in _equipment_property_rows(already)]


def test_optimal_apply_writes_the_flag_back_to_the_equipment() -> None:
    """应用组合时把「同等级承音假设」落成装备开关，不再丢失该状态。"""
    from lvjiang.apps.yysls.ui.loadout.optimal_combo import OptimalComboPage

    equip = _season_equipment(0.83)
    result = {
        "equipped": {"main_weapon": equip, "ring": dict(equip)},
        "assumptions": {"main_weapon": ["同等级承音假设"]},
    }

    applied = OptimalComboPage._apply_assume_chengyin(result)

    assert applied["main_weapon"][ASSUME_CHENGYIN_KEY] is True
    assert ASSUME_CHENGYIN_KEY not in applied["ring"]
    assert ASSUME_CHENGYIN_KEY not in equip


def test_repository_toggles_the_flag_without_touching_the_fingerprint(
    tmp_path,
) -> None:
    """开关跟装备走：按指纹写入，取消勾选时移除字段。"""
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository

    repo = LoadoutRepository("tester", tmp_path)
    equip = _season_equipment(0.83) | {"_fp": "abc12345", "_extra": {}}
    repo.upsert_item(equip)

    repo.set_item_assume_chengyin("abc12345", True)
    saved = repo.load().equipment_items["abc12345"]
    assert saved[ASSUME_CHENGYIN_KEY] is True
    assert saved["_fp"] == "abc12345"

    repo.set_item_assume_chengyin("abc12345", False)
    assert ASSUME_CHENGYIN_KEY not in repo.load().equipment_items["abc12345"]
