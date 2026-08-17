"""OCR 文本清洗规则测试

- 误识别替换：猜准率→精准率、扁武学→扇武学、经甲→胫甲
- 噪声字符删除：荐（全部出现位置）
- 符号统一：中文括号 → 英文括号
- OCR 引擎 → Parser 整链验证
"""

import pytest

from lvjiang.apps.yysls.core.equip_parser.parser import EquipmentParser
from lvjiang.core.ocr_cleaner import OCRCleaner


@pytest.fixture(scope="module")
def parser():
    return EquipmentParser()


@pytest.fixture(autouse=True)
def reset_cleaner():
    """每个测试前重置单例，确保加载最新配置"""
    OCRCleaner.reset_instance()


class TestOCRCleaner:
    @pytest.mark.parametrize("raw,expected", [
        ("猜准率 10.8%", "精准率 10.8%"),
        ("扁武学增伤 8.2%", "扇武学增伤 8.2%"),
        ("经甲", "胫甲"),
    ])
    def test_ocr_replacements(self, raw, expected):
        assert OCRCleaner().clean(raw) == expected

    def test_noise_jian_removed_everywhere(self):
        # "荐" 无论出现几次、在什么位置都删除
        assert OCRCleaner().clean("荐会心率荐 5%荐") == "会心率 5%"

    def test_chinese_brackets_to_english(self):
        assert OCRCleaner().clean("【转】最大攻击") == "[转]最大攻击"
        assert OCRCleaner().clean("会心率（%）") == "会心率(%)"

    def test_strip_whitespace(self):
        assert OCRCleaner().clean("  会心率 5%  ") == "会心率 5%"

    def test_empty(self):
        assert OCRCleaner().clean("") == ""
        assert OCRCleaner().clean("荐") == ""


class TestParserDelegation:
    """测试 OCR 引擎 → Parser 整链：先清洗再解析"""

    def _clean_then_parse(self, parser, raw: str):
        """模拟 OCR 引擎流程：先清洗再传入 parser"""
        cleaned = OCRCleaner().clean(raw)
        return parser._parse_single_affix(cleaned)

    def test_bianwuxue_corrected_to_shan(self, parser):
        # 扁武学 → 扇武学 后由 WUXUE_PATTERN 正常匹配
        affix = self._clean_then_parse(parser, "扁武学增伤 8.2%")
        assert affix.name == "扇武学增伤"
        assert affix.value == 8.2

    def test_caizhun_corrected(self, parser):
        affix = self._clean_then_parse(parser, "猜准率 10.8%")
        assert affix.name == "精准率"

    def test_jian_noise_removed(self, parser):
        affix = self._clean_then_parse(parser, "荐会心率 5%")
        assert affix.name == "会心率"
