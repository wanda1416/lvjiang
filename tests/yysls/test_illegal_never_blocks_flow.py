"""合法性判定绝不阻断扫描与调律流程

判定结果只是附加信息。创建/编辑模拟装备时可以拦下保存（用户在交互中，
能当场改），但扫描和调律是长流程：一件装备被丢掉或一个异常被抛出，
后果是整轮流程崩坏，远比「留下一件带标记的脏数据」严重。

本文件锁死这条约束，任何让判定影响流程的改动都会在这里失败。
"""

import pytest

from lvjiang.apps.yysls.core.equip_parser import get_equipment_parser
from lvjiang.apps.yysls.core.equip_parser.models import make_fingerprint
from lvjiang.apps.yysls.core.equip_validator import ILLEGAL_KEY


@pytest.fixture
def parser():
    return get_equipment_parser()


def _illegal_raw(**overrides) -> dict:
    """一件必然违规的装备：词条 2-5 里「劲」重复，且首词条数值超上限"""
    raw = {
        "equip_type": "踏雪含光 | 武器·剑",
        "equip_level": "承音 | 110阶",
        "base_attr": "外功攻击 100~232",
        "affix_gong": "最大外功攻击 +999",
        "affix_shang": "劲 +72.2",
        "affix_jue": "劲 +72.2",
        "affix_zhi": "势 +60",
        "affix_yu": "会心率 +7%",
    }
    raw.update(overrides)
    return raw


class TestParseNeverBlocks:
    def test_illegal_equipment_still_parsed(self, parser):
        equip = parser.parse(_illegal_raw())
        assert equip.type == "剑"
        assert equip.name == "踏雪含光"
        assert equip.level == 110
        assert len(equip.affixes) == 5, "违规装备的词条一条都不能少"

    def test_illegal_equipment_is_marked_not_dropped(self, parser):
        equip = parser.parse(_illegal_raw())
        assert equip.extra_data[ILLEGAL_KEY], "应被标记"
        assert equip.affixes[2].name == "劲", "重复的那条也必须原样保留"

    def test_parse_does_not_raise(self, parser):
        parser.parse(_illegal_raw())          # 不抛即通过

    def test_validator_crash_does_not_break_parse(self, parser, monkeypatch):
        """判定器自身出意外时，parse 必须照常返回完整装备。

        上层 to_equipment() 捕获异常后返回空 dict，等于把装备静默丢掉——
        这条用例守的就是这个后果。
        """
        import lvjiang.apps.yysls.core.equip_validator as mod
        def boom(_equip):
            raise RuntimeError("判定器炸了")
        monkeypatch.setattr(mod, "annotate_equipment", boom)

        equip = parser.parse(_illegal_raw())
        assert equip.type == "剑"
        assert len(equip.affixes) == 5
        assert ILLEGAL_KEY not in equip.extra_data

    def test_weird_cap_pct_does_not_raise(self, parser):
        """历史数据里 cap_pct 可能不是数字，判定器宁可漏报也不能抛。"""
        from lvjiang.apps.yysls.core.equip_parser.models import (
            Affix,
            EquipmentData,
        )
        from lvjiang.apps.yysls.core.equip_validator import validate_equipment
        equip = EquipmentData(
            type="剑", name="x", level=110,
            affixes=[Affix(name="最大外功攻击", value=1.0, cap_pct="很高")],  # type: ignore[arg-type]
            dingyin={"name": "外功穿透", "value": 1.0, "cap_pct": None},
        )
        assert validate_equipment(equip) == []


class TestFingerprintUnaffected:
    """指纹不能因为标注而改变，否则去重与存储会错乱。"""

    def test_fingerprint_ignores_illegal_mark(self, parser):
        equip = parser.parse(_illegal_raw())
        with_mark = equip.to_dict()
        equip.extra_data.pop(ILLEGAL_KEY)
        without_mark = equip.to_dict()
        assert with_mark["_fp"] == without_mark["_fp"]
        assert with_mark["_fp"] == make_fingerprint(without_mark)


class TestScanBuiltinNeverDropsEquipment:
    """DSL 内置 to_equipment() 是扫描工作流的入口，异常时会返回空 dict。"""

    def test_illegal_equipment_survives_to_equipment(self):
        from lvjiang.apps.yysls.workflows.builtins.equipment import (
            _to_equipment,
        )
        result = _to_equipment(_illegal_raw())
        assert result, "违规装备不能被扫描入口丢掉"
        assert result["type"] == "剑"
        assert result["_fp"], "指纹必须照常生成，否则背包遍历会漏件"
        assert result["_extra"][ILLEGAL_KEY]


class TestTuningPathNotValidated:
    """调律过程中读单条新词条走 parse_affix_text，不经过合法性判定。"""

    def test_parse_affix_text_has_no_validation(self, parser):
        affix = parser.parse_affix_text("劲 +72.2", 110)
        assert affix is not None
        assert affix.name == "劲"
