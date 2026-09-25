"""毕业率模型的等级、版本选择与导入契约。"""
from __future__ import annotations

import json

from loguru import logger

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


def _with_models(tmp_path, monkeypatch, entries, *, schema: int = 3):
    """把给定模型写进一个隔离的配置分层，并把注册表指过去。"""
    system = tmp_path / "system"
    for level, version in entries:
        _write(system, level, version, schema=schema)
    resolver = ConfigResolver(
        system_dir=system, local_dir=tmp_path / "local", dev_mode=False)
    monkeypatch.setattr(registry, "get_resolver", lambda: resolver)
    registry.invalidate_model_registry()
    return resolver


def test_old_schema_model_is_ignored_with_an_actionable_log(
    tmp_path, monkeypatch,
) -> None:
    """旧格式模型不能凭空消失：界面只会说「不可用」，日志得说清为什么。"""
    _with_models(tmp_path, monkeypatch, [(110, 1)], schema=2)
    # 项目用 loguru，不进 caplog，得自己挂一个 sink。
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="WARNING")
    try:
        assert registry.list_graduation_models() == ()
        text = "".join(messages)
        assert "schema" in text
        assert "重新导入" in text
    finally:
        logger.remove(sink_id)
        registry.invalidate_model_registry()


def test_missing_model_reason_points_at_the_world_level(
    tmp_path, monkeypatch,
) -> None:
    """世界等级低于所有模型时，要说清是等级不够，而不是让人去翻流派模型。"""
    _with_models(tmp_path, monkeypatch, [(110, 1)])
    try:
        reason = registry.describe_missing_model("鸣金·虹", "基础方案", 105)
        assert "110" in reason and "105" in reason
        assert "世界等级" in reason
    finally:
        registry.invalidate_model_registry()


def test_missing_model_reason_separates_no_model_from_wrong_name(
    tmp_path, monkeypatch,
) -> None:
    _with_models(tmp_path, monkeypatch, [(110, 1)])
    try:
        assert "没有名为" in registry.describe_missing_model(
            "鸣金·虹", "竞速轴", 115)
        assert "还没有任何毕业率模型" in registry.describe_missing_model(
            "破竹·风", "基础方案", 115)
    finally:
        registry.invalidate_model_registry()
