"""MaterialRecognizer 输出元数据 OCR 单测

不依赖真实参考图库与 OCR 引擎：
- ReferenceDatabase 用 tmp_path override 构造
- OCREngine 与 ReferenceMatcher 用 fake/mock 替代
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import yaml

from lvjiang.apps.yysls.core.recognizer.material_recognizer import (
    REQUIRED_OUTPUT_FIELDS,
    MaterialInfo,
    MaterialRecognizer,
    get_missing_output_fields,
)
from lvjiang.core.recognizers.reference_matcher import MatchResult
from lvjiang.core.reference_db import ReferenceDatabase


def _write_yaml(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")


def _make_db(tmp_path, meta_schema) -> ReferenceDatabase:
    system_yaml = tmp_path / "system" / "references.yaml"
    _write_yaml(system_yaml, {"version": 1, "references": [],
                              "meta_schema": meta_schema})
    # 空间扫描隔离到 tmp_path 的空目录（避免读真实 config）
    return ReferenceDatabase(
        system_dir=tmp_path / "system" / "references",
        system_yaml=system_yaml,
        local_dir=tmp_path / "local" / "references",
        local_yaml=tmp_path / "local" / "references.yaml",
        dev_mode=False,
        system_spaces_dir=tmp_path / "system" / "spaces_probe",
        local_spaces_dir=tmp_path / "local" / "spaces_probe",
        session_path=tmp_path / "session.json",
    )


class _FakeOCR:
    """假 OCR 引擎：把裁剪形状编码为文本，便于断言裁剪区域"""

    def recognize(self, crop):
        h, w = crop.shape[:2]
        return [SimpleNamespace(text=f"{h}x{w}")]


def _slot_img(size: int = 40) -> np.ndarray:
    """高方差噪声图（通过空槽检测）"""
    rng = np.random.default_rng(0)
    return rng.integers(0, 256, (size, size, 3), dtype=np.uint8)


def _make_recognizer(tmp_path, meta_schema) -> MaterialRecognizer:
    db = _make_db(tmp_path, meta_schema)
    recognizer = MaterialRecognizer(_FakeOCR(), reference_db=db)  # type: ignore[arg-type]
    recognizer._matcher = MagicMock()
    return recognizer


_OUTPUT_SCHEMA = [
    {"key": "level", "name": "等级", "scope": "input"},
    {"key": "level_text", "name": "等级文本区域", "scope": "output",
     "crop": [0.0, 0.0, 1.0, 0.25]},
    {"key": "count_text", "name": "数量文本区域", "scope": "output",
     "crop": [0.0, 0.25, 1.0, 0.75]},
]


class TestRecognizeOutputFields:
    def test_ocr_by_schema_crop(self, tmp_path):
        """schema 含输出字段：按各自 crop 区域 OCR 填充 ocr_texts"""
        recognizer = _make_recognizer(tmp_path, _OUTPUT_SCHEMA)
        recognizer._matcher.match.return_value = MatchResult(
            entry=None, label="彩狗粮", confidence=0.9, meta={},
        )

        result = recognizer.recognize(_slot_img())

        assert result.type == "彩狗粮"
        # 40x40 图：level_text 高 10，count_text 高 30
        assert result.ocr_texts["level_text"] == "10x40"
        assert result.ocr_texts["count_text"] == "30x40"
        assert result.level_text == "10x40"
        assert result.count_text == "30x40"

    def test_fallback_without_output_fields(self, tmp_path):
        """schema 无输出字段：回退硬编码上下半区，key 不变"""
        recognizer = _make_recognizer(
            tmp_path, [{"key": "level", "name": "等级", "scope": "input"}],
        )
        recognizer._matcher.match.return_value = MatchResult(
            entry=None, label="彩狗粮", confidence=0.9, meta={},
        )

        result = recognizer.recognize(_slot_img())

        assert set(result.ocr_texts) == {"level_text", "count_text"}
        # 上下各半：40x40 图各高 20
        assert result.ocr_texts["level_text"] == "20x40"
        assert result.ocr_texts["count_text"] == "20x40"

    def test_empty_slot_returns_empty(self, tmp_path):
        """空槽：无 ocr_texts"""
        recognizer = _make_recognizer(tmp_path, _OUTPUT_SCHEMA)
        result = recognizer.recognize(np.zeros((40, 40, 3), dtype=np.uint8))
        assert result.type == ""
        assert result.ocr_texts == {}
        recognizer._matcher.match.assert_not_called()


class TestRecognizeTopN:
    def test_level_from_reference_meta(self, tmp_path):
        """top N：等级取自参考条目 meta.level，其余输出字段共享一次 OCR"""
        recognizer = _make_recognizer(tmp_path, _OUTPUT_SCHEMA)
        recognizer._matcher.match_top_n.return_value = [
            MatchResult(entry=None, label="变音石", confidence=0.8,
                        meta={"level": 110}),
            MatchResult(entry=None, label="变音石", confidence=0.7,
                        meta={"level": 105}),
        ]

        results = recognizer.recognize_top_n(_slot_img(), n=2)

        assert [r.level_text for r in results] == ["110阶", "105阶"]
        # count_text 两条共享同一次 OCR 结果
        assert results[0].count_text == results[1].count_text == "30x40"

    def test_top_n_without_meta_level(self, tmp_path):
        """top N：参考条目无 level 时不覆盖 OCR 结果"""
        recognizer = _make_recognizer(tmp_path, _OUTPUT_SCHEMA)
        recognizer._matcher.match_top_n.return_value = [
            MatchResult(entry=None, label="溯玉", confidence=0.6, meta={}),
        ]

        results = recognizer.recognize_top_n(_slot_img(), n=1)

        assert results[0].level_text == "10x40"  # 保留 OCR 原值


class TestMaterialInfoParsing:
    """ocr_texts 便捷属性与解析属性"""

    def test_count_and_devoted_parsing(self):
        info = MaterialInfo(type="彩狗粮",
                            ocr_texts={"count_text": "0/691"})
        assert info.count == 691
        assert info.devoted == 0

    def test_count_without_slash(self):
        info = MaterialInfo(type="彩狗粮",
                            ocr_texts={"count_text": "691"})
        assert info.count == 691
        assert info.devoted is None

    def test_real_level_parsing(self):
        info = MaterialInfo(type="彩狗粮",
                            ocr_texts={"level_text": "110阶"})
        assert info.real_level == 110

    def test_missing_texts(self):
        info = MaterialInfo(type="")
        assert info.level_text == ""
        assert info.count_text == ""
        assert info.real_level is None
        assert info.count is None
        assert info.devoted is None

    def test_count_with_wan(self):
        """支持 '1.5万'、'12万' 等中文数字格式"""
        info = MaterialInfo(type="彩狗粮",
                            ocr_texts={"count_text": "1.5万"})
        assert info.count == 15000

        info2 = MaterialInfo(type="彩狗粮",
                             ocr_texts={"count_text": "12万"})
        assert info2.count == 120000

    def test_count_with_slash_and_wan(self):
        """'0/1.5万' 格式：投入 0，持有 15000"""
        info = MaterialInfo(type="彩狗粮",
                            ocr_texts={"count_text": "0/1.5万"})
        assert info.count == 15000
        assert info.devoted == 0

    def test_real_level_with_wan(self):
        """等级也可能出现 '万' 格式（虽然罕见）"""
        info = MaterialInfo(type="彩狗粮",
                            ocr_texts={"level_text": "1.5万"})
        assert info.real_level == 15000


class TestTuningPrecheck:
    """调律启动预检：当前图库空间必须含 level_text/count_text 输出字段"""

    def test_contract_keys(self):
        assert REQUIRED_OUTPUT_FIELDS == ("level_text", "count_text")

    def test_full_schema_satisfies_contract(self, tmp_path):
        db = _make_db(tmp_path, _OUTPUT_SCHEMA)
        db.load()
        assert get_missing_output_fields(db) == []

    def test_missing_both(self, tmp_path):
        db = _make_db(tmp_path, [{"key": "level", "name": "等级", "scope": "input"}])
        db.load()
        assert get_missing_output_fields(db) == ["level_text", "count_text"]

    def test_missing_one(self, tmp_path):
        schema = [
            {"key": "level_text", "name": "等级文本区域", "scope": "output",
             "crop": [0.0, 0.0, 1.0, 0.25]},
        ]
        db = _make_db(tmp_path, schema)
        db.load()
        assert get_missing_output_fields(db) == ["count_text"]

    def test_invalid_crop_not_counted(self, tmp_path):
        """output 字段 crop 非法时不算有效输出字段"""
        schema = [
            {"key": "level_text", "name": "等级文本区域", "scope": "output",
             "crop": [2.0, 0.0, 1.0, 0.25]},
            {"key": "count_text", "name": "数量文本区域", "scope": "output",
             "crop": [0.0, 0.25, 1.0, 0.75]},
        ]
        db = _make_db(tmp_path, schema)
        db.load()
        assert get_missing_output_fields(db) == ["level_text"]
