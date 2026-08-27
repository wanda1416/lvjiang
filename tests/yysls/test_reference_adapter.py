"""yysls 对通用 ReferenceInfo 的领域解析契约。"""

import pytest
import yaml

import lvjiang.apps.yysls.workflows.builtins.equipment  # noqa: F401
from lvjiang.apps.yysls.core.recognizer.reference_adapter import (
    REQUIRED_OUTPUT_FIELDS,
    get_missing_output_fields,
    parse_number,
    parse_tuning_material,
)
from lvjiang.core.recognizers import ReferenceInfo
from lvjiang.core.reference_db import ReferenceDatabase
from lvjiang.workflows import builtins


@pytest.mark.parametrize(("text", "expected"), [
    ("", None), ("110阶", 110), ("30200", 30200),
    ("1.5万", 15000), ("0/1 1092", 1092),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


@pytest.mark.parametrize(("count_text", "count", "devoted"), [
    ("", None, None), ("30200", 30200, None),
    ("0/691", 691, 0), ("50/200", 200, 50),
])
def test_yysls_rich_parse(count_text, count, devoted):
    parse = builtins.get_function("yysls_rich_parse")
    assert parse is not None
    result = parse({
        "label": "材料", "group": "组", "confidence": 0.9,
        "meta": {"level": 110}, "level_text": "110阶",
        "count_text": count_text,
    })
    assert result["real_level"] == 110
    assert result.get("count") == count
    assert result.get("devoted") == devoted
    assert "level_text" not in result and "count_text" not in result
    assert result["meta"] == {"level": 110}


def test_python_tuning_adapter_reuses_rich_semantics():
    material = parse_tuning_material(ReferenceInfo(
        label="彩狗粮", confidence=0.95,
        ocr_texts={"level_text": "110阶", "count_text": "0/691"},
    ))
    assert material.label == "彩狗粮"
    assert material.real_level == 110
    assert material.count == 691
    assert material.devoted == 0


def test_yysls_transform_reports_legacy_schema_without_output_fields():
    parse = builtins.get_function("yysls_rich_parse")
    assert parse is not None
    with pytest.raises(ValueError, match="缺少 yysls 输出字段"):
        parse({"label": "材料", "group": "", "confidence": 0.9, "meta": {}})


def _db(tmp_path, schema) -> ReferenceDatabase:
    system_yaml = tmp_path / "system" / "references.yaml"
    system_yaml.parent.mkdir(parents=True)
    system_yaml.write_text(yaml.dump({
        "version": 1, "references": [], "meta_schema": schema,
    }), encoding="utf-8")
    db = ReferenceDatabase(
        system_dir=tmp_path / "system" / "references",
        system_yaml=system_yaml,
        local_dir=tmp_path / "local" / "references",
        local_yaml=tmp_path / "local" / "references.yaml",
        dev_mode=False,
        system_spaces_dir=tmp_path / "system" / "spaces_probe",
        local_spaces_dir=tmp_path / "local" / "spaces_probe",
        session_path=tmp_path / "session.json",
    )
    db.load()
    return db


def test_required_output_fields_contract(tmp_path):
    assert REQUIRED_OUTPUT_FIELDS == ("level_text", "count_text")
    schema = [{
        "key": "level_text", "scope": "output", "crop": [0, 0, 1, 0.5],
    }]
    assert get_missing_output_fields(_db(tmp_path, schema)) == ["count_text"]
