"""毕业率模型的等级、版本选择与导入契约。"""
from __future__ import annotations

import json

from lvjiang.apps.yysls.core.graduation import model_registry as registry
from lvjiang.apps.yysls.core.graduation.graduation_converter import (
    import_graduation_scheme,
)
from lvjiang.core.config.resolver import ConfigResolver


def _model(level: int, version: int, *, schema: int = 3) -> dict:
    return {
        "content_version": 1,
        "schema_version": schema,
        "school": "鸣金·虹",
        "scheme": "基础方案",
        "model_level": level,
        "model_version": version,
    }


def _write(root, level: int, version: int, *, schema: int = 3) -> None:
    path = root / registry.model_rel_path(
        "鸣金·虹", "基础方案", level, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_model(level, version, schema=schema)), encoding="utf-8")


def test_selects_highest_eligible_level_then_latest_version(
    tmp_path, monkeypatch,
) -> None:
    system = tmp_path / "system"
    for level, version in ((105, 1), (110, 1), (110, 2), (115, 1)):
        _write(system, level, version)
    resolver = ConfigResolver(
        system_dir=system, local_dir=tmp_path / "local", dev_mode=False)
    monkeypatch.setattr(registry, "get_resolver", lambda: resolver)
    registry.invalidate_model_registry()
    try:
        selected = registry.select_graduation_model(
            "鸣金·虹", "基础方案", 114)
        assert selected is not None
        assert (selected.level, selected.version) == (110, 2)

        selected = registry.select_graduation_model(
            "鸣金·虹", "基础方案", 115)
        assert selected is not None
        assert (selected.level, selected.version) == (115, 1)
        assert registry.select_graduation_model(
            "鸣金·虹", "基础方案", 100) is None
    finally:
        registry.invalidate_model_registry()


def test_old_schema_is_not_treated_as_a_graduation_model(
    tmp_path, monkeypatch,
) -> None:
    system = tmp_path / "system"
    _write(system, 110, 1, schema=2)
    resolver = ConfigResolver(
        system_dir=system, local_dir=tmp_path / "local", dev_mode=False)
    monkeypatch.setattr(registry, "get_resolver", lambda: resolver)
    registry.invalidate_model_registry()
    try:
        assert registry.available_models("鸣金·虹") == ()
    finally:
        registry.invalidate_model_registry()


def test_import_same_level_creates_next_version(tmp_path, monkeypatch) -> None:
    import lvjiang.apps.yysls.core.graduation.graduation_converter as converter

    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system, 110, 1)
    _write(system, 110, 2)
    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=False)
    monkeypatch.setattr(registry, "get_resolver", lambda: resolver)
    monkeypatch.setattr(converter, "get_resolver", lambda: resolver)
    monkeypatch.setattr(
        converter,
        "convert_workbook",
        lambda _path, school, scheme, level, version: {
            **_model(level, version), "school": school, "scheme": scheme,
        },
    )
    monkeypatch.setattr(converter, "validate_model", lambda _model: {"dps": 1.0})
    source = tmp_path / "source.xlsx"
    source.touch()
    registry.invalidate_model_registry()
    try:
        destination, outputs = import_graduation_scheme(
            source, "鸣金·虹", "基础方案", 110)
        assert destination == (
            local / registry.model_rel_path(
                "鸣金·虹", "基础方案", 110, 3))
        assert json.loads(destination.read_text(encoding="utf-8"))[
            "model_version"] == 3
        assert outputs == {"dps": 1.0}
    finally:
        registry.invalidate_model_registry()
