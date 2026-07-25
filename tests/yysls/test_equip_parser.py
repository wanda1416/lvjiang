"""EquipmentParser（装备 OCR 数据转换器）测试

覆盖 git 历史中多次修复但缺回归保护的解析路径：
- 装备类型行解析（含脏数据、名称推断 fallback、手甲）
- 等级 + 承音标记
- base_attr 武器区间 / 防具单值 / 脏数据容错
- 词条解析（转律标记变体、OCR 纠错、武学增伤动态词条、套装过滤）
- cap_pct 计算与 parse() 整链（品阶推断、级联丢弃 warnings）
"""

import pytest

from src.apps.yysls.equip_parser.parser import EquipmentParser


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


# ─── equip_type 解析 ──────────────────────────────────────

class TestParseEquipType:
    def test_standard_weapon(self, parser):
        assert parser._parse_equip_type("踏雪含光 | 武器·剑") == ("踏雪含光", "剑")

    def test_standard_armor(self, parser):
        assert parser._parse_equip_type("雁南飞冠 | 冠胄") == ("雁南飞冠", "冠胄")

    def test_shoujia(self, parser):
        # 66a6a73 手甲术语修正
        assert parser._parse_equip_type("铁纱 | 武器·手甲") == ("铁纱", "手甲")

    def test_dirty_extra_segment(self, parser):
        # OCR 多切出一段脏数据，类型仍从最后一段提取
        assert parser._parse_equip_type("江无浪· | 一杆 | 武器·枪") == ("江无浪", "枪")

    @pytest.mark.parametrize("name,expected", [
        ("流星云珑", "环"),
        ("玄玉辟邪", "佩"),
        ("雁南飞冠", "冠胄"),
        ("寒山胸甲", "胸甲"),
        ("寒山胫甲", "胫甲"),
        ("寒山腕甲", "腕甲"),
    ])
    def test_infer_type_from_name(self, parser, name, expected):
        # 无类型段时按名称关键字推断
        assert parser._parse_equip_type(name) == (name, expected)

    def test_empty(self, parser):
        assert parser._parse_equip_type("") == (None, None)

    def test_unrecognizable_returns_none_type(self, parser):
        name, equip_type = parser._parse_equip_type("神秘物品 | 一杆")
        assert name == "神秘物品"
        assert equip_type is None


# ─── equip_level 解析 ──────────────────────────────────────

class TestParseEquipLevel:
    def test_chengyin_level(self, parser):
        assert parser._parse_equip_level("承音 | 110阶") == (110, True)

    def test_plain_level(self, parser):
        assert parser._parse_equip_level("100阶") == (100, False)

    def test_empty(self, parser):
        assert parser._parse_equip_level("") == (None, False)

    def test_chengyin_without_level(self, parser):
        assert parser._parse_equip_level("承音") == (None, True)


# ─── base_attr 解析 ────────────────────────────────────────

class TestParseBaseAttr:
    def test_weapon_range(self, parser):
        attr = parser._parse_base_attr("外功攻击 87~203")
        assert attr.name == "外功攻击"
        assert attr.value == [87, 203]

    def test_armor_single_value(self, parser):
        attr = parser._parse_base_attr("气血最大值 8750")
        assert attr.name == "气血最大值"
        assert attr.value == 8750

    def test_dirty_range_still_extracts_numbers(self, parser):
        # 67b4117 装备解析纠错：名称混入 OCR 噪声不影响区间提取
        attr = parser._parse_base_attr("外功攻击 老著 52~121")
        assert attr.value == [52, 121]
        assert attr.name.startswith("外功攻击")

    def test_unknown_name_fallback_last_number(self, parser):
        attr = parser._parse_base_attr("神秘属性 123")
        assert attr.name == "神秘属性"
        assert attr.value == 123

    def test_empty_and_garbage(self, parser):
        assert parser._parse_base_attr("") is None
        assert parser._parse_base_attr("纯噪声无数字") is None


# ─── 单条词条解析 ──────────────────────────────────────────

class TestParseSingleAffix:
    def test_percent_affix(self, parser):
        affix = parser._parse_single_affix("会心率 +5.6%")
        assert affix.name == "会心率"
        assert affix.value == 5.6
        assert affix.unit == "%"
        assert not affix.is_transferred

    def test_flat_affix_no_unit(self, parser):
        affix = parser._parse_single_affix("最大外功攻击 +110")
        assert affix.name == "最大外功攻击"
        assert affix.value == 110
        assert affix.unit is None

    @pytest.mark.parametrize("text", [
        "[转]会心率 5.6%",
        "［转］会心率 5.6%",
        "[转1会心率 5.6%",   # 87cb2b3：OCR 将 ] 误识别为 1
    ])
    def test_transfer_mark_variants(self, parser, text):
        affix = parser._parse_single_affix(text)
        assert affix.name == "会心率"
        assert affix.is_transferred

    def test_wuxue_dynamic_affix(self, parser):
        affix = parser._parse_single_affix("剑武学增伤 8.2%")
        assert affix.name == "剑武学增伤"
        assert affix.value == 8.2

    def test_ocr_correction_jingzhun(self, parser):
        # 猜准率 → 精准率
        affix = parser._parse_single_affix("猜准率 10.8%")
        assert affix.name == "精准率"

    def test_noise_char_jian_removed(self, parser):
        affix = parser._parse_single_affix("荐会心率 5%")
        assert affix.name == "会心率"

    def test_suit_info_filtered(self, parser):
        assert parser._parse_single_affix("寒山套装(2/2)") is None

    def test_unknown_affix_returns_none(self, parser):
        assert parser._parse_single_affix("未知词条 5") is None
        assert parser._parse_single_affix("") is None

    def test_parse_affix_text_with_cap_pct(self, parser):
        # 180bbfe：cap_pct = value / cap * 100（110 阶会心率上限 14%）
        affix = parser.parse_affix_text("会心率 +7%", level=110)
        assert affix.cap_pct == 50.0

    def test_parse_affix_text_without_level(self, parser):
        affix = parser.parse_affix_text("会心率 +7%")
        assert affix.cap_pct is None


# ─── parse() 整链 ──────────────────────────────────────────

def _weapon_raw(**overrides) -> dict:
    raw = {
        "equip_type": "踏雪含光 | 武器·剑",
        "equip_level": "承音 | 110阶",
        "base_attr": "外功攻击 100~232",
        "affix_gong": "最大外功攻击 +121.4",
        "affix_shang": "会心率 +7%",
        "affix_jue": "",
        "affix_zhi": "",
        "affix_yu": "",
    }
    raw.update(overrides)
    return raw


class TestParseFullChain:
    def test_weapon_gold_quality(self, parser):
        equip = parser.parse(_weapon_raw())
        assert equip.name == "踏雪含光"
        assert equip.type == "剑"
        assert equip.level == 110
        assert equip.is_chengyin
        assert equip.quality == "gold"       # 110 阶武器 max 232 落在 gold 区间
        assert equip.extra_data["affix_count"] == 2

    def test_cap_pct_computed(self, parser):
        equip = parser.parse(_weapon_raw())
        # 110 阶外功攻击上限 121.4 → 满词条 100%
        assert equip.affixes[0].cap_pct == 100.0
        assert equip.affixes[1].cap_pct == 50.0

    def test_armor_purple_quality(self, parser):
        equip = parser.parse({
            "equip_type": "雁南飞冠 | 冠胄",
            "equip_level": "110阶",
            "base_attr": "气血最大值 8750",
            "affix_gong": "劲 +76.8",
        })
        assert equip.quality == "purple"     # head 110 purple=8750
        assert not equip.is_chengyin

    def test_cascade_discard_on_middle_empty(self, parser):
        # 第 2~4 条任一为空 → 后续全部丢弃并记录 warning
        equip = parser.parse(_weapon_raw(
            affix_shang="", affix_jue="会心率 +7%",
        ))
        assert len(equip.affixes) == 1
        assert any("丢弃" in w for w in equip.warnings)

    def test_gong_empty_means_ocr_failure(self, parser):
        equip = parser.parse(_weapon_raw(affix_gong="", affix_shang=""))
        assert equip.affixes == []
        assert any("完全失败" in w for w in equip.warnings)

    def test_yu_empty_is_normal(self, parser):
        # 前 4 条齐全、第 5 条为空 → 正常结束无 warning
        equip = parser.parse(_weapon_raw(
            affix_jue="精准率 +12.4%", affix_zhi="劲 +76.8",
        ))
        assert len(equip.affixes) == 4
        assert equip.warnings == []

    def test_no_level_no_quality_no_cap_pct(self, parser):
        equip = parser.parse(_weapon_raw(equip_level=""))
        assert equip.quality is None
        assert all(a.cap_pct is None for a in equip.affixes)
