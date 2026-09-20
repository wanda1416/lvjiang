"""系统工作流 Profile 定义注册表的契约测试。"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from lvjiang.workflows.grammar import parse_file
from tests.workflows.conftest import make_engine

_ROOT = Path(__file__).parents[2]
_WORKFLOWS = _ROOT / "config" / "system" / "workflows"
_REGISTRY = _WORKFLOWS / "subcall" / "profile_registry.wf"


@pytest.fixture
def isolated_profile_config(tmp_path: Path, monkeypatch) -> Iterator[Path]:
    """让声明测试只写临时 profile.yaml，不接触用户配置。"""
    import lvjiang.core.profile.schema as profile_schema

    profile_path = tmp_path / "session" / "profile.yaml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(profile_schema, "_PROFILE_PATH", profile_path)
    profile_schema._config = None
    try:
        yield profile_path
    finally:
        profile_schema._config = None


def _declare(keys: list[str]) -> int:
    program = parse_file(_REGISTRY)
    engine = make_engine()
    engine._procs = dict(program.procs)
    result, _output = engine._run_proc(program.procs["declare_profiles"], [keys])
    return result


def test_registry_has_one_public_declaration_procedure() -> None:
    program = parse_file(_REGISTRY)

    assert set(program.procs) == {"declare_profiles"}


def test_registry_declares_multiple_models(isolated_profile_config: Path) -> None:
    from lvjiang.core.profile.models import QuotaKeyDef
    from lvjiang.core.profile.schema import get_profile_config

    assert _declare(["tongbao", "bugan_of_week", "tili"]) == 0

    config = get_profile_config()
    assert config.get_model_type("tongbao") == "stock"
    assert config.get_model_type("bugan_of_week") == "quota"
    assert config.get_model_type("tili") == "regen"
    quota_def = config.get_key("bugan_of_week")
    assert isinstance(quota_def, QuotaKeyDef)
    assert quota_def.period == "week"
    saved = yaml.safe_load(isolated_profile_config.read_text(encoding="utf-8"))
    quota = next(item for item in saved["quota"] if item["key"] == "bugan_of_week")
    regen = next(item for item in saved["regen"] if item["key"] == "tili")
    assert quota["cap"] == 23000
    assert regen["cap"] == 2500
    assert regen["regen_amount"] == 450


def test_registry_validates_all_keys_before_writing(
    isolated_profile_config: Path,
) -> None:
    from lvjiang.core.profile.schema import get_profile_config

    assert _declare(["tongbao", "not_registered"]) == -1
    assert get_profile_config().get_key("tongbao") is None


def test_registry_keeps_existing_definition_but_rejects_model_conflict(
    isolated_profile_config: Path,
) -> None:
    import lvjiang.core.profile.schema as profile_schema

    isolated_profile_config.write_text(
        yaml.safe_dump(
            {"quota": [{"key": "tongbao", "label": "已有冲突定义"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    profile_schema._config = None

    assert _declare(["tongbao"]) == -1
    assert profile_schema.get_profile_config().get_model_type("tongbao") == "quota"


def test_profile_consumers_use_the_central_registry() -> None:
    dependencies = {
        "scan_wallet.wf": [
            "tongbao", "bugan", "baoqian", "changmingyu", "bugan_of_week",
        ],
        "purchase_bugan.wf": [
            "bugan_of_week", "nn_bugan_of_week", "changmingyu", "tili",
        ],
        "purchase_niaoniao.wf": ["niaoniao_of_week", "changmingyu"],
        "purchase_xinfa.wf": ["xinfa_of_week"],
        "daily_jianghu.wf": ["haoling_of_week"],
        "weekly_huaruizhi.wf": ["huaruizhi_of_week"],
    }

    for filename, keys in dependencies.items():
        source = (_WORKFLOWS / filename).read_text(encoding="utf-8")
        rendered = ", ".join(f'"{key}"' for key in keys)
        declaration = f"declare_profiles([{rendered}])"
        assert 'import "subcall/profile_registry.wf"' in source
        assert declaration in source
        assert "profile_declare(" not in source
        profile_operations = [
            source.find(f"profile_{operation}(")
            for operation in ("get", "set", "inc", "observe", "model", "all")
        ]
        operation_indexes = [index for index in profile_operations if index >= 0]
        if operation_indexes:
            assert source.index(declaration) < min(operation_indexes)

    huaruizhi = (_WORKFLOWS / "weekly_huaruizhi.wf").read_text(encoding="utf-8")
    assert huaruizhi.count('declare_profiles(["huaruizhi_of_week"])') == 2

    business_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _WORKFLOWS.rglob("*.wf")
        if path != _REGISTRY
    )
    assert "profile_declare(" not in business_sources
    assert "check_profile(" not in business_sources
