"""全局装备合法性判定器（equip_validator）测试

判定的是「游戏能不能产出这样一件装备」，不是「这件装备好不好」。
依据见 docs/10-game/01-equipment-system.md「调律核心规则（两大铁律）」
与「神力词条」两节。用仓库里真实的 game_config.yaml，不 mock。
"""

from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.equip_validator import (
    CODE_ATTACK_OVERFLOW,
    CODE_CAP_OVERFLOW,
    CODE_DIVINE_OVERFLOW,
    CODE_DUPLICATE_AFFIX,
    CODE_INVALID_AFFIX_PART,
    CODE_INVALID_FIRST_AFFIX,
    CODE_MALFORMED_AFFIX,
    CODE_TRANSFERRED_DIVINE,
    CODE_UNKNOWN_AFFIX,
    CODE_UNKNOWN_EQUIP_TYPE,
    CODE_WEAPON_AFFIX_MISMATCH,
    ILLEGAL_KEY,
    annotate_equipment,
    illegal_reasons_of,
    validate_equipment,
    validate_equipment_dict,
)

# 110 级上限（game_config.yaml）：超上限用例按真实数值构造，不靠 cap_pct。
_CAP_110 = {"最大外功攻击": 121.4, "劲": 76.8}


def _equip(names, *, part="环", values=None, transferred=(), dingyin=None):
    """按顺序构造词条；values 为与 names 等长的真实数值列表。

    刻意不设 cap_pct：它是给调律 DSL 快查的派生缓存，判定器必须现算，
    传了也不该被采信。
    """
    affixes = []
    for i, n in enumerate(names):
        affixes.append(Affix(
            name=n, value=(values[i] if values else 1.0),
            is_transferred=(i in transferred),
        ))
    return EquipmentData(type=part, name="测试装备", level=110, quality="gold",
                         affixes=affixes, dingyin=dingyin or {})


def _codes(equip):
    return [r.code for r in validate_equipment(equip)]


class TestLegalEquipment:
    def test_normal_equipment_has_no_reason(self):
        assert _codes(_equip(
            ["最大外功攻击", "全武学增效", "劲", "势", "会意率"])) == []

    def test_affix_may_repeat_first(self):
        """词条 2-5 允许与首词条相同：首词条是初始词条，不由调律产出。"""
        assert _codes(_equip(
            ["最大外功攻击", "最大外功攻击", "势", "会意率", "全武学增效"])) == []

    def test_two_distinct_attack_affixes_allowed(self):
        assert _codes(_equip(
            ["最大外功攻击", "最大鸣金攻击", "最小鸣金攻击", "劲", "势"])) == []

    def test_incomplete_equipment_allowed(self):
        """词条不满 5 条（调律尚未调满）本身不是异常。"""
        assert _codes(_equip(["最大外功攻击", "劲"])) == []


class TestDuplicateAffix:
    def test_all_same_affix_flagged(self):
        codes = _codes(_equip(["最大外功攻击"] * 5))
        assert CODE_DUPLICATE_AFFIX in codes

    def test_two_identical_flagged(self):
        codes = _codes(_equip(
            ["最大外功攻击", "劲", "劲", "会意率", "全武学增效"]))
        assert codes == [CODE_DUPLICATE_AFFIX]

    def test_each_duplicate_name_reported_once(self):
        """同一个名字重复 3 次只报一条，不刷屏。"""
        reasons = validate_equipment(
            _equip(["势", "劲", "劲", "劲", "全武学增效"]))
        dups = [r for r in reasons if r.code == CODE_DUPLICATE_AFFIX]
        assert len(dups) == 1
        assert "3 次" in dups[0].message


class TestCountRules:
    def test_three_attack_affixes_flagged(self):
        codes = _codes(_equip(
            ["劲", "最大鸣金攻击", "最小鸣金攻击", "最大裂石攻击", "势"]))
        assert CODE_ATTACK_OVERFLOW in codes

    def test_two_divine_affixes_flagged(self):
        codes = _codes(_equip(
            ["劲", "全武学增效", "对首领单位增伤", "势", "会意率"]))
        assert CODE_DIVINE_OVERFLOW in codes

    def test_transferred_divine_flagged(self):
        codes = _codes(_equip(
            ["劲", "全武学增效", "势", "会意率"], transferred=(1,)))
        assert CODE_TRANSFERRED_DIVINE in codes

    def test_transferred_normal_affix_is_fine(self):
        """普通词条由转律产出完全正常，只有神力词条不行。"""
        codes = _codes(_equip(
            ["最大外功攻击", "会意率", "势", "劲"], transferred=(1,)))
        assert codes == []

    def test_count_rule_reported_before_duplicate(self):
        """两条神力恰好选了同一个：先报「神力最多 1 条」，更贴近要改的那一步。"""
        reasons = validate_equipment(
            _equip(["最大外功攻击", "全武学增效", "全武学增效", "势", "会意率"]))
        assert reasons[0].code == CODE_DIVINE_OVERFLOW
        assert CODE_DUPLICATE_AFFIX in [r.code for r in reasons]


class TestCapOverflow:
    def test_affix_over_cap_flagged(self):
        codes = _codes(_equip(
            ["最大外功攻击", "劲"],
            values=[_CAP_110["最大外功攻击"], _CAP_110["劲"] * 1.183]))
        assert codes == [CODE_CAP_OVERFLOW]

    def test_first_affix_over_cap_also_flagged(self):
        """首词条不受组合类规则约束，但数值超上限同样是异常。"""
        codes = _codes(_equip(
            ["最大外功攻击", "劲"],
            values=[_CAP_110["最大外功攻击"] * 1.4, _CAP_110["劲"] * 0.9]))
        assert codes == [CODE_CAP_OVERFLOW]

    def test_exactly_at_cap_is_fine(self):
        assert _codes(_equip(
            ["最大外功攻击"], values=[_CAP_110["最大外功攻击"]])) == []

    def test_display_rounding_at_cap_is_not_overflow(self):
        """游戏只显示 1 位小数，正好顶满的词条不能因半步误差被误报。"""
        assert _codes(_equip(
            ["最大外功攻击"], values=[_CAP_110["最大外功攻击"] + 0.04])) == []

    def test_stale_cap_pct_is_ignored_in_both_directions(self):
        """cap_pct 与 value 脱节时一律以现算为准。"""
        over = _equip(["最大外功攻击"],
                      values=[_CAP_110["最大外功攻击"] * 1.2])
        over.affixes[0].cap_pct = 90.8      # 陈旧缓存说没超
        assert _codes(over) == [CODE_CAP_OVERFLOW]

        fine = _equip(["最大外功攻击"], values=[_CAP_110["最大外功攻击"]])
        fine.affixes[0].cap_pct = 130.0     # 陈旧缓存说超了
        assert _codes(fine) == []

    def test_dingyin_is_not_part_of_equipment_validation(self):
        equip = _equip(["最大外功攻击"],
                       dingyin={"name": "外功穿透", "value": 20.0, "cap_pct": 119.0})
        assert _codes(equip) == []


class TestSlotAwareness:
    """槽位号必须按 affix_N 键取，不能用列表下标——中间槽可能缺失。"""

    def test_missing_first_slot_does_not_shift_others(self):
        d = {
            "type": "环",
            "affix_2": {"name": "劲", "value": 1.0},
            "affix_3": {"name": "劲", "value": 1.0},
        }
        assert [r.code for r in validate_equipment_dict(d)] == [CODE_DUPLICATE_AFFIX]

    def test_only_second_slot_filled_is_legal(self):
        d = {"type": "环", "affix_2": {"name": "劲", "value": 1.0}}
        assert validate_equipment_dict(d) == []


class TestAnnotate:
    def test_annotate_writes_reasons(self):
        equip = _equip(["最大外功攻击", "劲", "劲"])
        annotate_equipment(equip)
        assert equip.extra_data[ILLEGAL_KEY]
        assert "重复" in equip.extra_data[ILLEGAL_KEY][0]

    def test_annotate_clears_stale_mark(self):
        """重新判定为合法时必须移除旧标记，不能留着误报。"""
        equip = _equip(["最大外功攻击", "劲", "会意率"])
        equip.extra_data[ILLEGAL_KEY] = ["旧的异常"]
        annotate_equipment(equip)
        assert ILLEGAL_KEY not in equip.extra_data

    def test_mark_survives_serialization(self):
        equip = _equip(["势", "劲", "劲"])
        annotate_equipment(equip)
        d = equip.to_dict()
        assert illegal_reasons_of(d)
        assert illegal_reasons_of(EquipmentData.from_dict(d).to_dict())

    def test_legal_equipment_has_no_mark(self):
        equip = _equip(["最大外功攻击", "劲", "会意率"])
        annotate_equipment(equip)
        assert not illegal_reasons_of(equip.to_dict())


class TestConfiguredLegality:
    def test_unknown_equipment_type(self):
        d = {"type": "长剑", "affix_1": {"name": "最大外功攻击", "value": 1.0}}
        assert CODE_UNKNOWN_EQUIP_TYPE in [
            r.code for r in validate_equipment_dict(d)]

    def test_unknown_affix(self):
        d = {"type": "环", "affix_1": {"name": "会心牢", "value": 1.0}}
        assert CODE_UNKNOWN_AFFIX in [r.code for r in validate_equipment_dict(d)]

    def test_invalid_first_affix(self):
        d = {"type": "环", "affix_1": {"name": "会心率", "value": 1.0}}
        assert [r.code for r in validate_equipment_dict(d)] == [
            CODE_INVALID_FIRST_AFFIX]

    def test_invalid_affix_part(self):
        d = {
            "type": "环",
            "affix_1": {"name": "最大外功攻击", "value": 1.0},
            "affix_2": {"name": "单体类奇术增伤", "value": 1.0},
        }
        assert CODE_INVALID_AFFIX_PART in [
            r.code for r in validate_equipment_dict(d)]

    def test_weapon_affix_must_match_weapon_type(self):
        d = {
            "type": "剑",
            "affix_1": {"name": "最大外功攻击", "value": 1.0},
            "affix_2": {"name": "枪武学增伤", "value": 1.0},
        }
        assert CODE_WEAPON_AFFIX_MISMATCH in [
            r.code for r in validate_equipment_dict(d)]

    def test_matching_weapon_affix_is_legal(self):
        d = {
            "type": "剑",
            "affix_1": {"name": "最大外功攻击", "value": 1.0},
            "affix_2": {"name": "剑武学增伤", "value": 1.0},
        }
        assert validate_equipment_dict(d) == []

    @pytest.mark.parametrize("affix", ["bad", {"name": "劲"}, {"value": 1.0}])
    def test_malformed_affix(self, affix):
        d = {"type": "环", "affix_1": affix}
        assert CODE_MALFORMED_AFFIX in [
            r.code for r in validate_equipment_dict(d)]


class TestIllegalReasonsOf:
    @pytest.mark.parametrize("extra,expected", [
        (None, []),
        ({}, []),
        ({ILLEGAL_KEY: []}, []),
        ({ILLEGAL_KEY: ["a", "b"]}, ["a", "b"]),
        ({ILLEGAL_KEY: "a,b"}, ["a", "b"]),        # 兼容历史的逗号串写法
        ({ILLEGAL_KEY: "a, ,b"}, ["a", "b"]),
    ])
    def test_reads_various_shapes(self, extra, expected):
        d = {"_extra": extra} if extra is not None else {}
        assert illegal_reasons_of(d) == expected


class TestHistoricalDiscovery:
    def test_inventory_reload_refreshes_existing_marks(self):
        from lvjiang.apps.yysls.core.combat.equipment import EquipmentInventory
        from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
            ZHIGE_DINGYIN_KEY,
        )

        equip = {
            "type": "环",
            "affix_1": {"name": "会心率", "value": 1.0},
            "dingyin": {"name": "止戈特殊效果", "value": 999},
            "_extra": {},
        }
        state = SimpleNamespace(equipment_items={"fp": equip})
        inventory = EquipmentInventory.__new__(EquipmentInventory)
        inventory._repo = SimpleNamespace(load=lambda: state)

        inventory.reload()

        assert ILLEGAL_KEY in equip["_extra"]
        assert equip["_extra"][ZHIGE_DINGYIN_KEY] is True

    def test_one_dirty_item_does_not_skip_later_items(self, monkeypatch):
        from lvjiang.apps.yysls.core import equip_validator
        from lvjiang.apps.yysls.core.combat.equipment import EquipmentInventory
        from lvjiang.apps.yysls.core.equip_parser import dingyin_parser

        bad = {"bad": True}
        good: dict = {}
        state = SimpleNamespace(equipment_items={"bad_fp": bad, "good_fp": good})
        inventory = EquipmentInventory.__new__(EquipmentInventory)
        inventory._repo = SimpleNamespace(load=lambda: state)

        def audit(equip):
            if equip.get("bad"):
                raise ValueError("historical dirt")
            equip["audited"] = True
            return []

        monkeypatch.setattr(equip_validator, "annotate_equipment_dict", audit)
        monkeypatch.setattr(
            dingyin_parser, "refresh_dingyin_marker_dict",
            lambda equip: equip.__setitem__("dingyin_refreshed", True) or False,
        )

        inventory.reload()

        assert good == {"audited": True, "dingyin_refreshed": True}
