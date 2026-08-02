"""DingyinParser（定音词条解析器）测试

- 候选池 = 外功增益/属攻增益 + 指定技能增效（十大流派 × 5）全量
  （词条名全局唯一，匹配不依赖装备部位）
- EquipmentParser 委托整链（dingyin 字段解析 + 无法解析 warning）
"""

import pytest

from lvjiang.apps.yysls.equip_parser.dingyin_parser import DingyinParser
from lvjiang.apps.yysls.equip_parser.parser import EquipmentParser


@pytest.fixture(scope="module")
def dp():
    return DingyinParser()


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


# ─── 候选词条池 ────────────────────────────────────────────

class TestCandidates:
    def test_full_pool(self, dp):
        # 全量池 = 增益类 3 条 + 指定技能增效 52 条
        names = set(dp._candidates())
        assert {"外功穿透", "外功抗性", "无相穿透"} <= names
        assert "无名剑法武学技增伤" in names
        assert "千机索天重击增伤" in names
        assert len(names) == 55


# ─── 解析 ─────────────────────────────────────────────────

class TestParse:
    @pytest.mark.parametrize("text,name,value", [
        ("外功穿透 +14.2%", "外功穿透", 14.2),
        ("外功抗性+9.6%", "外功抗性", 9.6),
        ("无相穿透 12.8", "无相穿透", 12.8),
        ("无名剑法武学技增伤 +8.0%", "无名剑法武学技增伤", 8.0),
        ("积矩九剑流血增伤+6.4%", "积矩九剑流血增伤", 6.4),
        ("明川药典治疗技增疗 7.2%", "明川药典治疗技增疗", 7.2),
    ])
    def test_both_sides(self, dp, text, name, value):
        assert dp.parse(text) == {"name": name, "value": value}

    def test_prefix_noise_substring_match(self, dp):
        # OCR 前缀噪声 → 子串匹配兜底
        result = dp.parse("荐外功穿透 +14.2%")
        assert result == {"name": "外功穿透", "value": 14.2}

    def test_cleaner_and_dot_normalized(self, dp):
        # 真实脏数据：含噪声字符"荐" + 游戏内 武学·技能 间隔号
        result = dp.parse("明川药典·治疗技增疗荐7.5%")
        assert result == {"name": "明川药典治疗技增疗", "value": 7.5}

    def test_empty_and_garbage(self, dp):
        assert dp.parse("") is None
        assert dp.parse("纯噪声文本") is None

    def test_name_without_value(self, dp):
        assert dp.parse("外功穿透") is None


# ─── EquipmentParser 委托整链 ──────────────────────────────

class TestParserDelegation:
    # 满词条测试数据（5 条词条才可能有定音）
    _FULL_AFFIXES = {
        "affix_gong": "最大外功攻击 +121.4",
        "affix_shang": "劲 +50.0",
        "affix_jue": "敏 +40.0",
        "affix_zhi": "气血最大值 +500",
        "affix_yu": "外功防御 +30.0",
    }

    def test_weapon_dingyin_parsed(self, parser):
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "承音 | 110阶",
            "base_attr": "外功攻击 100~232",
            **self._FULL_AFFIXES,
            "dingyin": "外功穿透 +14.2%",
        })
        assert equip.dingyin == {"name": "外功穿透", "value": 14.2}
        assert equip.to_dict()["dingyin"] == {"name": "外功穿透", "value": 14.2}

    def test_armor_dingyin_parsed(self, parser):
        equip = parser.parse({
            "equip_type": "雁南飞冠 | 冠胄",
            "equip_level": "110阶",
            "base_attr": "气血最大值 8750",
            "affix_gong": "劲 +76.8",
            "affix_shang": "敏 +50.0",
            "affix_jue": "气血最大值 +300",
            "affix_zhi": "外功防御 +40.0",
            "affix_yu": "体 +20.0",
            "dingyin": "千机索天重击增伤 +6.4%",
        })
        assert equip.dingyin == {"name": "千机索天重击增伤", "value": 6.4}

    def test_no_dingyin_field(self, parser):
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "110阶",
            "base_attr": "外功攻击 100~232",
            "affix_gong": "最大外功攻击 +121.4",
        })
        assert equip.dingyin == {}
        assert equip.to_dict()["dingyin"] is None

    def test_unparsable_dingyin_warns(self, parser):
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "110阶",
            "base_attr": "外功攻击 100~232",
            **self._FULL_AFFIXES,
            "dingyin": "乱码噪声",
        })
        assert equip.dingyin == {}
        assert any("定音词条无法解析" in w for w in equip.warnings)

    def test_affixes_less_than_5_skips_dingyin(self, parser):
        """词条不满 5 个时，即使有 dingyin 字段也跳过解析"""
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "110阶",
            "base_attr": "外功攻击 100~232",
            "affix_gong": "最大外功攻击 +121.4",
            "affix_shang": "劲 +50.0",
            # 只有 2 条词条，不满 5 条
            "dingyin": "外功穿透 +14.2%",
        })
        assert equip.dingyin == {}  # 不应解析定音
        assert len(equip.affixes) == 2
