"""真实装备的承音操作。

承音不是一个可以随手勾掉的布尔：它要同时决定目标等级、按升级表换掉被合并
的词条、把数值截断到承音上限，三件事缺一件写入就会被养成规则拒掉。所以
勾选框改成只读状态展示，承音由按钮执行。
"""

from __future__ import annotations

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.affix_cap import affix_cap_value
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    aggregate_equipment_attrs,
    effective_equipped,
)
from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
from lvjiang.apps.yysls.core.loadout.development_rules import (
    check_real_development,
)
from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
from lvjiang.apps.yysls.ui.loadout.equip.mock_dialog import MockEquipDialog


@pytest.fixture
def gc():
    return get_game_config()


def _equip(part: str, level: int, affixes: list[tuple[str, float]], *,
           chengyin: bool = False) -> dict:
    equip = {
        "type": part, "name": "测试装备", "level": level,
        "original_level": level, "quality": "gold", "is_chengyin": chengyin,
    }
    for index, (name, value) in enumerate(affixes, 1):
        equip[f"affix_{index}"] = {"name": name, "value": value, "unit": "%"}
    equip["_fp"] = make_fingerprint(equip)
    return equip


def _dialog(qtbot, equip: dict) -> MockEquipDialog:
    dialog = MockEquipDialog(equip)
    qtbot.addWidget(dialog)
    return dialog


_HELM_110 = [("会意率", 5.0), ("单体类奇术增伤", 15.4),
             ("最大外功攻击", 100.0), ("劲", 50.0), ("势", 50.0)]


# ─── 勾选框只读、按钮接管 ──────────────────────────────────

def test_chengyin_checkbox_is_read_only_in_real_development(qtbot):
    dialog = _dialog(qtbot, _equip("冠胄", 110, _HELM_110))

    assert dialog._check_chengyin.isEnabled() is False
    # 窗口未 show 时 isVisible 恒为假，这里问的是控件自己有没有被隐藏
    assert not dialog._btn_chengyin.isHidden()


# ─── 跨等阶承音 ────────────────────────────────────────────

def test_chengyin_upgrades_the_merged_affix_without_looking_like_a_transmute(
        qtbot, gc):
    """跨过 115 时旧奇术词条换成全奇术增伤——是改名，不是转律。

    当成转律的话会打上 is_transferred、触发转律冷却，并占掉这件装备唯一的
    转律名额；养成校验也会因为「不支持该转律变化」直接拒绝写入。
    """
    equip = _equip("冠胄", 110, _HELM_110)
    dialog = _dialog(qtbot, equip)

    dialog._btn_chengyin.click()
    result = dialog._build_real_development_data()

    assert result["level"] == 115
    assert result["is_chengyin"] is True
    assert result["affix_2"]["name"] == "全奇术增伤"
    assert not result["affix_2"].get("is_transferred")
    assert not result.get("cooldown_kind")
    assert check_real_development(equip, result) is None


def test_repository_does_not_start_transmute_cooldown_for_affix_upgrade(
        qtbot, tmp_path):
    """承音自动改名必须在仓储写入边界也保持为承音，而不是转律。"""
    equip = _equip("冠胄", 110, _HELM_110)
    dialog = _dialog(qtbot, equip)
    dialog._btn_chengyin.click()
    result = dialog._build_real_development_data()
    repo = LoadoutRepository("test-user", tmp_path)
    old_fp = repo.upsert_item(equip)

    new_fp = repo.update_real_development(old_fp, result)

    stored = repo.load().equipment_items[new_fp]
    assert stored["affix_2"]["name"] == "全奇术增伤"
    assert not stored.get("cooldown_kind")
    assert not stored.get("cooldown_expires_at")


def test_upgraded_affix_still_feeds_the_calculation(qtbot, gc):
    """改名后这条词条必须照样算进去，否则承音等于白丢一条词条。"""
    equip = _equip("冠胄", 110, _HELM_110)
    dialog = _dialog(qtbot, equip)

    dialog._btn_chengyin.click()
    result = dialog._build_real_development_data()

    before = aggregate_equipment_attrs(effective_equipped({"head": equip}, gc))
    after = aggregate_equipment_attrs(effective_equipped({"head": result}, gc))
    assert before.single_qs_bonus == pytest.approx(0.154)
    assert after.all_qs_bonus == pytest.approx(0.154)


def test_affix_retired_from_the_new_first_pool_survives_chengyin(qtbot):
    """115 不再首出「体」，但原生 110 首出的那条承音后仍然留着。

    换等级时重建词条下拉会把它从候选里抹掉、连带清空该行，承音就变成了
    「删词条」而被写入校验拒掉。
    """
    equip = _equip("胫甲", 110, [
        ("体", 30.0), ("最大外功攻击", 100.0), ("劲", 50.0),
        ("势", 50.0), ("外功防御", 40.0)])
    dialog = _dialog(qtbot, equip)

    dialog._btn_chengyin.click()
    result = dialog._build_real_development_data()

    assert result["affix_1"]["name"] == "体"
    assert check_real_development(equip, result) is None


# ─── 原地承音与截断 ────────────────────────────────────────

def test_top_level_equipment_chengyins_in_place_and_clamps(qtbot, gc):
    """已在本赛季最高等阶的装备升无可升：只标记承音，数值落到承音上限。"""
    level = gc.current_equip_level()
    names = ["会意率", "全奇术增伤", "最大外功攻击", "劲", "势"]
    equip = _equip("冠胄", level, [
        (name, float(affix_cap_value(level, name, game_config=gc) or 0))
        for name in names])
    dialog = _dialog(qtbot, equip)

    dialog._btn_chengyin.click()
    result = dialog._build_real_development_data()

    assert result["level"] == level
    assert result["is_chengyin"] is True
    for index, name in enumerate(names, 1):
        cap = affix_cap_value(level, name, chengyin=True, game_config=gc)
        assert result[f"affix_{index}"]["value"] == pytest.approx(cap)
    assert check_real_development(equip, result) is None


def test_in_place_chengyin_state_change_is_a_real_edit(qtbot, gc):
    """数值已经处于承音上限时，单独写入承音状态也不是空操作。"""
    level = gc.current_equip_level()
    names = ["会意率", "全奇术增伤", "最大外功攻击", "劲", "势"]
    equip = _equip("冠胄", level, [
        (name, float(affix_cap_value(
            level, name, chengyin=True, game_config=gc) or 0))
        for name in names
    ])
    dialog = _dialog(qtbot, equip)

    dialog._btn_chengyin.click()
    result = dialog._build_real_development_data()

    assert result["is_chengyin"] is True
    assert dialog._validate_real_development(result) is None


def test_clamping_down_is_allowed_only_at_the_moment_of_chengyin(gc):
    """截断只在承音那一刻合法，之后再降数值仍然是「培养倒退」。"""
    level = gc.current_equip_level()
    cap = affix_cap_value(level, "会意率", game_config=gc)
    chengyin_cap = affix_cap_value(level, "会意率", chengyin=True,
                                   game_config=gc)
    old = _equip("冠胄", level, [("会意率", float(cap or 0))])
    clamped = {**old, "is_chengyin": True,
               "affix_1": {"name": "会意率", "value": float(chengyin_cap or 0)}}

    assert check_real_development(old, clamped) is None

    already = {**clamped}
    lower = {**already,
             "affix_1": {"name": "会意率", "value": float(chengyin_cap or 0) - 1}}
    assert check_real_development(already, lower) == "培养只能提高词条数值"


# ─── 不能承音的情况 ────────────────────────────────────────

def test_button_is_blocked_below_the_season_floor(qtbot, gc):
    """本赛季作废线以下的装备已经不能承音，按钮直接不可用并说明原因。"""
    floor = gc.current_min_chengyin_level()
    assert floor, "当前赛季没有配置最低承音等级，本用例失去意义"
    below = max(
        (c.level for c in gc.get_level_configs() if c.level < floor),
        default=0)
    assert below, "没有低于作废线的已配置等级"

    dialog = _dialog(qtbot, _equip("冠胄", below, [("会意率", 5.0)]))

    assert dialog._btn_chengyin.isEnabled() is False
    assert str(floor) in dialog._btn_chengyin.toolTip()


def test_already_chengyin_equipment_cannot_chengyin_again(qtbot, gc):
    dialog = _dialog(qtbot, _equip(
        "冠胄", gc.current_equip_level(), [("会意率", 5.0)], chengyin=True))

    assert dialog._btn_chengyin.isEnabled() is False
    assert "已经承音" in dialog._btn_chengyin.toolTip()


def test_rules_reject_chengyin_below_the_season_floor(gc):
    floor = gc.current_min_chengyin_level()
    below = max(
        (c.level for c in gc.get_level_configs() if c.level < floor),
        default=0)
    nxt = min(c.level for c in gc.get_level_configs() if c.level > below)
    old = _equip("冠胄", below, [("会意率", 5.0)])
    new = {**old, "level": nxt, "is_chengyin": True}

    assert check_real_development(old, new) == "本赛季该等级的装备已不能承音"


def test_rules_reject_level_increase_without_chengyin(gc):
    """等级提升是承音的结果，不能只改等级而仍保留未承音状态。"""
    old_level = 110
    new_level = min(
        item.level for item in gc.get_level_configs()
        if item.level > old_level
    )
    old = _equip("环", old_level, [
        ("会意率", 5.0), ("最大外功攻击", 100.0), ("劲", 50.0),
        ("势", 50.0), ("敏", 50.0),
    ])
    new = {**old, "level": new_level}

    assert check_real_development(old, new) == "提升装备等级必须同时承音"
