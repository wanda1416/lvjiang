"""装备内置函数测试

覆盖 make_fingerprint / is_good_equip 的核心判定逻辑。
affix_cap / chengyin_cap 需要 monkeypatch get_game_config。
"""


# 导入以注册内置函数
import lvjiang.apps.yysls.workflows.builtins.equipment  # noqa: F401
from lvjiang.workflows.builtins import get_function


def _fn(name):
    fn = get_function(name)
    assert fn is not None, f"内置函数 {name} 未注册"
    return fn


class TestMakeFingerprint:
    def test_basic_fingerprint(self):
        """正常装备数据生成指纹"""
        data = {
            "type": "武器",
            "level": "40",
            "quality": "紫色",
            "chengyin": "否",
            "affix_1": {"name": "外功攻击", "value": "100"},
        }
        fp = _fn("make_fingerprint")(data)
        assert len(fp) == 8  # MD5 前 8 位
        assert isinstance(fp, str)

    def test_same_data_same_fingerprint(self):
        """相同数据生成相同指纹"""
        data = {"type": "武器", "level": "40", "quality": "紫色"}
        fp1 = _fn("make_fingerprint")(data)
        fp2 = _fn("make_fingerprint")(data)
        assert fp1 == fp2

    def test_different_data_different_fingerprint(self):
        """不同数据生成不同指纹"""
        data1 = {"type": "武器", "level": "40"}
        data2 = {"type": "武器", "level": "45"}
        fp1 = _fn("make_fingerprint")(data1)
        fp2 = _fn("make_fingerprint")(data2)
        assert fp1 != fp2

    def test_empty_data_returns_empty(self):
        """空数据返回空字符串"""
        assert _fn("make_fingerprint")({}) == ""
        assert _fn("make_fingerprint")(None) == ""
        assert _fn("make_fingerprint")("not a dict") == ""

    def test_serialized_empty_equipment_returns_empty(self):
        """空槽 OCR 字段齐全但身份全空，仍必须视为空装备。"""
        data = {
            "type": None,
            "name": None,
            "level": None,
            "quality": None,
            "is_chengyin": False,
            "base_attr": None,
            "base_attr_2": None,
            "dingyin": None,
        }
        assert _fn("make_fingerprint")(data) == ""

    def test_affixes_included_in_fingerprint(self):
        """词条参与指纹计算"""
        base = {"type": "武器", "level": "40", "quality": "紫色"}
        with_affix = {**base, "affix_1": {"name": "会心", "value": "5%"}}
        fp_base = _fn("make_fingerprint")(base)
        fp_with = _fn("make_fingerprint")(with_affix)
        assert fp_base != fp_with

    def test_missing_affix_name_skipped(self):
        """词条无 name 字段时跳过"""
        data = {
            "type": "武器",
            "affix_1": {"value": "100"},  # 无 name
        }
        fp = _fn("make_fingerprint")(data)
        assert len(fp) == 8


class TestIsGoodEquip:
    def test_high_value_keywords_hit(self):
        """命中 2 条以上高价值词条返回 True"""
        result = {
            "f1": "大外攻+100",
            "f2": "会心+5%",
            "f3": "生命+200",
        }
        assert _fn("is_good_equip")(result) is True

    def test_low_value_keywords_miss(self):
        """命中不足 2 条返回 False"""
        result = {
            "f1": "生命+200",
            "f2": "防御+50",
            "f3": "气血+100",
        }
        assert _fn("is_good_equip")(result) is False

    def test_empty_data_returns_false(self):
        """空数据返回 False"""
        assert _fn("is_good_equip")({}) is False
        assert _fn("is_good_equip")(None) is False
        assert _fn("is_good_equip")("not a dict") is False

    def test_non_string_values_skipped(self):
        """非字符串值不参与匹配"""
        result = {
            "f1": 123,  # 非字符串
            "f2": None,
            "f3": "大外攻+100",
            "f4": "会心+5%",
        }
        assert _fn("is_good_equip")(result) is True

    def test_single_keyword_multiple_fields(self):
        """同一关键词在多个字段命中各计一次"""
        result = {
            "f1": "大外攻+100",
            "f2": "大外攻+200",  # 同一关键词，但不同字段
        }
        # 每个字段只计一次，两个字段都命中 = 2 次
        assert _fn("is_good_equip")(result) is True


class TestAffixCap:
    def test_empty_name_returns_zero(self, monkeypatch):
        """空名称返回 0"""
        assert _fn("affix_cap")("", 40) == 0
        assert _fn("affix_cap")(None, 40) == 0

    def test_invalid_level_returns_zero(self, monkeypatch):
        """无效等级返回 0"""
        assert _fn("affix_cap")("外攻攻击", "abc") == 0
        assert _fn("affix_cap")("外攻攻击", None) == 0

    def test_valid_query_returns_cap(self, monkeypatch):
        """正常查询返回上限值"""
        mock_config = type("C", (), {
            "get_affix_caps": lambda self, level, name: {"cap": 150.0, "chengyin": 141.0},
        })()
        monkeypatch.setattr(
            "lvjiang.apps.yysls.config.get_game_config",
            lambda: mock_config,
        )
        result = _fn("affix_cap")("外攻攻击", 40)
        assert result == 150.0

    def test_not_found_returns_zero(self, monkeypatch):
        """未找到配置返回 0"""
        mock_config = type("C", (), {
            "get_affix_caps": lambda self, level, name: None,
        })()
        monkeypatch.setattr(
            "lvjiang.apps.yysls.config.get_game_config",
            lambda: mock_config,
        )
        assert _fn("affix_cap")("不存在的词条", 40) == 0


class TestChengyinCap:
    def test_valid_query_returns_chengyin(self, monkeypatch):
        """正常查询返回承音值"""
        mock_config = type("C", (), {
            "get_affix_caps": lambda self, level, name: {"cap": 150.0, "chengyin": 141.0},
        })()
        monkeypatch.setattr(
            "lvjiang.apps.yysls.config.get_game_config",
            lambda: mock_config,
        )
        result = _fn("chengyin_cap")("外攻攻击", 40)
        assert result == 141.0

    def test_empty_name_returns_zero(self):
        """空名称返回 0"""
        assert _fn("chengyin_cap")("", 40) == 0

    def test_invalid_level_returns_zero(self):
        """无效等级返回 0"""
        assert _fn("chengyin_cap")("外攻攻击", "abc") == 0
