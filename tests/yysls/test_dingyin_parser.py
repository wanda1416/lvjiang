"""DingyinParser（定音词条解析器）测试

- 左四（武器/环/佩）候选池：外功增益/属攻增益 原始词条名
- 右四（防具）候选池：指定技能增效 十大流派 × 5 = 50 条
- EquipmentParser 委托整链（dingyin 字段解析 + 无法解析 warning）
"""

import pytest

from src.apps.yysls.equip_parser.dingyin_parser import DingyinParser
from src.apps.yysls.equip_parser.parser import EquipmentParser


@pytest.fixture(scope="module")
def dp():
    return DingyinParser()


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


# ─── 候选词条池 ────────────────────────────────────────────

class TestCandidates:
    def test_left_pool(self, dp):
        # 左四 = 外功增益 + 属攻增益 的原始词条名
        names = set(dp._candidates("weapon"))
        assert names == {"外功穿透", "外功抗性", "属攻穿透"}
        assert set(dp._candidates("jewelry")) == names

    def test_right_pool_50_names(self, dp):
        names = dp._candidates("armor")
        assert len(names) == 50
        assert "无名剑法武学技增伤" in names
        assert "千机索天重击增伤" in names

    def test_unknown_category_empty(self, dp):
        assert dp._candidates("unknown") == []


# ─── 解析 ─────────────────────────────────────────────────

class TestParse:
    @pytest.mark.parametrize("text,name,value", [
        ("外功穿透 +14.2%", "外功穿透", 14.2),
        ("外功抗性+9.6%", "外功抗性", 9.6),
        ("属攻穿透 12.8", "属攻穿透", 12.8),
    ])
    def test_weapon_side(self, dp, text, name, value):
        assert dp.parse(text, "weapon") == {"name": name, "value": value}

    @pytest.mark.parametrize("text,name,value", [
        ("无名剑法武学技增伤 +8.0%", "无名剑法武学技增伤", 8.0),
        ("积矩九剑流血增伤+6.4%", "积矩九剑流血增伤", 6.4),
        ("明川药典治疗技增疗 7.2%", "明川药典治疗技增疗", 7.2),
    ])
    def test_armor_side(self, dp, text, name, value):
        assert dp.parse(text, "armor") == {"name": name, "value": value}

    def test_prefix_noise_substring_match(self, dp):
        # OCR 前缀噪声 → 子串匹配兜底
        result = dp.parse("荐外功穿透 +14.2%", "weapon")
        assert result == {"name": "外功穿透", "value": 14.2}

    def test_cleaner_and_dot_normalized(self, dp):
        # 真实脏数据：含噪声字符"荐" + 游戏内 武学·技能 间隔号
        result = dp.parse("明川药典·治疗技增疗荐7.5%", "armor")
        assert result == {"name": "明川药典治疗技增疗", "value": 7.5}

    def test_wrong_pool_rejected(self, dp):
        # 左四词条名不在右四词条池中，反之亦然
        assert dp.parse("外功穿透 +14.2%", "armor") is None
        assert dp.parse("无名剑法武学技增伤 +8.0%", "weapon") is None

    def test_empty_and_garbage(self, dp):
        assert dp.parse("", "weapon") is None
        assert dp.parse("纯噪声文本", "weapon") is None

    def test_name_without_value(self, dp):
        assert dp.parse("外功穿透", "weapon") is None


# ─── EquipmentParser 委托整链 ──────────────────────────────

class TestParserDelegation:
    def test_weapon_dingyin_parsed(self, parser):
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "承音 | 110阶",
            "base_attr": "外功攻击 100~232",
            "affix_gong": "最大外功攻击 +121.4",
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
            "affix_gong": "最大外功攻击 +121.4",
            "dingyin": "乱码噪声",
        })
        assert equip.dingyin == {}
        assert any("定音词条无法解析" in w for w in equip.warnings)
