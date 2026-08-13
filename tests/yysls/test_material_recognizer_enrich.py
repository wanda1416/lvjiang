"""MaterialRecognizer.enrich_info() 单元测试

验证将 MaterialInfo 转为富 dict 的逻辑，
确保 base 字段和插件专属解析字段均正确输出。
"""

from unittest.mock import MagicMock

import numpy as np

# 确保 yysls 内置函数已注册
import lvjiang.apps.yysls.workflows.builtins.equipment  # noqa: F401
from lvjiang.apps.yysls.core.material_recognizer import MaterialInfo, MaterialRecognizer


def _make_recognizer() -> MaterialRecognizer:
    """构造一个最小化的 MaterialRecognizer（无需真实 OCR/图库）"""
    return MaterialRecognizer(ocr_engine=MagicMock())


class TestEnrichInfoBaseFields:
    """base 字段（label/group/confidence）及解析字段"""

    def test_basic_enrich(self):
        """正常识别：扁平字段 + 全部解析字段"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="宋元通宝",
            ocr_texts={"level_text": "110阶", "count_text": "0/691"},
            confidence=0.95,
            meta={"level": 110},
        )
        result = recognizer.enrich_info(info)

        # 扁平字段
        assert result["label"] == "宋元通宝"
        assert result["confidence"] == 0.95
        assert result["group"] == ""
        # 原始 ocr 字段已被 yysls_rich_parse 删除
        assert "level_text" not in result
        assert "count_text" not in result
        # 解析字段
        assert result["real_level"] == 110
        assert result["count"] == 691
        assert result["devoted"] == 0

    def test_empty_slot(self):
        """空槽：label 为空"""
        recognizer = _make_recognizer()
        info = MaterialInfo(type="", confidence=1.0)
        result = recognizer.enrich_info(info)

        assert result["label"] == ""
        assert result["confidence"] == 1.0
        assert result["group"] == ""
        # 解析字段均不应出现
        assert "real_level" not in result
        assert "count" not in result
        assert "devoted" not in result

    def test_confidence_numpy_float32_serializable(self):
        """confidence 为 np.float32 时，enrich_info 应转为 Python float，确保 JSON 可序列化"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="振玉",
            confidence=np.float32(0.494156),
            meta={"group": "装备培养"},
        )
        result = recognizer.enrich_info(info)

        # 值相等
        assert abs(result["confidence"] - 0.494156) < 1e-4
        # 类型必须是 Python float，而非 np.float32
        assert isinstance(result["confidence"], float)
        assert not isinstance(result["confidence"], np.floating)
        # JSON 序列化不应抛出异常
        import json
        json.dumps(result)


class TestEnrichInfoParsedFields:
    """插件解析字段（real_level/count/devoted）的条件输出"""

    def test_no_level_text(self):
        """level_text 为空时不输出 real_level"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="定音石",
            ocr_texts={"count_text": "100"},
            confidence=0.9,
        )
        result = recognizer.enrich_info(info)

        assert "real_level" not in result
        assert result["count"] == 100
        assert "devoted" not in result  # 无 "/" 时 devoted 为 None

    def test_no_count_text(self):
        """count_text 为空时不输出 count 和 devoted"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="定音石",
            ocr_texts={"level_text": "50阶"},
            confidence=0.85,
        )
        result = recognizer.enrich_info(info)

        assert result["real_level"] == 50
        assert "count" not in result
        assert "devoted" not in result

    def test_count_without_slash(self):
        """count_text 无 "/"：有 count 无 devoted"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="定音石",
            ocr_texts={"level_text": "50阶", "count_text": "30200"},
            confidence=0.85,
        )
        result = recognizer.enrich_info(info)

        assert result["real_level"] == 50
        assert result["count"] == 30200
        assert "devoted" not in result

    def test_count_with_slash(self):
        """count_text 有 "/"：有 count 和 devoted"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="宋元通宝",
            ocr_texts={"level_text": "110阶", "count_text": "50/200"},
            confidence=0.9,
        )
        result = recognizer.enrich_info(info)

        assert result["real_level"] == 110
        assert result["count"] == 200
        assert result["devoted"] == 50

    def test_large_number_with_wan(self):
        """1.5万 格式的数字"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="宋元通宝",
            ocr_texts={"level_text": "1.5万", "count_text": "0/15000"},
            confidence=0.9,
        )
        result = recognizer.enrich_info(info)

        assert result["real_level"] == 15000
        assert result["count"] == 15000
        assert result["devoted"] == 0


class TestEnrichInfoMetaIsolation:
    """enrich_info 返回的 dict 修改不影响原始 MaterialInfo"""

    def test_label_independent(self):
        """修改返回 dict 的 label 不影响原始 info"""
        recognizer = _make_recognizer()
        info = MaterialInfo(type="test", confidence=0.5)
        result = recognizer.enrich_info(info)
        result["label"] = "changed"
        assert info.type == "test"

    def test_confidence_independent(self):
        """修改返回 dict 的 confidence 不影响原始 info"""
        recognizer = _make_recognizer()
        info = MaterialInfo(type="test", confidence=0.5)
        result = recognizer.enrich_info(info)
        result["confidence"] = 999
        assert info.confidence == 0.5


class TestYyslsRichParseBuiltin:
    """yysls_rich_parse 内置函数测试"""

    def _get_func(self):
        from lvjiang.workflows import builtins
        fn = builtins.get_function("yysls_rich_parse")
        assert fn is not None, "yysls_rich_parse 未注册"
        return fn

    def test_basic_parse(self):
        """基本解析：level_text → real_level，count_text → count/devoted"""
        parse = self._get_func()
        base = {
            "label": "宋元通宝",
            "group": "货币资产",
            "confidence": 0.95,
            "level_text": "110阶",
            "count_text": "0/691",
        }
        result = parse(base)
        assert result["real_level"] == 110
        assert result["count"] == 691
        assert result["devoted"] == 0
        # 原始字段已删除
        assert "level_text" not in result
        assert "count_text" not in result

    def test_count_without_slash(self):
        """count_text 无 '/'：有 count 无 devoted"""
        parse = self._get_func()
        base = {
            "label": "定音石",
            "group": "",
            "confidence": 0.85,
            "level_text": "50阶",
            "count_text": "30200",
        }
        result = parse(base)
        assert result["real_level"] == 50
        assert result["count"] == 30200
        assert "devoted" not in result
        assert "level_text" not in result
        assert "count_text" not in result

    def test_empty_ocr_fields(self):
        """level_text/count_text 为空时不添加解析字段"""
        parse = self._get_func()
        base = {"label": "test", "group": "", "confidence": 0.5, "level_text": "", "count_text": ""}
        result = parse(base)
        assert "real_level" not in result
        assert "count" not in result
        assert "devoted" not in result

    def test_large_number_wan(self):
        """1.5万 格式"""
        parse = self._get_func()
        base = {
            "label": "宋元通宝",
            "group": "",
            "confidence": 0.9,
            "level_text": "1.5万",
            "count_text": "0/15000",
        }
        result = parse(base)
        assert result["real_level"] == 15000
        assert result["count"] == 15000
        assert result["devoted"] == 0

    def test_enrich_info_uses_builtin(self):
        """enrich_info() 应通过 yysls_rich_parse 内置函数实现解析"""
        recognizer = _make_recognizer()
        info = MaterialInfo(
            type="宋元通宝",
            ocr_texts={"level_text": "110阶", "count_text": "0/691"},
            confidence=0.95,
            meta={"level": 110},
        )
        result = recognizer.enrich_info(info)
        # enrich_info 现在通过内置函数实现，应包含解析字段
        assert result["real_level"] == 110
        assert result["count"] == 691
        assert result["devoted"] == 0
        # 原始字段已删除
        assert "level_text" not in result
        assert "count_text" not in result
