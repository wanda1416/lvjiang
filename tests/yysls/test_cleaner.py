"""词条 OCR 文本数据清洗（cleaner）测试

- 误识别替换：猜准率→精准率、扁武学→扇武学
- 噪声字符删除：荐（全部出现位置）
- EquipmentParser 委托整链（清洗后正常匹配词条）
"""

import pytest

from lvjiang.apps.yysls.equip_parser.cleaner import clean_affix_text
from lvjiang.apps.yysls.equip_parser.parser import EquipmentParser


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


class TestCleanAffixText:
    @pytest.mark.parametrize("raw,expected", [
        ("猜准率 10.8%", "精准率 10.8%"),
        ("扁武学增伤 8.2%", "扇武学增伤 8.2%"),
    ])
    def test_ocr_replacements(self, raw, expected):
        assert clean_affix_text(raw) == expected

    def test_noise_jian_removed_everywhere(self):
        # "荐" 无论出现几次、在什么位置都删除
        assert clean_affix_text("荐会心率荐 5%荐") == "会心率 5%"

    def test_strip_whitespace(self):
        assert clean_affix_text("  会心率 5%  ") == "会心率 5%"

    def test_empty(self):
        assert clean_affix_text("") == ""
        assert clean_affix_text("荐") == ""


class TestParserDelegation:
    def test_bianwuxue_corrected_to_shan(self, parser):
        # 扁武学 → 扇武学 后由 WUXUE_PATTERN 正常匹配
        affix = parser._parse_single_affix("扁武学增伤 8.2%")
        assert affix.name == "扇武学增伤"
        assert affix.value == 8.2

    def test_caizhun_corrected(self, parser):
        affix = parser._parse_single_affix("猜准率 10.8%")
        assert affix.name == "精准率"

    def test_jian_noise_removed(self, parser):
        affix = parser._parse_single_affix("荐会心率 5%")
        assert affix.name == "会心率"
