"""EquipmentParser（装备 OCR 数据转换器）测试

覆盖 git 历史中多次修复但缺回归保护的解析路径：
- 装备类型行解析（含脏数据、名称推断 fallback、手甲）
- 等级 + 承音标记
- base_attr 武器区间 / 防具单值 / 脏数据容错
- 词条解析（转律标记变体、OCR 纠错、武学增伤动态词条、套装过滤）
- cap_pct 计算与 parse() 整链（品阶推断、级联丢弃 warnings）
"""

import pytest

from lvjiang.apps.yysls.core.equip_parser.models import EquipmentData
from lvjiang.apps.yysls.core.equip_parser.parser import EquipmentParser
from lvjiang.apps.yysls.core.equip_validator import (
    ILLEGAL_KEY,
    illegal_reasons_of,
)
from lvjiang.core.ocr_cleaner import OCRCleaner


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


@pytest.fixture(autouse=True)
def reset_cleaner():
    """每个测试前重置单例，确保加载最新配置"""
    OCRCleaner.reset_instance()


def clean(text: str) -> str:
    """模拟 OCR 引擎清洗"""
    return OCRCleaner().clean(text)


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
        ("雁南飞甲", "胸甲"),
        ("吴钩缚袴", "胫甲"),
        ("吴钩披膊", "腕甲"),
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

    def test_c_has_priority_and_conflict_is_logged(self, parser, monkeypatch):
        errors = []
        monkeypatch.setattr(
            "lvjiang.apps.yysls.core.equip_parser.parser.logger.error",
            errors.append,
        )
        assert parser._parse_equip_type("雁南飞冠 | 胸甲") == (
            "雁南飞冠", "胸甲")
        assert errors and "装备类型冲突" in errors[0]

    def test_missing_c_falls_back_to_b(self, parser):
        assert parser._parse_equip_type("雁南飞甲") == ("雁南飞甲", "胸甲")

    @pytest.mark.parametrize("raw,expected_type", [
        ("吴钩霜甲 | 胸申", "胸甲"),   # "甲"错识别，"胸"单字命中
        ("雁南飞 | 寇胄", "冠胄"),     # "冠"错识别，"胄"单字命中
        ("流星珑 | 环", "环"),
        ("玄玉 | 佩", "佩"),
    ])
    def test_type_segment_single_char_hit(self, parser, raw, expected_type):
        # 类型段单字足以说明部位，容忍 OCR 错字
        assert parser._parse_equip_type(raw)[1] == expected_type


# ─── equip_level 解析 ──────────────────────────────────────

class TestParseEquipLevel:
    def test_chengyin_level(self, parser):
        assert parser._parse_equip_level("承音 | 110阶") == (110, True)

    def test_plain_level(self, parser):
        assert parser._parse_equip_level("100阶") == (100, False)

    def test_empty(self, parser):
        assert parser._parse_equip_level("") == (0, False)

    def test_chengyin_without_level(self, parser):
        assert parser._parse_equip_level("承音") == (0, True)


class TestOriginalLevel:
    @pytest.mark.parametrize("raw,expected", [
        ("流星云珑 | 环", 110),
        ("吴钩霜甲 | 胸甲", 110),
        ("踏雪含光 | 武器·剑", 105),
        ("雁南飞冠 | 冠胄", 105),
        ("未知冠 | 冠胄", 0),
    ])
    def test_parse_original_level_from_tier_name_only(
            self, parser, raw, expected):
        equip = parser.parse({"equip_type": raw, "equip_level": "110阶"})
        assert equip.original_level == expected

    def test_original_level_roundtrip_and_legacy_backfill(self):
        equip = EquipmentData(name="流星云珑", original_level=110)
        restored = EquipmentData.from_dict(equip.to_dict(include_fp=False))
        assert restored.original_level == 110

        legacy = equip.to_dict(include_fp=False)
        legacy.pop("original_level")
        assert EquipmentData.from_dict(legacy).original_level == 110


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
        "[转]会心率 5.6%",   # 全角括号清洗后
        "[转1会心率 5.6%",   # 87cb2b3：OCR 将 ] 误识别为 1
        "[转劲62.8",         # OCR 漏识别闭合括号
    ])
    def test_transfer_mark_variants(self, parser, text):
        affix = parser._parse_single_affix(clean(text))
        if "劲" in text:
            # 劲词条（转劲 = 转律后的劲属性）
            assert affix.name == "劲"
        else:
            assert affix.name == "会心率"
        assert affix.is_transferred

    def test_wuxue_dynamic_affix(self, parser):
        affix = parser._parse_single_affix("剑武学增伤 8.2%")
        assert affix.name == "剑武学增伤"
        assert affix.value == 8.2

    def test_ocr_correction_jingzhun(self, parser):
        # 猜准率 → 精准率（由 OCR 引擎清洗）
        affix = parser._parse_single_affix(clean("猜准率 10.8%"))
        assert affix.name == "精准率"

    def test_noise_char_jian_removed(self, parser):
        # 荐 噪声由 OCR 引擎删除
        affix = parser._parse_single_affix(clean("荐会心率 5%"))
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
        assert equip.quality == "gold"       # 110 阶武器区间 [100,232] 精确等于 gold
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

    def test_level_recovered_from_base_attr_when_ocr_missing(self, parser):
        # equip_level OCR 漏识别 → 基础属性值全局唯一，反查回填 等级+品阶
        # base_attr 区间 [100,232] 精确等于 剑 110 阶 gold → 回填 level=110, quality=gold
        equip = parser.parse(_weapon_raw(equip_level=""))
        assert equip.level == 110
        assert equip.quality == "gold"
        # 等级回填后 cap_pct 也能正常计算（后续链路受益）
        assert equip.affixes[0].cap_pct == 100.0
        assert equip.affixes[1].cap_pct == 50.0

    def test_chest_name_suffix_is_recognized_without_base_attr_backfill(self, parser):
        # 胸甲名称固定以“甲”结尾，直接由名称识别；这不是基础属性反推。
        equip = parser.parse({
            "equip_type": "吴钩霜甲",
            "equip_level": "110阶",
            "base_attr": "气血最大值 19445",
            "affix_gong": "劲 +76.8",
        })
        assert equip.type == "胸甲"
        assert equip.quality == "gold"

    def test_type_none_when_unrecognized(self, parser):
        # equip_type 未识别到类型关键字 → type 为 None
        equip = parser.parse({
            "equip_type": "雁南飞",
            "equip_level": "110阶",
            "base_attr": "气血最大值 7778",
            "affix_gong": "劲 +76.8",
        })
        assert equip.type is None
        assert equip.quality == "blue"


# ─── 合法性判定接入（parse 后自动标记状态异常） ──────────────

class TestIllegalAnnotation:
    """parse() 解析完调用全局判定器，把游戏产不出的组合标进 _extra。

    判定逻辑本身在 tests/yysls/test_equip_validator.py 里测，这里只验证
    「parse 确实调了、结果确实落到了 extra_data」这条接线。
    """

    def test_duplicate_affix_marked(self, parser):
        equip = parser.parse(_weapon_raw(
            affix_jue="劲 +72.2", affix_zhi="劲 +72.2"))
        reasons = equip.extra_data.get(ILLEGAL_KEY)
        assert reasons, "词条 2-5 重复应被标记"
        assert any("重复" in r for r in reasons)

    def test_over_cap_value_marked(self, parser):
        """121.4 是 110 阶最大外功攻击的上限，给个更大的值必然超上限。"""
        equip = parser.parse(_weapon_raw(affix_gong="最大外功攻击 +200"))
        reasons = equip.extra_data.get(ILLEGAL_KEY)
        assert reasons
        assert any("上限" in r for r in reasons)

    def test_normal_equipment_not_marked(self, parser):
        equip = parser.parse(_weapon_raw())
        assert ILLEGAL_KEY not in equip.extra_data

    def test_mark_reaches_json(self, parser):
        equip = parser.parse(_weapon_raw(
            affix_jue="劲 +72.2", affix_zhi="劲 +72.2"))
        assert illegal_reasons_of(equip.to_dict())

    def test_affix_data_not_dropped(self, parser):
        """只标注不丢数据：异常装备的词条必须原样保留，交给用户校正。"""
        equip = parser.parse(_weapon_raw(
            affix_jue="劲 +72.2", affix_zhi="劲 +72.2"))
        assert [a.name for a in equip.affixes] == [
            "最大外功攻击", "会心率", "劲", "劲"]


# ─── 界面语言切换不影响 OCR 匹配 ──────────────────────────────

class TestOcrMatchingSurvivesTranslation:
    """本模块解析的是游戏截屏 OCR 文字，恒为中文，不随本软件界面语言变化。

    曾经这里大量字面量被误包了 tr()：只要对应词条被翻译，切到英文界面
    就会拿翻译后的英文去匹配游戏截屏里恒定的中文，永远匹配不上，导致
    装备类型/等级/承音标记/词条全部解析失败。

    用 monkeypatch 强行让 tr() 对这些词返回一个明显不同的英文值，
    模拟"已翻译"状态，验证匹配側已经不再经过 tr()。
    """

    @pytest.fixture(autouse=True)
    def force_translated(self, monkeypatch):
        import lvjiang.i18n as i18n
        fake = {
            "武器": "WEAPON", "冠": "CROWN", "胄": "HELM",
            "胸": "CHEST", "胫": "SHIN", "腕": "WRIST",
            "环": "RING", "佩": "PENDANT", "云珑": "YUNLONG",
            "辟邪": "BIXIE", "承音": "CHENGYIN", "套装": "SET",
            "外功防御": "EXT_DEF",
        }
        monkeypatch.setattr(i18n, "_current_language", "en_US")
        monkeypatch.setattr(i18n, "_translations", fake)

    def test_weapon_type_still_recognized(self, parser):
        assert parser._parse_equip_type("踏雪含光 | 武器·剑") == ("踏雪含光", "剑")

    def test_armor_type_from_name_still_recognized(self, parser):
        # 配置中的游戏原始词及规范类型都不经界面翻译。
        assert parser._parse_equip_type("流星云珑") == ("流星云珑", "环")
        assert parser._parse_equip_type("玄玉辟邪") == ("玄玉辟邪", "佩")

    def test_armor_type_segment_still_recognized(self, parser):
        assert parser._parse_equip_type("雁南飞冠 | 冠胄") == ("雁南飞冠", "冠胄")

    def test_chengyin_level_still_recognized(self, parser):
        assert parser._parse_equip_level("承音 | 110阶") == (110, True)

    def test_base_attr_2_still_recognized(self, parser):
        attr = parser._parse_base_attr("外功防御 500", is_base_attr_2=True)
        assert attr is not None
        assert attr.name == "外功防御"
        assert attr.value == 500

    def test_set_info_still_filtered_out(self, parser):
        assert parser._parse_single_affix("弓玦套装") is None
