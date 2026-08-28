"""通用 ReferenceRecognizer 的 schema OCR 与 rich 契约。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
import yaml

from lvjiang.core.recognizers import ReferenceInfo, ReferenceRecognizer
from lvjiang.core.recognizers.reference_matcher import MatchResult
from lvjiang.core.reference_db import MetaFieldDef, ReferenceDatabase


def _write_yaml(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")


def _make_db(tmp_path, meta_schema) -> ReferenceDatabase:
    system_yaml = tmp_path / "system" / "references.yaml"
    _write_yaml(system_yaml, {
        "version": 1, "references": [], "meta_schema": meta_schema,
    })
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
    def recognize(self, crop):
        height, width = crop.shape[:2]
        return [SimpleNamespace(text=f"{height}x{width}")]


OUTPUT_SCHEMA = [
    {"key": "level", "name": "等级", "scope": "input"},
    {"key": "level_text", "name": "等级文本区域", "scope": "output",
     "crop": [0.0, 0.0, 1.0, 0.25]},
    {"key": "count_text", "name": "数量文本区域", "scope": "output",
     "crop": [0.0, 0.25, 1.0, 0.75]},
]


def _recognizer(tmp_path, schema=OUTPUT_SCHEMA) -> ReferenceRecognizer:
    recognizer = ReferenceRecognizer(_FakeOCR(), _make_db(tmp_path, schema))  # type: ignore[arg-type]
    recognizer._matcher = MagicMock()
    return recognizer


def _image() -> np.ndarray:
    return np.zeros((40, 40, 3), dtype=np.uint8)


def test_ocr_uses_each_output_schema_crop(tmp_path):
    recognizer = _recognizer(tmp_path)
    recognizer._matcher.match.return_value = MatchResult(
        entry=None, label="参考 A", confidence=0.9, meta={"level": 110},
    )
    result = recognizer.recognize(_image())
    assert result.label == "参考 A"
    assert result.ocr_texts == {
        "level_text": "10x40", "count_text": "30x40",
    }


def test_schema_without_output_fields_produces_no_implicit_business_keys(tmp_path):
    recognizer = _recognizer(
        tmp_path, [{"key": "level", "name": "等级", "scope": "input"}],
    )
    recognizer._matcher.match.return_value = MatchResult(
        entry=None, label="参考 A", confidence=0.9, meta={},
    )
    assert recognizer.recognize(_image()).ocr_texts == {}


def test_unmatched_reference_skips_ocr(tmp_path):
    recognizer = _recognizer(tmp_path)
    recognizer._matcher.match.return_value = MatchResult(
        entry=None, label="", confidence=0.2, meta={},
    )
    result = recognizer.recognize(_image())
    assert result.label == ""
    assert result.confidence == 0.2
    assert result.ocr_texts == {}


def test_match_exception_is_consumed_as_unmatched(tmp_path):
    recognizer = _recognizer(tmp_path)
    recognizer._matcher.match.side_effect = RuntimeError("特征提取失败")

    result = recognizer.recognize(_image())

    assert result == ReferenceInfo(label="")


def test_ocr_exception_is_consumed_per_output_field(tmp_path):
    recognizer = _recognizer(tmp_path)
    recognizer._matcher.match.return_value = MatchResult(
        entry=None, label="参考 A", confidence=0.9, meta={},
    )
    recognizer._ocr = MagicMock()
    recognizer._ocr.recognize.side_effect = RuntimeError("OCR 暂不可用")

    result = recognizer.recognize(_image())

    assert result.label == "参考 A"
    assert result.ocr_texts == {"level_text": "", "count_text": ""}


def test_top_n_match_exception_is_consumed_as_no_candidates(tmp_path):
    recognizer = _recognizer(tmp_path)
    recognizer._matcher.match_top_n.side_effect = RuntimeError("匹配器异常")

    assert recognizer.recognize_top_n(_image()) == []


def test_empty_image_is_safely_unmatched(tmp_path):
    recognizer = _recognizer(tmp_path)
    empty = np.empty((0, 0, 3), dtype=np.uint8)
    assert recognizer.recognize(empty).label == ""
    assert recognizer.recognize_top_n(empty) == []
    recognizer._matcher.match.assert_not_called()
    recognizer._matcher.match_top_n.assert_not_called()


def test_top_n_keeps_real_ocr_instead_of_fabricating_from_meta(tmp_path):
    recognizer = _recognizer(tmp_path)
    recognizer._matcher.match_top_n.return_value = [
        MatchResult(entry=None, label="候选 A", confidence=0.8, meta={"level": 110}),
        MatchResult(entry=None, label="候选 B", confidence=0.7, meta={"level": 105}),
    ]
    results = recognizer.recognize_top_n(_image(), n=2)
    assert [item.ocr_texts["level_text"] for item in results] == ["10x40", "10x40"]
    assert [item.meta["level"] for item in results] == [110, 105]


def test_build_rich_base_keeps_meta_nested_and_reserves_engine_fields():
    info = ReferenceInfo(
        label="参考 A", group="组", confidence=np.float32(0.75),
        meta={"level": 110},
        ocr_texts={"label": "伪造", "meta": "伪造", "custom": "文本"},
    )
    rich = ReferenceRecognizer.build_rich_base(info)
    assert rich == {
        "label": "参考 A", "meta": {"level": 110}, "custom": "文本",
        "group": "组", "confidence": 0.75,
    }
    assert isinstance(rich["confidence"], float)


def test_reference_db_rejects_reserved_output_keys(tmp_path):
    db = _make_db(tmp_path, OUTPUT_SCHEMA)
    with pytest.raises(ValueError, match="保留字段"):
        db.set_meta_schema([
            MetaFieldDef(
                key="label", scope="output", crop=[0.0, 0.0, 1.0, 1.0],
            ),
        ])
