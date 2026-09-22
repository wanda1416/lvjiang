"""DingyinParser（定音词条解析器）测试

- 候选池 = 外功增益/属攻增益 + 指定技能增效（十大流派 × 5）全量
  （词条名全局唯一，匹配不依赖装备部位）
- EquipmentParser 委托整链（dingyin 字段解析 + 无法解析 warning）
"""

import pytest

from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
    DINGYIN_NORMAL,
    DINGYIN_SLOT_NOTICE,
    DINGYIN_ZHIGE,
    ZHIGE_DINGYIN_KEY,
    ZHIGE_DINGYIN_NAME,
    DingyinParser,
    refresh_dingyin_marker_dict,
)
from lvjiang.apps.yysls.core.equip_parser.parser import EquipmentParser
from lvjiang.core.ocr_cleaner import OCRCleaner
from tests.case_matrix import case_matrix


@pytest.fixture(scope="module")
def dp():
    return DingyinParser()


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


@pytest.fixture(autouse=True)
def reset_cleaner():
    """每个测试前重置单例，确保加载最新配置"""
    OCRCleaner.reset_instance()


def clean(text: str) -> str:
    """模拟 OCR 引擎清洗"""
    return OCRCleaner().clean(text, "equip")


# ─── 候选词条池 ────────────────────────────────────────────

class TestCandidates:
    def test_full_pool(self, dp):
        # 全量池 = 增益类 3 条 + 指定技能增效 57 条（11 流派）
        names = set(dp._candidates())
        assert {"外功穿透", "外功抗性", "无相穿透"} <= names
        assert "无名剑法武学技增伤" in names
        assert "千机索天重击增伤" in names
        assert len(names) == 60

    def test_full_pool_survives_translation(self, dp, monkeypatch):
        """_DINGYIN_CATEGORIES 查的是 attributes.yaml 里的分类 key（裸中文
        游戏配置数据），不能过 tr()——一旦被翻译，候选池会在英文界面下
        整个查不到任何别名，定音解析全灭。"""
        import lvjiang.i18n as i18n
        monkeypatch.setattr(i18n, "_current_language", "en_US")
        monkeypatch.setattr(i18n, "_translations", {
            "外功增益": "EXT_BONUS", "属攻增益": "ELEM_BONUS",
            "指定技能增效": "SKILL_BONUS",
        })
        assert len(set(dp._candidates())) == 60


# ─── 解析 ─────────────────────────────────────────────────

class TestParse:
    @case_matrix("text,name,value", [
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
        # OCR 前缀噪声 → 子串匹配兜底（由 OCR 引擎清洗）
        result = dp.parse(clean("荐外功穿透 +14.2%"))
        assert result == {"name": "外功穿透", "value": 14.2}

    def test_cleaner_and_dot_normalized(self, dp):
        # 真实脏数据：含噪声字符"荐" + 游戏内 武学·技能 间隔号（由 OCR 引擎清洗）
        result = dp.parse(clean("明川药典·治疗技增疗荐7.5%"))
        assert result == {"name": "明川药典治疗技增疗", "value": 7.5}

    def test_empty_and_garbage(self, dp):
        assert dp.parse("") is None
        assert dp.parse("纯噪声文本") is None

    def test_name_without_value(self, dp):
        assert dp.parse("外功穿透") is None
        assert dp.matches_normal_name("外功穿透") is True

    def test_zhige_text_does_not_match_normal_name(self, dp):
        assert dp.matches_normal_name("止戈特殊效果 +12") is False


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
        assert equip.dingyin == {"name": "外功穿透", "value": 14.2, "cap_pct": 84.5}
        assert equip.to_dict()["dingyin"] == {"name": "外功穿透", "value": 14.2, "cap_pct": 84.5}

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
        assert equip.dingyin == {"name": "千机索天重击增伤", "value": 6.4, "cap_pct": 69.6}

    def test_no_dingyin_field(self, parser):
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "110阶",
            "base_attr": "外功攻击 100~232",
            "affix_gong": "最大外功攻击 +121.4",
        })
        assert equip.dingyin == {}
        assert equip.to_dict()["dingyin"] is None

    def test_unparsable_dingyin_lands_in_the_zhige_slot(self, parser):
        """词库解释不了的文本进止戈槽，绝不能占用普通定音槽。

        一次扫描只读得到一种定音，写错槽会让仓储的合并把另一种定音当成
        「本次没带」而搬回来，两边都错。
        """
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "110阶",
            "base_attr": "外功攻击 100~232",
            **self._FULL_AFFIXES,
            "dingyin": "乱码噪声",
        })
        assert equip.dingyin == {}
        assert equip.dingyin_zhige == {"name": ZHIGE_DINGYIN_NAME}
        assert equip.dingyin_type == DINGYIN_ZHIGE
        assert equip.extra_data[ZHIGE_DINGYIN_KEY] is True
        assert not any("定音词条无法解析" in w for w in equip.warnings)

    def test_normal_name_with_bad_value_is_recorded_as_zero(self, parser):
        """名称认得出、数值没读出来：按 0 记进普通定音槽。

        0 在卡片上一眼就不对，比静默留着上一次的数值更容易被发现；重扫一次
        即可恢复。归到止戈是错的——名称明明认得出来。
        """
        equip = parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "承音 | 110阶",
            "base_attr": "外功攻击 100~232",
            **self._FULL_AFFIXES,
            "dingyin": "外功穿透",
        })
        assert ZHIGE_DINGYIN_KEY not in equip.extra_data
        assert equip.dingyin_zhige == {}
        assert equip.dingyin_type == DINGYIN_NORMAL
        assert equip.dingyin["name"] == "外功穿透"
        assert equip.dingyin["value"] == 0.0
        assert any("已按 0 记录" in w for w in equip.warnings)
        # 说明挂在它解释的那一槽里，合并时跟着槽一起搬，不会丢也不会串。
        assert "已按 0 记录" in equip.dingyin[DINGYIN_SLOT_NOTICE]

    def test_affixes_less_than_5_skips_dingyin(self, parser):
        """词条不满 5 个时不可能有定音，读到什么都是脏数据，整段跳过。

        此时连 dingyin_type 都不能写——写了会被仓储当成一次有效的展示状态
        更新，把背包里原本正确的定音种类改掉。
        """
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
        assert equip.dingyin_zhige == {}
        assert equip.dingyin_type == ""
        assert len(equip.affixes) == 2


class TestZhigeMarker:
    """标记是 dingyin_type 的派生值，不再反过来决定它。"""

    def test_legacy_marker_becomes_a_zhige_slot(self):
        """历史记录只有标记没有数据槽，读取时补出固定名称的止戈槽。"""
        equip = {"dingyin": {}, "_extra": {ZHIGE_DINGYIN_KEY: True}}
        assert refresh_dingyin_marker_dict(equip) is True
        assert equip["dingyin_zhige"] == {"name": ZHIGE_DINGYIN_NAME}

    def test_zhige_type_survives_a_kept_normal_dingyin(self):
        """两种定音并存时，加载不能因为普通槽有值就把止戈态改回去。

        旧实现从 dingyin.name 反推标记，新模型下普通槽会长期保留数据，
        那样每次加载都会把止戈态抹掉——正是这次要修的丢失。
        """
        equip = {
            "dingyin": {"name": "外功穿透", "value": 10},
            "dingyin_zhige": {"name": ZHIGE_DINGYIN_NAME},
            "dingyin_type": DINGYIN_ZHIGE,
        }
        assert refresh_dingyin_marker_dict(equip) is True
        assert equip["dingyin"] == {"name": "外功穿透", "value": 10}
        assert equip["_extra"][ZHIGE_DINGYIN_KEY] is True

    def test_normal_type_clears_stale_marker(self):
        equip = {
            "dingyin": {"name": "外功穿透", "value": 10},
            "dingyin_type": DINGYIN_NORMAL,
            "_extra": {ZHIGE_DINGYIN_KEY: True},
        }
        assert refresh_dingyin_marker_dict(equip) is False
        assert ZHIGE_DINGYIN_KEY not in equip["_extra"]

    def test_unknown_legacy_name_stays_in_the_normal_slot(self):
        """历史上被写进普通槽的止戈名称不做识别搬迁。

        名称判定会随定音词库增删而翻转，不能让它去改一个已经存成事实的
        展示状态；这种记录重扫一次就会归位。
        """
        equip = {"dingyin": {"name": "止戈特殊效果", "value": 99}}
        assert refresh_dingyin_marker_dict(equip) is False
        assert equip["dingyin"] == {"name": "止戈特殊效果", "value": 99}
        assert "dingyin_zhige" not in equip


class TestMisreadVsZhige:
    """区分「OCR 误读」与「可预计的止戈定音」

    两者形态上无法区分——都带数值、名称都不在词库里。误读的特征是与真实
    定音名共享长前缀。分不开的话，定音名错一个字就会被静默当成止戈定音，
    该装备的定音收益从毕业率里彻底消失，而用户只看到一个正常的
    <止戈定音>，无从察觉需要校正。
    """

    def test_misread_detected_by_shared_prefix(self, dp):
        assert dp.suspected_misread("外功穿诱 +14.2%") == "外功穿透"

    def test_genuine_zhige_not_flagged(self, dp):
        assert dp.suspected_misread("止戈特殊效果 +12") is None

    def test_random_noise_not_flagged(self, dp):
        assert dp.suspected_misread("乱码噪声") is None

    def test_valid_name_not_flagged(self, dp):
        assert dp.suspected_misread("外功穿透 +14.2%") is None

    def test_empty_not_flagged(self, dp):
        assert dp.suspected_misread("") is None
        assert dp.suspected_misread(None) is None


class TestMisreadReachesWarnings:
    _AFFIXES = {
        "affix_gong": "最大外功攻击 +121.4",
        "affix_shang": "会心率 +7%",
        "affix_jue": "劲 +72.2",
        "affix_zhi": "势 +60",
        "affix_yu": "会意率 +6",
    }

    def _parse(self, parser, dingyin: str):
        return parser.parse({
            "equip_type": "踏雪含光 | 武器·剑",
            "equip_level": "承音 | 110阶",
            "base_attr": "外功攻击 100~232",
            **self._AFFIXES,
            "dingyin": dingyin,
        })

    def test_misread_warns_but_still_uses_non_normal_marker(self, parser):
        from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
            ZHIGE_DINGYIN_KEY,
        )
        equip = self._parse(parser, "外功穿诱 +14.2%")
        assert any("疑似误读" in w for w in equip.warnings)
        assert "外功穿透" in " ".join(equip.warnings)
        assert equip.extra_data[ZHIGE_DINGYIN_KEY] is True
        # 说明挂在止戈槽自己身上，不会跟着另一种定音走。
        assert "外功穿透" in equip.dingyin_zhige[DINGYIN_SLOT_NOTICE]

    def test_genuine_zhige_still_silent(self, parser):
        from lvjiang.apps.yysls.core.equip_parser.dingyin_parser import (
            ZHIGE_DINGYIN_KEY,
        )
        equip = self._parse(parser, "止戈特殊效果 +12")
        assert equip.extra_data[ZHIGE_DINGYIN_KEY] is True
        assert not any("疑似误读" in w for w in equip.warnings)
